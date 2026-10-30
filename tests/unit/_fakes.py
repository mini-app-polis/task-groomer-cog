"""A scripted Messages API, for the agent and the worker."""

from __future__ import annotations

import json
from typing import Any

import httpx
from mini_app_polis.api.contract import GroomingTask

TASK = GroomingTask(
    task_gid="1219754951292362",
    task_id="TASK-38",
    name="TASK-38 · idea - dashboard for TV shows and movies",
    notes="Done when:\nWhere:\n",
    section="Ideas",
    fingerprint="d" * 64,
    problems=["Done when is blank"],
)

REGISTRY = {"infra": "Terraform for the fleet.", "website-astro-software": "A site."}


def usage(**overrides: int) -> dict[str, int]:
    base = {
        "input_tokens": 1000,
        "output_tokens": 200,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    base.update(overrides)
    return base


def tool_use(name: str, args: dict[str, Any], id_: str = "t1") -> dict[str, Any]:
    return {
        "content": [{"type": "tool_use", "id": id_, "name": name, "input": args}],
        "usage": usage(),
        "stop_reason": "tool_use",
    }


def text_reply(text: str) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": text}],
        "usage": usage(),
        "stop_reason": "end_turn",
    }


PROPOSAL_INPUT = {
    "title": "Dashboard for TV shows and movies",
    "where": "website-astro-software",
    "effort": "M",
    "questions": ["Which service holds the watch history?"],
}


class Script:
    """Answers each Messages request with the next reply, and keeps the bodies."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        reply = self.replies.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))
