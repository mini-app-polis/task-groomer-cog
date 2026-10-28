from __future__ import annotations

import pytest
from _fakes import TASK
from mini_app_polis.api.contract import GROOMING_MAX_CHILDREN, GroomingProposal

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


def _child(**overrides: object) -> dict[str, object]:
    return {
        "title": "common-python-utils: the contract",
        "done_when": "It is released.",
        "where": "common-python-utils",
        "constraints": "None",
        "effort": "S",
        "body": "The shared shape first.",
        **overrides,
    }


SPLIT = {
    "title": "Executor tags across the fleet",
    "done_when": "ignored for a split",
    "where": "common-python-utils, task-manager-cog",
    "body": "Why the work exists.",
    "questions": [],
    "children": [
        _child(),
        _child(
            title="task-manager-cog: apply the tag",
            where="task-manager-cog",
            blocked_by_siblings=[0],
            executor="cowork",
        ),
    ],
}


def test_a_split_carries_its_children_and_only_the_tasks_title_and_body() -> None:
    proposal = build(TASK, SPLIT, model="m", cost_usd=0.2, split=True)
    assert [c.title for c in proposal.children] == [
        "common-python-utils: the contract",
        "task-manager-cog: apply the tag",
    ]
    assert proposal.children[1].blocked_by_siblings == [0]
    assert proposal.children[1].executor == "cowork"
    assert proposal.title == "Executor tags across the fleet"
    assert proposal.body == "Why the work exists."
    assert proposal.done_when is None and proposal.where is None


def test_children_are_ignored_when_splitting_is_off() -> None:
    proposal = build(TASK, SPLIT, model="m", cost_usd=0.2)
    assert proposal.children == []
    assert proposal.where == "common-python-utils, task-manager-cog"


@pytest.mark.parametrize(
    "children",
    [
        [_child(), _child(blocked_by_siblings=[1])],  # waits on itself
        [_child(), _child(blocked_by_siblings=[5])],  # no such sibling
        [_child(), _child(effort="XL")],
        [_child(), _child(done_when="")],
        [_child(), "not a child"],
        [_child()] * (GROOMING_MAX_CHILDREN + 1),
    ],
)
def test_a_split_that_does_not_hold_is_dropped_whole(children: list) -> None:
    proposal = build(
        TASK, {**SPLIT, "children": children}, model="m", cost_usd=0.2, split=True
    )
    assert proposal.children == []
    # The task is proposed as it would be without a split.
    assert proposal.where == "common-python-utils, task-manager-cog"


def test_an_off_contract_child_executor_is_dropped_not_the_split() -> None:
    proposal = build(
        TASK,
        {**SPLIT, "children": [_child(executor="robot")]},
        model="m",
        cost_usd=0,
        split=True,
    )
    assert [c.executor for c in proposal.children] == [None]


def test_a_proposal_without_an_executor_does_not_send_the_field() -> None:
    plain = build(TASK, {"title": "T"}, model="m", cost_usd=0)
    assert "executor" not in plain.model_fields_set
    assert "children" not in plain.model_fields_set
    named = build(TASK, {"title": "T", "executor": "person"}, model="m", cost_usd=0)
    assert "executor" in named.model_fields_set


DECISION = {
    "title": "Decide what tagging means and which data feeds identification",
    "done_when": "A decision record under docs/decisions states the choices.",
    "where": "common-python-utils",
    "constraints": "None",
    "effort": "S",
    "blocked_by": "None",
    "body": "The owner decides the product behaviour before anything is built.",
    "executor": "cowork",
    "questions": [],
}


def test_a_decision_record_task_is_proposed_as_cowork() -> None:
    proposal = build(TASK, DECISION, model="m", cost_usd=0.1)
    assert proposal.executor == "cowork"
    # It round-trips as the contract's proposal, executor and all.
    sent = proposal.model_dump_json(exclude_unset=True)
    assert GroomingProposal.model_validate_json(sent).executor == "cowork"


def test_a_decision_and_its_build_split_into_a_cowork_child_first() -> None:
    proposed = {
        "title": "Redesign tagging",
        "body": "Decide, then build.",
        "children": [
            _child(title="Decide what tagging means", executor="cowork"),
            _child(title="Build the tagging", blocked_by_siblings=[0]),
        ],
    }
    proposal = build(TASK, proposed, model="m", cost_usd=0.1, split=True)
    decision, build_child = proposal.children
    assert decision.executor == "cowork"
    assert build_child.executor is None
    assert build_child.blocked_by_siblings == [0]
