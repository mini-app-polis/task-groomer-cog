from __future__ import annotations

import pytest
from _fakes import TASK

from task_groomer_cog.proposal import build


def test_a_proposal_carries_the_task_and_what_it_cost() -> None:
    proposal = build(
        TASK,
        {"title": "  Dashboard  ", "done_when": "", "effort": "M", "questions": ["Q?"]},
        model="claude-sonnet-5-5",
        cost_usd=0.1234567,
    )
    assert proposal.task_gid == TASK.task_gid
    assert proposal.fingerprint == TASK.fingerprint
    assert proposal.title == "Dashboard"
    assert proposal.done_when is None
    assert proposal.effort == "M"
    assert proposal.questions == ["Q?"]
    assert proposal.cost_usd == 0.123457


def test_what_the_model_got_wrong_is_dropped_or_cut() -> None:
    proposal = build(
        TASK,
        {
            "title": 42,
            "effort": "XL",
            "body": "x" * 30_000,
            "questions": ["", None, *[f"q{i}" for i in range(20)]],
        },
        model="m",
        cost_usd=0,
    )
    assert proposal.title == "idea - dashboard for TV shows and movies"
    assert proposal.effort is None
    assert proposal.body is not None and len(proposal.body) == 20_000
    assert proposal.questions == [f"q{i}" for i in range(10)]


@pytest.mark.parametrize("executor", ["cowork", "person"])
def test_a_proposal_carries_the_executor_the_model_named(executor: str) -> None:
    proposal = build(TASK, {"title": "T", "executor": executor}, model="m", cost_usd=0)
    assert proposal.executor == executor


@pytest.mark.parametrize("executor", [None, "agent", "Cowork", 3])
def test_an_executor_off_the_contract_is_dropped(executor: object) -> None:
    proposal = build(TASK, {"title": "T", "executor": executor}, model="m", cost_usd=0)
    assert proposal.executor is None


def test_a_task_already_tagged_keeps_its_tag() -> None:
    tagged = TASK.model_copy(update={"tags": ["cowork"]})
    proposal = build(
        tagged, {"title": "T", "executor": "person"}, model="m", cost_usd=0
    )
    assert proposal.executor is None
