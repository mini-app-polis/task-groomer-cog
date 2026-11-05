"""The Lambda entrypoint: one refining job in, one proposal out.

task-refiner-cog runs on Lambda behind its own queue, declared in
mini-app-polis/infra.
The API is the only producer: task-manager-cog asks it to refine a task
(``POST /v1/tasks/refinings/jobs``), and it enqueues one message:

    {"type": "task.refine", "version": 1, "payload": <RefiningJobRequest>}

A job is one paid run (agent.py). Its cost is recorded with the API
whatever the outcome, and its proposal goes back through the API
(``POST /v1/tasks/refinings/proposals``) to task-manager-cog, which applies
it. This cog never writes to the board.

**No automatic retries.** A redelivered job would pay for the run again, so
the queue delivers each message once (``max_receive_count = 1`` in
mini-app-polis/infra) and a job that fails is reported and dropped. The
task is refined again when it next changes.

**One paid run per job, even when SQS delivers it twice.** Before the model
is called, the run claims the task and the fingerprint of the content it was
sent for (``POST /v1/tasks/refinings/claims``). The API's unique key on that
pair means one delivery wins; another, on this container or another, finds
the claim held and is acknowledged with nothing paid, recorded or proposed.
A run that fails gives its claim back
(``POST /v1/tasks/refinings/claims/release``), so the next send of the same
content can claim it; a run that succeeds keeps it. If the claim cannot be
asked for at all, the job is reported and dropped, never run unclaimed.

**A run stopped by the budget is sent again.** The month's spend is checked
before every request, the first included, so with the cap spent the run
makes no model request. A run stopped that way reports the stop for the task
and its content's fingerprint (``POST /v1/tasks/refinings/budget-spent``)
before it gives its claim back, and the API has the job sent again once
there is budget, without waiting for the task to change. If the report
fails it is logged and the run ends as it would have. Any other failed run
is not reported this way and is not sent again until the task changes.

**An unrecognised message is a producer bug.** It is reported once and
lands in the dead-letter queue.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import (
    RefiningBudgetSpentMarked,
    RefiningBudgetSpentRequest,
    RefiningClaimReleased,
    RefiningClaimRequest,
    RefiningClaimResult,
    RefiningJobRequest,
    RefiningProposal,
    RefiningProposalAccepted,
    RefiningRunCreate,
    RefiningRunRecorded,
    RefiningSpend,
    TaskRefineMessage,
)
from mini_app_polis.environment import log_settings
from mini_app_polis.lambda_worker import (
    UnprocessableMessage,
    all_failed,
    deadline,
    process_records,
)
from mini_app_polis.lambda_worker import parse_message as parse_queue_message
from mini_app_polis.pipeline_status import post_run_finding, run_report
from mini_app_polis.sentry import init_sentry

from ._version import __version__
from .agent import BudgetSpent, Limits, RefiningFailed, refine
from .proposal import build
from .registry import DEFAULT_URL as DEFAULT_REGISTRY_URL
from .registry import load_registry
from .repos import Repos

log = logger_mod.get_logger()

REPO = "task-refiner-cog"
FLOW_NAME = "refine-task"

#: This cog's name to the API, which picks its key.
MACHINE_NAME = "task-refiner-cog"

#: How long before the function's timeout a run is stopped, leaving room for
#: the run report and the handler's return.
DEADLINE_MARGIN_SECONDS = 20

init_sentry(REPO)

#: Whether this container has logged its effective settings yet.
_settings_logged = False


class RefiningApi(Protocol):
    """The API calls a run makes: the API, or a fake in a test."""

    def claim_refining(self, payload: RefiningClaimRequest) -> RefiningClaimResult:
        """Claim one job before the model is called; false when already held."""
        ...

    def release_refining_claim(
        self, payload: RefiningClaimRequest
    ) -> RefiningClaimReleased:
        """Give a claim back after a run that failed."""
        ...

    def mark_refining_budget_spent(
        self, payload: RefiningBudgetSpentRequest
    ) -> RefiningBudgetSpentMarked:
        """Report a run stopped by the budget, so the job is sent again."""
        ...

    def refining_spend(self) -> RefiningSpend:
        """This month's paid refining so far, for the cap."""
        ...

    def record_refining_run(self, payload: RefiningRunCreate) -> RefiningRunRecorded:
        """Record one paid run and its cost."""
        ...

    def submit_refining_proposal(
        self, payload: RefiningProposal
    ) -> RefiningProposalAccepted:
        """Hand a proposal back for the task manager to apply."""
        ...


def _api() -> RefiningApi:
    return KaianoApiClient.from_env(MACHINE_NAME)


def parse_message(body: str) -> RefiningJobRequest:
    """The job a message carries. Raises UnprocessableMessage otherwise."""
    return parse_queue_message(body, TaskRefineMessage).payload


def refine_task(
    job: RefiningJobRequest,
    *,
    run_id: str,
    api_factory: Callable[[], RefiningApi] = _api,
    registry_loader: Callable[[], Mapping[str, str]] = load_registry,
    repos_factory: Callable[[Mapping[str, str]], Repos] = Repos,
    refine_fn: Callable[..., Any] = refine,
) -> None:
    """Refine one task and hand the proposal back. Never redelivered.

    The job is claimed first, so a duplicate delivery pays for nothing, and
    the claim is given back if the run fails.
    """
    task = job.task
    label = task.task_id or task.task_gid
    limits = Limits.from_env()
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
        api = api_factory()
        claim = RefiningClaimRequest(
            task_gid=task.task_gid, fingerprint=task.fingerprint
        )
        try:
            claimed = api.claim_refining(claim).claimed
        except Exception as exc:  # noqa: BLE001 — never refine unclaimed
            report.issue("refining claim failed", label, detail=str(exc))
            return
        if not claimed:
            report.note("already claimed: a duplicate delivery", label)
            return

        try:
            _refine_claimed(
                api,
                job,
                claim,
                report=report,
                label=label,
                limits=limits,
                registry_loader=registry_loader,
                repos_factory=repos_factory,
                refine_fn=refine_fn,
            )
        except BaseException:
            _release(api, claim)
            raise


def _refine_claimed(
    api: RefiningApi,
    job: RefiningJobRequest,
    claim: RefiningClaimRequest,
    *,
    report: Any,
    label: str,
    limits: Limits,
    registry_loader: Callable[[], Mapping[str, str]],
    repos_factory: Callable[[Mapping[str, str]], Repos],
    refine_fn: Callable[..., Any],
) -> None:
    """The paid run, once the job is claimed. A run that fails releases it."""
    task = job.task
    registry = registry_loader()
    try:
        result = refine_fn(
            task,
            registry=registry,
            board=job.board,
            repos=repos_factory(registry),
            spent_this_month=lambda: api.refining_spend().cost_usd,
            limits=limits,
        )
    except BudgetSpent as exc:
        _record(api, task.task_gid, limits.model, exc.usage, exc.cost_usd)
        _mark_budget_spent(api, claim)
        _release(api, claim)
        report.issue("refining budget spent", label, detail=str(exc))
        return
    except RefiningFailed as exc:
        _record(api, task.task_gid, limits.model, exc.usage, exc.cost_usd)
        _release(api, claim)
        report.issue("refining run failed", label, detail=str(exc))
        return

    _record(api, task.task_gid, result.model, result.usage, result.cost_usd)
    proposal = build(
        task,
        result.proposal,
        model=result.model,
        cost_usd=result.cost_usd,
        split=limits.split,
    )
    api.submit_refining_proposal(proposal)

    report.count("rounds", result.rounds)
    report.count("cost_usd", f"{result.cost_usd:.4f}")
    report.count("questions", len(proposal.questions))
    report.count("children", len(proposal.children))
    report.updated("refining proposal", f"{label} · {proposal.title}")


def _release(api: RefiningApi, claim: RefiningClaimRequest) -> None:
    """Give the claim back. A failure here is logged, never raised over the run's."""
    try:
        api.release_refining_claim(claim)
    except Exception:  # noqa: BLE001 — the run's own outcome is what matters
        log.exception("worker: could not release the claim on %s", claim.task_gid)


def _mark_budget_spent(api: RefiningApi, claim: RefiningClaimRequest) -> None:
    """Report a stop by the budget. A failure here is logged, never raised."""
    try:
        api.mark_refining_budget_spent(
            RefiningBudgetSpentRequest(
                task_gid=claim.task_gid, fingerprint=claim.fingerprint
            )
        )
    except Exception:  # noqa: BLE001 — the run's own outcome is what matters
        log.exception("worker: could not report the budget stop on %s", claim.task_gid)


def _record(
    api: RefiningApi, task_gid: str, model: str, usage: Any, cost_usd: float
) -> None:
    """Record a run's cost. A run that cost nothing made no request."""
    if not cost_usd:
        return
    api.record_refining_run(
        RefiningRunCreate(
            task_gid=task_gid,
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cost_usd=round(cost_usd, 6),
        )
    )


def effective_settings() -> dict[str, Any]:
    """Each setting this cog reads, as resolved with its default applied.

    Secrets are included so the report says whether each is set; the shared
    helper shows a value only for a name on ``SETTINGS_ALLOWLIST``.
    """
    limits = Limits.from_env()
    return {
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY"),
        "TASK_REFINER_COG_API_KEY": os.environ.get("TASK_REFINER_COG_API_KEY"),
        "GITHUB_TOKEN": os.environ.get("GITHUB_TOKEN"),
        "ECOSYSTEM_YAML_URL": (os.environ.get("ECOSYSTEM_YAML_URL") or "").strip()
        or DEFAULT_REGISTRY_URL,
        "REFINER_MODEL": limits.model,
        "REFINER_MAX_ROUNDS": limits.max_rounds,
        "REFINER_MAX_INPUT_TOKENS": limits.max_input_tokens,
        "REFINER_MAX_OUTPUT_TOKENS": limits.max_output_tokens,
        "REFINER_MONTHLY_CAP_USD": limits.monthly_cap_usd,
        "REFINER_SPLIT": limits.split,
    }


def _log_settings_once() -> None:
    """Log the effective settings on a container's first invocation only.

    Not at import: the settings reach the environment from Parameter Store
    in the handler, so only after the first load do they show as set.
    """
    global _settings_logged
    if _settings_logged:
        return
    _settings_logged = True
    try:
        log_settings(effective_settings())
    except Exception:  # noqa: BLE001 — the report is not the job
        log.exception("worker: could not log the effective settings")


def process_message(body: str, *, run_id: str, **kwargs: Any) -> None:
    """Handle one message."""
    job = parse_message(body)
    log.info("worker: run %s refines task %s", run_id, job.task.task_gid)
    refine_task(job, run_id=run_id, **kwargs)


def _report_unprocessable(exc: BaseException, run_id: str) -> None:
    try:
        post_run_finding(
            FLOW_NAME,
            "ERROR",
            f"an unprocessable message failed: {type(exc).__name__}: {exc}",
            repo=REPO,
            source="queue_consumer",
            run_id=run_id,
            version=__version__,
        )
    except Exception:  # noqa: BLE001 — the notification is not the job
        log.exception("worker: could not report the unprocessable message")


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run each record's job. Never raises.

    A record named in ``batchItemFailures`` comes back, and with one receive
    allowed it goes straight to the dead-letter queue: that is where a job
    that crashed outside the run's own handling ends, rather than paying
    again.
    """
    try:
        load_secrets(refresh=True)
    except Exception:  # noqa: BLE001 — nothing can run without its settings
        log.exception("worker: could not refresh settings from SSM")
        return all_failed(event)
    _log_settings_once()

    def handle(record: dict[str, Any]) -> None:
        message_id = str(record.get("messageId") or "")
        attempt = (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")
        with timing.invocation(
            cog="task-refiner", source="refining", attempt=attempt
        ) as timed:
            try:
                with deadline(context, margin_seconds=DEADLINE_MARGIN_SECONDS):
                    process_message(record.get("body") or "", run_id=message_id)
            except UnprocessableMessage:
                timed.label(outcome="unprocessable")
                raise
            except BaseException:
                timed.label(outcome="failed")
                raise
            timed.label(outcome="ok")

    def on_unprocessable(exc: UnprocessableMessage, record: dict[str, Any]) -> None:
        # Once, on the first receive: a duplicate delivery fails the same way,
        # and the dead-letter queue it lands in has an alarm of its own.
        attempt = str(
            (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")
        )
        if attempt in ("1", "?"):
            _report_unprocessable(exc, str(record.get("messageId") or ""))

    return process_records(event, handle, on_unprocessable=on_unprocessable)
