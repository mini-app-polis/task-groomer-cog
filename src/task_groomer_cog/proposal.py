"""Turning the model's propose call into the contract's proposal.

The model's input is trusted for nothing: every field is checked and cut to
the contract's limits, an effort off the scale is dropped (the task then
fails the linter on Effort, which is right), and an empty answer is left
out rather than sent as an empty string.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import (
    GROOMING_MAX_CHILDREN,
    GroomingChild,
    GroomingProposal,
    GroomingTask,
)
from pydantic import ValidationError

from .prompt import executor_of, executors

_LIMITS = {
    "title": 300,
    "done_when": 5000,
    "where": 1000,
    "constraints": 5000,
    "blocked_by": 500,
    "body": 20_000,
}
log = logger_mod.get_logger()

MAX_QUESTIONS = 10
MAX_QUESTION_CHARS = 1000


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def _children(proposed: Any) -> list[GroomingChild]:
    """The split the model proposed, whole or not at all.

    Children refer to each other by position, so one dropped child would
    shift the rest: a split with any child that does not hold is dropped
    entirely, and the task is proposed without it.
    """
    if not isinstance(proposed, list) or not proposed:
        return []
    if len(proposed) > GROOMING_MAX_CHILDREN:
        log.warning(
            "proposal: %d children is over the cap; split dropped", len(proposed)
        )
        return []
    children = []
    for raw in proposed:
        if not isinstance(raw, Mapping):
            log.warning("proposal: a child is not an object; split dropped")
            return []
        siblings = raw.get("blocked_by_siblings")
        executor = raw.get("executor")
        try:
            children.append(
                GroomingChild.model_validate(
                    {
                        "title": _text(raw.get("title"), _LIMITS["title"]) or "",
                        "done_when": _text(raw.get("done_when"), _LIMITS["done_when"])
                        or "",
                        "where": _text(raw.get("where"), _LIMITS["where"]) or "",
                        "constraints": _text(
                            raw.get("constraints"), _LIMITS["constraints"]
                        )
                        or "",
                        "effort": raw.get("effort"),
                        "executor": executor if executor in executors() else None,
                        "blocked_by_siblings": siblings
                        if isinstance(siblings, list)
                        else [],
                        "blocked_by": _text(
                            raw.get("blocked_by"), _LIMITS["blocked_by"]
                        ),
                        "body": _text(raw.get("body"), _LIMITS["body"]) or "",
                    }
                )
            )
        except ValidationError as exc:
            log.warning("proposal: a child does not hold (%s); split dropped", exc)
            return []
    return children


def build(
    task: GroomingTask,
    proposed: Mapping[str, Any],
    *,
    model: str,
    cost_usd: float,
    split: bool = False,
) -> GroomingProposal:
    """The proposal for ``task`` from the model's ``proposed`` input.

    Children are taken only when ``split`` is on, and only whole: a split
    whose children wait on anything but an earlier sibling is dropped too.
    """
    fallback_title = task.name.split(" · ", 1)[-1]
    title = _text(proposed.get("title"), _LIMITS["title"]) or fallback_title
    effort = proposed.get("effort")
    questions_in = proposed.get("questions")
    questions = [
        q
        for q in (
            _text(x, MAX_QUESTION_CHARS)
            for x in (questions_in if isinstance(questions_in, list) else [])
        )
        if q
    ][:MAX_QUESTIONS]
    # A task already tagged keeps its tag: the groomer only names an
    # executor for a task that has none.
    executor = proposed.get("executor")
    if executor not in executors() or executor_of(task.tags) is not None:
        executor = None
    children = _children(proposed.get("children")) if split else []
    fields: dict[str, Any] = {
        "task_gid": task.task_gid,
        "fingerprint": task.fingerprint,
        "title": title[: _LIMITS["title"]],
        "model": model,
        "cost_usd": round(cost_usd, 6),
    }
    if children:
        try:
            # The task becomes their compound task: only its title and body.
            return GroomingProposal(
                **fields,
                body=_text(proposed.get("body"), _LIMITS["body"]),
                questions=questions,
                children=children,
            )
        except ValidationError as exc:
            log.warning("proposal: the split does not hold (%s); dropped", exc)
    return GroomingProposal(
        task_gid=task.task_gid,
        fingerprint=task.fingerprint,
        title=title[: _LIMITS["title"]],
        done_when=_text(proposed.get("done_when"), _LIMITS["done_when"]),
        where=_text(proposed.get("where"), _LIMITS["where"]),
        constraints=_text(proposed.get("constraints"), _LIMITS["constraints"]),
        effort=effort if effort in ("S", "M", "L") else None,
        blocked_by=_text(proposed.get("blocked_by"), _LIMITS["blocked_by"]),
        body=_text(proposed.get("body"), _LIMITS["body"]),
        executor=executor,
        questions=questions,
        model=model,
        cost_usd=round(cost_usd, 6),
    )
