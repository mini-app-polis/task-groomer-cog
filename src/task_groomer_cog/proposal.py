"""Turning the model's propose call into the contract's proposal.

The model's input is trusted for nothing: every field is checked and cut to
the contract's limits, an effort off the scale is dropped (the task then
fails the linter on Effort, which is right), and an empty answer is left
out rather than sent as an empty string.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from mini_app_polis.api.contract import GroomingProposal, GroomingTask

_LIMITS = {
    "title": 300,
    "done_when": 5000,
    "where": 1000,
    "constraints": 5000,
    "blocked_by": 500,
    "body": 20_000,
}
MAX_QUESTIONS = 10
MAX_QUESTION_CHARS = 1000


def _text(value: Any, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1].rstrip() + "…"


def build(
    task: GroomingTask,
    proposed: Mapping[str, Any],
    *,
    model: str,
    cost_usd: float,
) -> GroomingProposal:
    """The proposal for ``task`` from the model's ``proposed`` input."""
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
        questions=questions,
        model=model,
        cost_usd=round(cost_usd, 6),
    )
