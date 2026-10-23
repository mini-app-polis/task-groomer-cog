from __future__ import annotations

import datetime as dt
import json
from dataclasses import dataclass, field
from typing import Any

import pytest
from _fakes import PROPOSAL_INPUT, REGISTRY, TASK
from mini_app_polis.api.contract import (
    GroomingProposal,
    GroomingProposalAccepted,
    GroomingRunCreate,
    GroomingRunRecorded,
    GroomingSpend,
)

from task_groomer_cog import worker
from task_groomer_cog.agent import BudgetSpent, GroomingFailed, RunResult
from task_groomer_cog.pricing import Usage

JOB = {"task": TASK.model_dump(mode="json"), "board": ["MINI-9 · Discord commands"]}


def _body(**overrides: Any) -> str:
    message = {"type": "task.groom", "version": 1, "payload": JOB}
    message.update(overrides)
    return json.dumps(message)


@dataclass
class FakeApi:
    spent: float = 0.0
    runs: list[GroomingRunCreate] = field(default_factory=list)
    proposals: list[GroomingProposal] = field(default_factory=list)

    def grooming_spend(self) -> GroomingSpend:
        return GroomingSpend(month="2026-10", runs=len(self.runs), cost_usd=self.spent)

    def record_grooming_run(self, payload: GroomingRunCreate) -> GroomingRunRecorded:
        self.runs.append(payload)
        return GroomingRunRecorded(
            id="r",
            task_gid=payload.task_gid,
            cost_usd=payload.cost_usd,
            recorded_at=dt.datetime.now(dt.UTC),
        )

    def submit_grooming_proposal(
        self, payload: GroomingProposal
    ) -> GroomingProposalAccepted:
        self.proposals.append(payload)
        return GroomingProposalAccepted(message_id="m", task_gid=payload.task_gid)


def _run(api: FakeApi, outcome: Any) -> None:
    def fake_groom(task, **kwargs):
        assert kwargs["board"] == ["MINI-9 · Discord commands"]
        assert kwargs["spent_this_month"]() == api.spent
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    worker.process_message(
        _body(),
        run_id="m-1",
        api_factory=lambda: api,
        registry_loader=lambda: REGISTRY,
        repos_factory=lambda registry: object(),
        groom_fn=fake_groom,
    )


@pytest.mark.parametrize(
    ("body", "reason"),
    [
        ("not json", "not JSON"),
        ("[1]", "not an object"),
        (_body(version=2), "version"),
        (_body(type="task.create"), "message type"),
        (_body(payload={}), "not a valid task.groom"),
    ],
)
def test_unreadable_messages_are_refused(body: str, reason: str) -> None:
    with pytest.raises(worker.UnprocessableMessage, match=reason):
        worker.parse_message(body)


def test_a_run_records_its_cost_and_hands_its_proposal_back(
    _no_outbound_reports,
) -> None:
    api = FakeApi(spent=3.0)
    result = RunResult(
        proposal=dict(PROPOSAL_INPUT),
        model="claude-sonnet-5-5",
        usage=Usage(
            input_tokens=5000, output_tokens=900, cache_read_input_tokens=20000
        ),
        cost_usd=0.0213,
        rounds=3,
    )
    _run(api, result)

    (run,) = api.runs
    assert (run.input_tokens, run.output_tokens, run.cost_usd) == (25000, 900, 0.0213)
    (proposal,) = api.proposals
    assert proposal.task_gid == TASK.task_gid
    assert proposal.fingerprint == TASK.fingerprint
    assert proposal.questions == PROPOSAL_INPUT["questions"]
    ((_, kwargs),) = _no_outbound_reports
    assert "MINI-38 · Dashboard for TV shows and movies" in kwargs["text"]


@pytest.mark.parametrize(
    ("error", "reason"),
    [
        (BudgetSpent("spent", cost_usd=0.0), "grooming budget spent"),
        (
            GroomingFailed("no proposal", cost_usd=0.05, usage=Usage(10, 2)),
            "grooming run failed",
        ),
    ],
)
def test_a_run_that_ends_without_a_proposal_is_reported_not_retried(
    _no_outbound_reports, error: Exception, reason: str
) -> None:
    api = FakeApi()
    _run(api, error)  # does not raise: the message is not redelivered
    assert api.proposals == []
    assert [r.cost_usd for r in api.runs] == ([0.05] if error.cost_usd else [])
    ((args, kwargs),) = _no_outbound_reports
    assert args[1] == "WARN"
    assert reason in kwargs["text"]


def test_the_handler_reports_an_unreadable_message_and_names_it(
    monkeypatch: pytest.MonkeyPatch, _no_outbound_reports
) -> None:
    monkeypatch.setattr(worker, "load_secrets", lambda refresh: [])
    event = {"Records": [{"messageId": "m-1", "body": "not json", "attributes": {}}]}
    assert worker.lambda_handler(event, None) == {
        "batchItemFailures": [{"itemIdentifier": "m-1"}]
    }
    ((args, _),) = _no_outbound_reports
    assert args[1] == "ERROR"


def test_the_handler_names_a_job_that_crashed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(worker, "load_secrets", lambda refresh: [])

    def crash(body, *, run_id, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(worker, "process_message", crash)
    event = {"Records": [{"messageId": "m-2", "body": _body(), "attributes": {}}]}
    assert worker.lambda_handler(event, None) == {
        "batchItemFailures": [{"itemIdentifier": "m-2"}]
    }


def test_the_handler_returns_everything_when_settings_cannot_load(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail(refresh):
        raise RuntimeError("ssm")

    monkeypatch.setattr(worker, "load_secrets", fail)
    event = {
        "Records": [{"messageId": "a", "body": "x"}, {"messageId": "b", "body": "y"}]
    }
    assert worker.lambda_handler(event, None)["batchItemFailures"] == [
        {"itemIdentifier": "a"},
        {"itemIdentifier": "b"},
    ]
