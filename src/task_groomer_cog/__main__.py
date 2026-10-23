"""Run the groomer by hand: estimate first, then groom one task for real.

    doppler run -- uv run python -m task_groomer_cog --dry-run --task <gid>
    doppler run -- uv run python -m task_groomer_cog --dry-run --board
    doppler run -- uv run python -m task_groomer_cog --task <gid>

The task is read from the board (``ASANA_ACCESS_TOKEN``,
``ASANA_INBOX_PROJECT_ID``), only read. ``--dry-run`` builds each task's
real first request and counts it with Anthropic's free token-counting
endpoint, then prices a short run and a long one: no model call, nothing
spent. ``--board`` estimates every open task in the grooming columns, which
is an upper bound, since the sweep only sends tasks that are not ready.

With ``GROOMER_SPLIT=true`` the estimate's long run allows for a split's
larger propose call.

Without ``--dry-run`` the one task is groomed for real: the run is paid,
and its proposal and cost are logged, with the children of a split listed
one per line. Nothing is written to the board,
and nothing is recorded with the API, so the run does not count against
the month's cap; it is yours to account for.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import GroomingTask
from mini_app_polis.asana import AsanaClient, AsanaTaskRecord

from . import pricing
from .agent import Limits, count_tokens, groom, request_body
from .proposal import build
from .registry import load_registry
from .repos import Repos

log = logger_mod.get_logger()

GROOM_SECTIONS = ("ideas", "refining", "blocked")
DO_NOT_WORK = "do not work"

#: The shape of a short run and of a long one, for the estimate: rounds,
#: fresh input each tool round adds, and output per round.
SHORT = (3, 3_000, 400)
LONG = (8, 6_000, 600)
#: The propose call's output, on top.
PROPOSE_OUTPUT = 1_500
#: A split's propose call, for the long run when splitting is on.
SPLIT_PROPOSE_OUTPUT = 5_000


@dataclass(frozen=True)
class Estimate:
    """A task's first request, and what a short run and a long one would cost."""

    first_request_tokens: int
    low_usd: float
    high_usd: float


def estimate(
    first: int, model: str, max_input: int, *, split: bool = False
) -> Estimate:
    """Price a short run and a long one from the first request's size.

    Round one writes the prompt to the cache; every later round reads it
    from the cache and adds what the tools returned since. With ``split``,
    the long run ends in a split's larger propose call.
    """

    def run(rounds: int, growth: int, output: int, propose: int) -> float:
        total = 0.0
        sent = 0
        for k in range(1, rounds + 1):
            fresh = (k - 1) * growth
            if sent + first + fresh > max_input:
                break
            sent += first + fresh
            usage = pricing.Usage(
                input_tokens=fresh,
                output_tokens=output + (propose if k == rounds else 0),
                cache_creation_input_tokens=first if k == 1 else 0,
                cache_read_input_tokens=first if k > 1 else 0,
            )
            total += pricing.cost(model, usage)
        return total

    long_propose = SPLIT_PROPOSE_OUTPUT if split else PROPOSE_OUTPUT
    return Estimate(first, run(*SHORT, PROPOSE_OUTPUT), run(*LONG, long_propose))


def as_grooming_task(task: AsanaTaskRecord) -> GroomingTask:
    """The task as a groomer is given it, from its record on the board."""
    content = f"{task.name}\n{task.notes}"
    return GroomingTask(
        task_gid=task.gid,
        task_id=task.name.split(" · ", 1)[0] if " · " in task.name else None,
        name=task.name,
        notes=task.notes,
        tags=list(task.tag_names),
        section=task.section_name,
        fingerprint=hashlib.sha256(content.encode()).hexdigest(),
    )


def main(argv: list[str] | None = None) -> int:
    """Estimate one task or the board, or groom one task for real."""
    parser = argparse.ArgumentParser(prog="python -m task_groomer_cog")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument("--task", help="the task's gid on the board")
    which.add_argument("--board", action="store_true", help="every open task")
    parser.add_argument(
        "--dry-run", action="store_true", help="estimate; spend nothing"
    )
    args = parser.parse_args(argv)
    if args.board and not args.dry_run:
        parser.error("--board is an estimate only; add --dry-run")

    project = (os.environ.get("ASANA_INBOX_PROJECT_ID") or "").strip()
    if not project:
        parser.error("ASANA_INBOX_PROJECT_ID is not set")
    tasks = [t for t in AsanaClient().list_project_tasks(project) if not t.completed]
    board = [t.name for t in tasks]
    if args.task:
        chosen = [t for t in tasks if t.gid == args.task]
        if not chosen:
            parser.error(f"no open task {args.task} on the board")
    else:
        chosen = [
            t
            for t in tasks
            if (t.section_name or "").lower() in GROOM_SECTIONS
            and DO_NOT_WORK not in t.name.lower()
        ]

    limits = Limits.from_env()
    registry = load_registry()

    if args.dry_run:
        low = high = 0.0
        for task in chosen:
            job = as_grooming_task(task)
            others = [n for n in board if n != task.name]
            first = count_tokens(request_body(job, registry, others, limits))
            guess = estimate(
                first, limits.model, limits.max_input_tokens, split=limits.split
            )
            low, high = low + guess.low_usd, high + guess.high_usd
            log.info(
                "%-70s  first request %6d tokens  $%.3f–$%.3f",
                task.name[:70],
                first,
                guess.low_usd,
                guess.high_usd,
            )
        log.info(
            "%d task(s) with %s: about $%.2f–$%.2f. Nothing was spent.",
            len(chosen),
            limits.model,
            low,
            high,
        )
        return 0

    (task,) = chosen
    job = as_grooming_task(task)
    result = groom(
        job,
        registry=registry,
        board=[n for n in board if n != task.name],
        repos=Repos(registry),
        spent_this_month=lambda: 0.0,
        limits=limits,
    )
    proposal = build(
        job,
        result.proposal,
        model=result.model,
        cost_usd=result.cost_usd,
        split=limits.split,
    )
    log.info("%s", json.dumps(proposal.model_dump(mode="json"), indent=2))
    for position, child in enumerate(proposal.children):
        waits = ", ".join(str(s) for s in child.blocked_by_siblings) or "none"
        log.info(
            "Child %d: %s (%s, effort %s, executor %s, waits on children %s)",
            position,
            child.title,
            child.where,
            child.effort,
            child.executor or "agent",
            waits,
        )
    log.info("Tools used: %s", ", ".join(result.tool_calls) or "none")
    log.info("%d round(s), %s, $%.4f", result.rounds, result.usage, result.cost_usd)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
