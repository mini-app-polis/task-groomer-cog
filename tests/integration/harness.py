"""Fakes and event builders for the integration suite. See conftest.py.

Every external service is stubbed at the HTTP boundary with respx (TEST-007):
the Messages API, GitHub (repository archives), the ecosystem registry and
api-kaianolevine-com. Each fake keeps what it was sent, so a test can look
at what a run paid for and what it proposed.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

API_BASE = "https://api.groomer.test"
API_KEY = "task-groomer-cog-test-key"
REGISTRY_URL = "https://registry.test/ecosystem.yaml"
MESSAGES_URL = "https://api.anthropic.com/v1/messages"
GITHUB_ZIPBALL = "https://api.github.com/repos/mini-app-polis/{repo}/zipball/{branch}"

REGISTRY_YAML = """\
services:
  - id: infra
    description: Terraform for the fleet.
  - id: website-astro-software
    description: The software site.
"""

#: The repositories the fake GitHub serves, file path -> text, on dev.
REPOS: dict[str, dict[str, str]] = {
    "infra": {
        "README.md": "# infra\nTerraform for the fleet.\n",
        "cogs.hcl": 'module "deejay" {\n  source = "./modules/cog-worker"\n}\n',
    },
    "website-astro-software": {
        "README.md": "# website-astro-software\nThe software site.\n",
    },
}

TASK_GID = "1219754951292362"
FINGERPRINT = "d" * 64


def job_payload(**task_overrides: Any) -> dict[str, Any]:
    """A grooming job as the API enqueues it."""
    task = {
        "task_gid": TASK_GID,
        "task_id": "TASK-38",
        "name": "TASK-38 · idea - dashboard for TV shows and movies",
        "notes": "Done when:\nWhere:\n",
        "section": "Ideas",
        "fingerprint": FINGERPRINT,
        "problems": ["Done when is blank"],
    }
    task.update(task_overrides)
    return {"task": task, "board": ["TASK-9 · Discord commands"]}


def groom_body(**task_overrides: Any) -> str:
    return json.dumps(
        {"type": "task.groom", "version": 1, "payload": job_payload(**task_overrides)}
    )


def sqs_record(body: str, message_id: str, *, receive_count: int = 1) -> dict:
    """One record as the SQS event source mapping delivers it to Lambda."""
    return {
        "messageId": message_id,
        "receiptHandle": f"rh-{message_id}",
        "body": body,
        "attributes": {
            "ApproximateReceiveCount": str(receive_count),
            "SentTimestamp": "1759680000000",
            "SenderId": "AROAEXAMPLE:api-kaianolevine-com",
            "ApproximateFirstReceiveTimestamp": "1759680000001",
        },
        "messageAttributes": {},
        "md5OfBody": "ignored",
        "eventSource": "aws:sqs",
        "eventSourceARN": "arn:aws:sqs:us-east-1:000000000000:task-groomer-jobs",
        "awsRegion": "us-east-1",
    }


def sqs_event(*records: dict) -> dict:
    return {"Records": list(records)}


@dataclass
class LambdaContext:
    """Enough of the Lambda context for the deadline to arm."""

    remaining_ms: int = 900_000

    def get_remaining_time_in_millis(self) -> int:
        return self.remaining_ms


# ── the Messages API ─────────────────────────────────────────────────────


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


PROPOSAL = {
    "title": "Dashboard for TV shows and movies",
    "done_when": "A page lists what was watched this month.",
    "where": "website-astro-software",
    "constraints": "None",
    "effort": "M",
    "body": "Why: one place to see it.",
    "questions": ["Which service holds the watch history?"],
}


def propose(**overrides: Any) -> dict[str, Any]:
    return tool_use("propose", {**PROPOSAL, **overrides}, id_="p1")


@dataclass
class FakeModel:
    """The Messages API: answers each request with the next scripted reply.

    ``conversation`` is the reply sequence one run gets; it is replayed for
    each run, so a job delivered twice gets the same answers twice.
    """

    conversation: list[Any] = field(default_factory=list)
    requests: list[dict[str, Any]] = field(default_factory=list)
    _pending: list[Any] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.requests.append(body)
        # A run's first request starts the conversation over; a retried
        # first request (after a 529, say) continues the one in progress.
        if len(body["messages"]) == 1 and not self._pending:
            self._pending = list(self.conversation)
        reply = self._pending.pop(0)
        if isinstance(reply, httpx.Response):
            return reply
        return httpx.Response(200, json=reply)

    def first_requests(self) -> int:
        """Requests that opened a run's conversation, retries included."""
        return sum(1 for body in self.requests if len(body["messages"]) == 1)


# ── GitHub and the registry ──────────────────────────────────────────────


def zipball(repo: str, files: dict[str, str]) -> bytes:
    """A repository archive as GitHub serves it: everything under one root."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        root = f"mini-app-polis-{repo}-0000000/"
        archive.writestr(root, "")
        for path, text in files.items():
            archive.writestr(root + path, text)
    return buffer.getvalue()


@dataclass
class FakeGitHub:
    downloads: list[str] = field(default_factory=list)

    def handler(self, repo: str) -> Callable[[httpx.Request], httpx.Response]:
        def handle(request: httpx.Request) -> httpx.Response:
            self.downloads.append(repo)
            return httpx.Response(200, content=zipball(repo, REPOS[repo]))

        return handle


# ── api-kaianolevine-com ─────────────────────────────────────────────────


@dataclass
class FakeApi:
    """The grooming routes, recorded. ``spent`` is the month's spend so far."""

    spent: float = 0.0
    runs: list[dict[str, Any]] = field(default_factory=list)
    proposals: list[dict[str, Any]] = field(default_factory=list)
    attempts: dict[str, int] = field(default_factory=dict)
    auth: list[str] = field(default_factory=list)
    #: Claims by (task_gid, fingerprint): "claimed" or "released", as the
    #: API's unique key holds them.
    claims: dict[tuple[str, str], str] = field(default_factory=dict)
    releases: list[dict[str, Any]] = field(default_factory=list)
    _faults: dict[str, list[int]] = field(default_factory=dict)

    def fail(self, path: str, status: int, *, times: int = 1) -> None:
        self._faults.setdefault(path, []).extend([status] * times)

    def _fault(self, path: str) -> httpx.Response | None:
        self.attempts[path] = self.attempts.get(path, 0) + 1
        pending = self._faults.get(path)
        if pending:
            return httpx.Response(pending.pop(0), json={"detail": "injected failure"})
        return None

    @staticmethod
    def _ok(data: dict[str, Any]) -> httpx.Response:
        return httpx.Response(
            200, json={"data": data, "meta": {"count": 1, "total": 1, "version": "t"}}
        )

    def claim(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        failed = self._fault("/v1/tasks/groomings/claims")
        if failed:
            return failed
        key = (body["task_gid"], body["fingerprint"])
        won = self.claims.get(key) in (None, "released")
        if won:
            self.claims[key] = "claimed"
        return self._ok({**body, "claimed": won})

    def release(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        self.releases.append(body)
        key = (body["task_gid"], body["fingerprint"])
        held = self.claims.get(key) == "claimed"
        if held:
            self.claims[key] = "released"
        return self._ok({**body, "released": held})

    def spend(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("Authorization", ""))
        return self._fault("/v1/tasks/groomings/spend") or self._ok(
            {
                "month": "2026-10",
                "runs": len(self.runs),
                "cost_usd": self.spent + sum(r["cost_usd"] for r in self.runs),
            }
        )

    def record_run(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        failed = self._fault("/v1/tasks/groomings/runs")
        if failed:
            return failed
        self.runs.append(body)
        return self._ok(
            {
                "id": f"run-{len(self.runs)}",
                "task_gid": body["task_gid"],
                "cost_usd": body["cost_usd"],
                "recorded_at": "2026-10-23T12:00:00Z",
            }
        )

    def submit_proposal(self, request: httpx.Request) -> httpx.Response:
        self.auth.append(request.headers.get("Authorization", ""))
        body = json.loads(request.content)
        failed = self._fault("/v1/tasks/groomings/proposals")
        if failed:
            return failed
        self.proposals.append(body)
        return self._ok(
            {
                "accepted": True,
                "message_id": f"q-{len(self.proposals)}",
                "task_gid": body["task_gid"],
            }
        )
