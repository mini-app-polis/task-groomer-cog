"""The Lambda entrypoint: one grooming job in, one proposal out.

task-groomer-cog runs on Lambda behind its own queue, declared in
mini-app-polis/infra.
The API is the only producer: task-manager-cog asks it to groom a task
(``POST /v1/tasks/groomings/jobs``), and it enqueues one message:

    {"type": "task.groom", "version": 1, "payload": <GroomingJobRequest>}

A job is one paid run (agent.py). Its cost is recorded with the API
whatever the outcome, and its proposal goes back through the API
(``POST /v1/tasks/groomings/proposals``) to task-manager-cog, which applies
it. This cog never writes to the board.

**No automatic retries.** A redelivered job would pay for the run again, so
the queue delivers each message once (``max_receive_count = 1`` in
mini-app-polis/infra) and a job that fails is reported and dropped. The
task is groomed again when it next changes.

**One paid run per job, even when SQS delivers it twice.** Before the model
is called, the run claims the task and the fingerprint of the content it was
sent for (``POST /v1/tasks/groomings/claims``). The API's unique key on that
pair means one delivery wins; another, on this container or another, finds
the claim held and is acknowledged with nothing paid, recorded or proposed.
A run that fails gives its claim back
(``POST /v1/tasks/groomings/claims/release``), so the next send of the same
content can claim it; a run that succeeds keeps it. If the claim cannot be
asked for at all, the job is reported and dropped, never run unclaimed.

**A run stopped by the budget is sent again.** The month's spend is checked
before every request, the first included, so with the cap spent the run
makes no model request. A run stopped that way reports the stop for the task
and its content's fingerprint (``POST /v1/tasks/groomings/budget-spent``)
before it gives its claim back, and the API has the job sent again once
there is budget, without waiting for the task to change. If the report
fails it is logged and the run ends as it would have. Any other failed run
is not reported this way and is not sent again until the task changes.

**An unrecognised message is a producer bug.** It is reported once and
lands in the dead-letter queue.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any, Protocol

from mini_app_polis import load_secrets, timing
from mini_app_polis import logger as logger_mod
from mini_app_polis.api import KaianoApiClient
from mini_app_polis.api.contract import (
    GroomingBudgetSpentMarked,
    GroomingBudgetSpentRequest,
    GroomingClaimReleased,
    GroomingClaimRequest,
    GroomingClaimResult,
    GroomingJobRequest,
    GroomingProposal,
    GroomingProposalAccepted,
    GroomingRunCreate,
    GroomingRunRecorded,
    GroomingSpend,
    TaskGroomMessage,
)
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
from .agent import BudgetSpent, GroomingFailed, Limits, groom
from .proposal import build
from .registry import load_registry
from .repos import Repos

log = logger_mod.get_logger()

REPO = "task-groomer-cog"
FLOW_NAME = "groom-task"

#: This cog's name to the API, which picks its key.
MACHINE_NAME = "task-groomer-cog"

#: How long before the function's timeout a run is stopped, leaving room for
#: the run report and the handler's return.
DEADLINE_MARGIN_SECONDS = 20

init_sentry(REPO)


class GroomingApi(Protocol):
    """The API calls a run makes: the API, or a fake in a test."""

    def claim_grooming(self, payload: GroomingClaimRequest) -> GroomingClaimResult:
        """Claim one job before the model is called; false when already held."""
        ...

    def release_grooming_claim(
        self, payload: GroomingClaimRequest
    ) -> GroomingClaimReleased:
        """Give a claim back after a run that failed."""
        ...

    def mark_grooming_budget_spent(
        self, payload: GroomingBudgetSpentRequest
    ) -> GroomingBudgetSpentMarked:
        """Report a run stopped by the budget, so the job is sent again."""
        ...

    def grooming_spend(self) -> GroomingSpend:
        """This month's paid grooming so far, for the cap."""
        ...

    def record_grooming_run(self, payload: GroomingRunCreate) -> GroomingRunRecorded:
        """Record one paid run and its cost."""
        ...

    def submit_grooming_proposal(
        self, payload: GroomingProposal
    ) -> GroomingProposalAccepted:
        """Hand a proposal back for the task manager to apply."""
        ...


def _api() -> GroomingApi:
    return KaianoApiClient.from_env(MACHINE_NAME)


def parse_message(body: str) -> GroomingJobRequest:
    """The job a message carries. Raises UnprocessableMessage otherwise."""
    return parse_queue_message(body, TaskGroomMessage).payload


def groom_task(
    job: GroomingJobRequest,
    *,
    run_id: str,
    api_factory: Callable[[], GroomingApi] = _api,
    registry_loader: Callable[[], Mapping[str, str]] = load_registry,
    repos_factory: Callable[[Mapping[str, str]], Repos] = Repos,
    groom_fn: Callable[..., Any] = groom,
) -> None:
    """Groom one task and hand the proposal back. Never redelivered.

    The job is claimed first, so a duplicate delivery pays for nothing, and
    the claim is given back if the run fails.
    """
    task = job.task
    label = task.task_id or task.task_gid
    limits = Limits.from_env()
    with run_report(FLOW_NAME, repo=REPO, run_id=run_id, version=__version__) as report:
        api = api_factory()
        claim = GroomingClaimRequest(
            task_gid=task.task_gid, fingerprint=task.fingerprint
        )
        try:
            claimed = api.claim_grooming(claim).claimed
        except Exception as exc:  # noqa: BLE001 — never groom unclaimed
            report.issue("grooming claim failed", label, detail=str(exc))
            return
        if not claimed:
            report.note("already claimed: a duplicate delivery", label)
            return

        try:
            _groom_claimed(
                api,
                job,
                claim,
                report=report,
                label=label,
                limits=limits,
                registry_loader=registry_loader,
                repos_factory=repos_factory,
                groom_fn=groom_fn,
            )
        except BaseException:
            _release(api, claim)
            raise


def _groom_claimed(
    api: GroomingApi,
    job: GroomingJobRequest,
    claim: GroomingClaimRequest,
    *,
    report: Any,
    label: str,
    limits: Limits,
    registry_loader: Callable[[], Mapping[str, str]],
    repos_factory: Callable[[Mapping[str, str]], Repos],
    groom_fn: Callable[..., Any],
) -> None:
    """The paid run, once the job is claimed. A run that fails releases it."""
    task = job.task
    registry = registry_loader()
    try:
        result = groom_fn(
            task,
            registry=registry,
            board=job.board,
            repos=repos_factory(registry),
            spent_this_month=lambda: api.grooming_spend().cost_usd,
            limits=limits,
        )
    except BudgetSpent as exc:
        _record(api, task.task_gid, limits.model, exc.usage, exc.cost_usd)
        _mark_budget_spent(api, claim)
        _release(api, claim)
        report.issue("grooming budget spent", label, detail=str(exc))
        return
    except GroomingFailed as exc:
        _record(api, task.task_gid, limits.model, exc.usage, exc.cost_usd)
        _release(api, claim)
        report.issue("grooming run failed", label, detail=str(exc))
        return

    _record(api, task.task_gid, result.model, result.usage, result.cost_usd)
    proposal = build(
        task,
        result.proposal,
        model=result.model,
        cost_usd=result.cost_usd,
        split=limits.split,
    )
    api.submit_grooming_proposal(proposal)

    report.count("rounds", result.rounds)
    report.count("cost_usd", f"{result.cost_usd:.4f}")
    report.count("questions", len(proposal.questions))
    report.count("children", len(proposal.children))
    report.updated("grooming proposal", f"{label} · {proposal.title}")


def _release(api: GroomingApi, claim: GroomingClaimRequest) -> None:
    """Give the claim back. A failure here is logged, never raised over the run's."""
    try:
        api.release_grooming_claim(claim)
    except Exception:  # noqa: BLE001 — the run's own outcome is what matters
        log.exception("worker: could not release the claim on %s", claim.task_gid)


def _mark_budget_spent(api: GroomingApi, claim: GroomingClaimRequest) -> None:
    """Report a stop by the budget. A failure here is logged, never raised."""
    try:
        api.mark_grooming_budget_spent(
            GroomingBudgetSpentRequest(
                task_gid=claim.task_gid, fingerprint=claim.fingerprint
            )
        )
    except Exception:  # noqa: BLE001 — the run's own outcome is what matters
        log.exception("worker: could not report the budget stop on %s", claim.task_gid)


def _record(
    api: GroomingApi, task_gid: str, model: str, usage: Any, cost_usd: float
) -> None:
    """Record a run's cost. A run that cost nothing made no request."""
    if not cost_usd:
        return
    api.record_grooming_run(
        GroomingRunCreate(
            task_gid=task_gid,
            model=model,
            input_tokens=getattr(usage, "prompt_tokens", 0),
            output_tokens=getattr(usage, "output_tokens", 0),
            cost_usd=round(cost_usd, 6),
        )
    )


def process_message(body: str, *, run_id: str, **kwargs: Any) -> None:
    """Handle one message."""
    job = parse_message(body)
    log.info("worker: run %s grooms task %s", run_id, job.task.task_gid)
    groom_task(job, run_id=run_id, **kwargs)


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

    def handle(record: dict[str, Any]) -> None:
        message_id = str(record.get("messageId") or "")
        attempt = (record.get("attributes") or {}).get("ApproximateReceiveCount", "?")
        with timing.invocation(
            cog="task-groomer", source="grooming", attempt=attempt
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
