"""One grooming run: the model reads a task and the code, then proposes.

The run is a short loop over the Messages API. Each round the model may
call the read-only tools (list, read and search files in the fleet's
repositories); it ends by calling ``propose`` once, and that call's input
is the proposal. The loop is this module's own, so it can hold every cap:

- **Rounds.** At most ``GROOMER_MAX_ROUNDS``. The last round tells the
  model to propose; a run that still has no proposal after it fails.
- **Tokens.** At most ``GROOMER_MAX_INPUT_TOKENS`` of input across the run;
  once that is close, each round from then on tells the model to propose.
  ``GROOMER_MAX_OUTPUT_TOKENS`` caps each reply; a reply cut off at it fails
  the run rather than proposing half a proposal. A proposal for a task that
  spans the fleet runs to a few thousand tokens, hence the default.
- **Money.** Before every request, the month's spend so far plus this run's
  cost must be under ``GROOMER_MONTHLY_CAP_USD``. Past it, the run stops
  with :class:`BudgetSpent` and nothing is proposed.

The propose call is asked for in the conversation, not forced with
``tool_choice``: the current models (Sonnet 5.5, Opus 5.5) answer a forced
``tool_choice`` with a 400. Leaving ``tool_choice`` alone also keeps the
cached prefix the same on every round.

The fixed prompt and the tool definitions are marked for caching, so every
round after the first reads them from the cache at a tenth of the price or
less.

A request that fails transiently (429, 5xx, a timeout) is tried again a
couple of times inside the run. A run that fails is not: it ends, and the
task waits for its next change.

  ANTHROPIC_API_KEY          the key
  GROOMER_MODEL              the model; default claude-sonnet-5-5
  GROOMER_MAX_ROUNDS         default 8
  GROOMER_MAX_INPUT_TOKENS   default 150000
  GROOMER_MAX_OUTPUT_TOKENS  default 8192
  GROOMER_MONTHLY_CAP_USD    default 20
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import httpx
from mini_app_polis import logger as logger_mod
from mini_app_polis.api.contract import GROOMING_MAX_CHILDREN, GroomingTask

from . import pricing
from .prompt import executors, system_prompt, task_message
from .repos import RepoError, Repos

log = logger_mod.get_logger()

API_URL = "https://api.anthropic.com/v1/messages"
COUNT_URL = "https://api.anthropic.com/v1/messages/count_tokens"
API_VERSION = "2023-06-01"
TIMEOUT_SECONDS = 120.0
TRANSIENT = frozenset({408, 429, 500, 502, 503, 504, 529})
ATTEMPTS = 3

DEFAULT_MODEL = "claude-sonnet-5-5"

#: When this share of the input cap is used, the next round must propose.
INPUT_HEADROOM = 0.8

PROPOSE = "propose"

#: Added to the last user turn of a round that must propose.
PROPOSE_NOW = (
    "This is the last round: call the propose tool now with what you have, "
    "and ask about anything unsettled in questions."
)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "list_files",
        "description": (
            "List files and directories in a fleet repository, two levels "
            "under path (the root when path is empty)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string", "description": "A name from the registry."},
                "path": {
                    "type": "string",
                    "description": "A directory; empty for the root.",
                },
            },
            "required": ["repo"],
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from a fleet repository, up to 400 numbered lines "
            "from start_line."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "path": {"type": "string"},
                "start_line": {"type": "integer", "minimum": 1},
            },
            "required": ["repo", "path"],
        },
    },
    {
        "name": "search",
        "description": (
            "Search a fleet repository's text files for a regular expression "
            "(case-insensitive), optionally under a path. Returns up to 40 "
            "matching lines with file and line number."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "repo": {"type": "string"},
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["repo", "pattern"],
        },
    },
    {
        "name": PROPOSE,
        "description": (
            "Propose what the task should say. Call exactly once, last. Leave "
            "out any part you could not settle, and ask about it in questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "done_when": {"type": "string"},
                "where": {"type": "string"},
                "constraints": {"type": "string"},
                "effort": {"type": "string", "enum": ["S", "M", "L"]},
                "blocked_by": {"type": "string"},
                "body": {"type": "string"},
                "executor": {"type": "string", "enum": list(executors())},
                "questions": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["title", "questions"],
        },
        # Cache everything up to and including the tools.
        "cache_control": {"type": "ephemeral"},
    },
]


#: One child task in a split, as the propose tool takes it.
CHILD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "done_when": {"type": "string"},
        "where": {"type": "string"},
        "constraints": {"type": "string"},
        "effort": {"type": "string", "enum": ["S", "M", "L"]},
        "executor": {"type": "string", "enum": list(executors())},
        "blocked_by_siblings": {
            "type": "array",
            "items": {"type": "integer", "minimum": 0},
            "description": "Earlier children this one waits for, by position from 0.",
        },
        "blocked_by": {"type": "string"},
        "body": {"type": "string"},
    },
    "required": ["title", "done_when", "where", "constraints", "effort", "body"],
}


def tools(*, split: bool = False) -> list[dict[str, Any]]:
    """The tools a run is given; ``split`` lets the propose call carry children."""
    if not split:
        return TOOLS
    *others, propose = TOOLS
    schema = propose["input_schema"]
    return [
        *others,
        {
            **propose,
            "input_schema": {
                **schema,
                "properties": {
                    **schema["properties"],
                    "children": {
                        "type": "array",
                        "items": CHILD_SCHEMA,
                        "maxItems": GROOMING_MAX_CHILDREN,
                    },
                },
            },
        },
    ]


class GroomingFailed(RuntimeError):
    """The run could not finish: the API refused, or the reply was unusable.

    ``cost_usd`` and ``usage`` say what the run had spent by then, which is
    still spent and still recorded.
    """

    def __init__(
        self, message: str, *, cost_usd: float = 0.0, usage: Any = None
    ) -> None:
        super().__init__(message)
        self.cost_usd = cost_usd
        self.usage = usage


class BudgetSpent(GroomingFailed):
    """The month's grooming budget is spent. Nothing more was asked."""


@dataclass(frozen=True)
class Limits:
    """What one run may spend: the model, rounds, tokens, and the month's cap."""

    model: str = DEFAULT_MODEL
    max_rounds: int = 8
    max_input_tokens: int = 150_000
    max_output_tokens: int = 8192
    monthly_cap_usd: float = 20.0
    #: Whether a run may split a task into children (``GROOMER_SPLIT``).
    split: bool = False

    @classmethod
    def from_env(cls) -> Limits:
        """The limits from ``GROOMER_*`` settings, each defaulting when unset."""

        def number(name: str, default: float) -> float:
            raw = (os.environ.get(name) or "").strip()
            return float(raw) if raw else default

        return cls(
            model=(os.environ.get("GROOMER_MODEL") or "").strip() or DEFAULT_MODEL,
            max_rounds=int(number("GROOMER_MAX_ROUNDS", 8)),
            max_input_tokens=int(number("GROOMER_MAX_INPUT_TOKENS", 150_000)),
            max_output_tokens=int(number("GROOMER_MAX_OUTPUT_TOKENS", 8192)),
            monthly_cap_usd=number("GROOMER_MONTHLY_CAP_USD", 20.0),
            split=(os.environ.get("GROOMER_SPLIT") or "").strip().lower() == "true",
        )


@dataclass
class RunResult:
    """What a run produced and what it cost."""

    proposal: dict[str, Any]
    model: str
    usage: pricing.Usage = field(default_factory=pricing.Usage)
    cost_usd: float = 0.0
    rounds: int = 0
    tool_calls: list[str] = field(default_factory=list)


def _headers() -> dict[str, str]:
    key = (os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not key:
        raise GroomingFailed("ANTHROPIC_API_KEY is not set")
    return {
        "x-api-key": key,
        "anthropic-version": API_VERSION,
        "content-type": "application/json",
    }


def request_body(
    task: GroomingTask,
    registry: Mapping[str, str],
    board: Sequence[str],
    limits: Limits,
) -> dict[str, Any]:
    """The first request of a run: what the dry run counts and the run sends."""
    return {
        "model": limits.model,
        "max_tokens": limits.max_output_tokens,
        "system": [
            {
                "type": "text",
                "text": system_prompt(registry, board, split=limits.split),
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "tools": tools(split=limits.split),
        "messages": [{"role": "user", "content": task_message(task)}],
    }


def _post(http: httpx.Client, url: str, body: dict[str, Any]) -> dict[str, Any]:
    """One API request, tried again on a transient failure."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            response = http.post(url, headers=_headers(), json=body)
        except httpx.TransportError as exc:
            if attempt == ATTEMPTS:
                raise GroomingFailed(
                    f"the Messages API was unreachable: {exc}"
                ) from exc
        else:
            if response.status_code == 200:
                return response.json()
            if response.status_code not in TRANSIENT or attempt == ATTEMPTS:
                raise GroomingFailed(
                    f"the Messages API answered {response.status_code}: "
                    f"{response.text[:300]}"
                )
        time.sleep(2**attempt)
    raise GroomingFailed("unreachable")  # pragma: no cover


def _new_client() -> httpx.Client:
    """The client a run's requests go through, each one sent by :func:`_post`."""
    # no-retry: this only builds the client; it makes no request. Every
    # request is sent by _post, which tries a transient failure again.
    return httpx.Client(timeout=TIMEOUT_SECONDS)


def count_tokens(body: dict[str, Any], *, http: httpx.Client | None = None) -> int:
    """The first request's input tokens, from the free counting endpoint."""
    client = http or _new_client()
    counted = {
        k: v for k, v in body.items() if k in {"model", "system", "tools", "messages"}
    }
    return int(_post(client, COUNT_URL, counted).get("input_tokens") or 0)


def _usage(reply: Mapping[str, Any]) -> pricing.Usage:
    usage = reply.get("usage") or {}
    return pricing.Usage(
        input_tokens=int(usage.get("input_tokens") or 0),
        output_tokens=int(usage.get("output_tokens") or 0),
        cache_creation_input_tokens=int(usage.get("cache_creation_input_tokens") or 0),
        cache_read_input_tokens=int(usage.get("cache_read_input_tokens") or 0),
    )


def _ask_for_proposal(messages: list[dict[str, Any]]) -> None:
    """Tell the model, in the last user turn, that this round must propose."""
    turn = messages[-1]
    content = turn["content"]
    if isinstance(content, str):
        turn["content"] = f"{content}\n\n{PROPOSE_NOW}"
    else:
        turn["content"] = [*content, {"type": "text", "text": PROPOSE_NOW}]


def _run_tool(repos: Repos, name: str, args: Mapping[str, Any]) -> tuple[str, bool]:
    """A tool's result text, and whether it is an error."""
    try:
        if name == "list_files":
            return repos.list_files(
                str(args["repo"]), str(args.get("path") or "")
            ), False
        if name == "read_file":
            return (
                repos.read_file(
                    str(args["repo"]),
                    str(args["path"]),
                    int(args.get("start_line") or 1),
                ),
                False,
            )
        if name == "search":
            return (
                repos.search(
                    str(args["repo"]), str(args["pattern"]), str(args.get("path") or "")
                ),
                False,
            )
        return f"There is no tool called {name}.", True
    except RepoError as exc:
        return str(exc), True
    except (KeyError, ValueError, TypeError) as exc:
        return f"Bad arguments for {name}: {exc}", True


def groom(
    task: GroomingTask,
    *,
    registry: Mapping[str, str],
    board: Sequence[str],
    repos: Repos,
    spent_this_month: Callable[[], float],
    limits: Limits | None = None,
    http: httpx.Client | None = None,
) -> RunResult:
    """Run the model over one task until it proposes. Raises on failure.

    A failure carries what the run had spent (``GroomingFailed.cost_usd``).
    """
    result = RunResult(proposal={}, model=(limits or Limits.from_env()).model)
    try:
        return _loop(
            task,
            result,
            registry=registry,
            board=board,
            repos=repos,
            spent_this_month=spent_this_month,
            limits=limits or Limits.from_env(),
            http=http,
        )
    except GroomingFailed as exc:
        exc.cost_usd, exc.usage = result.cost_usd, result.usage
        raise


def _loop(
    task: GroomingTask,
    result: RunResult,
    *,
    registry: Mapping[str, str],
    board: Sequence[str],
    repos: Repos,
    spent_this_month: Callable[[], float],
    limits: Limits,
    http: httpx.Client | None,
) -> RunResult:
    pricing.price_of(limits.model)  # refuse a model that cannot be capped
    client = http or _new_client()
    body = request_body(task, registry, board, limits)
    month = spent_this_month()

    for round_number in range(1, limits.max_rounds + 1):
        if month + result.cost_usd >= limits.monthly_cap_usd:
            raise BudgetSpent(
                f"grooming has spent ${month + result.cost_usd:.2f} of its "
                f"${limits.monthly_cap_usd:.2f} this month"
            )
        last = round_number == limits.max_rounds or (
            result.usage.prompt_tokens >= limits.max_input_tokens * INPUT_HEADROOM
        )
        if last:
            _ask_for_proposal(body["messages"])

        reply = _post(client, API_URL, body)
        usage = _usage(reply)
        result.usage = result.usage + usage
        result.cost_usd += pricing.cost(limits.model, usage)
        result.rounds = round_number

        if reply.get("stop_reason") == "max_tokens":
            # A reply cut off mid-call carries a partial proposal at best.
            raise GroomingFailed(
                f"the reply stopped at max_tokens={limits.max_output_tokens}; "
                "raise GROOMER_MAX_OUTPUT_TOKENS"
            )

        content = reply.get("content") or []
        uses = [
            c for c in content if isinstance(c, dict) and c.get("type") == "tool_use"
        ]
        for use in uses:
            if use.get("name") == PROPOSE:
                result.proposal = dict(use.get("input") or {})
                log.info(
                    "groom: %s proposed after %d round(s), $%.4f",
                    task.task_gid,
                    result.rounds,
                    result.cost_usd,
                )
                return result

        body["messages"].append({"role": "assistant", "content": content})
        if not uses:
            body["messages"].append(
                {"role": "user", "content": "Call the propose tool now."}
            )
            continue

        results = []
        for use in uses:
            name = str(use.get("name"))
            text, is_error = _run_tool(repos, name, use.get("input") or {})
            result.tool_calls.append(f"{name}({json.dumps(use.get('input'))[:120]})")
            results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": use.get("id"),
                    "content": text,
                    "is_error": is_error,
                }
            )
        body["messages"].append({"role": "user", "content": results})

    raise GroomingFailed(
        f"no proposal after {limits.max_rounds} rounds (${result.cost_usd:.4f})"
    )
