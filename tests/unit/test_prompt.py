from __future__ import annotations

from typing import get_args

from _fakes import TASK
from mini_app_polis.api.contract import GROOMING_MAX_CHILDREN, GroomingExecutor

from task_groomer_cog.agent import PROPOSE, TOOLS, Limits, request_body, tools
from task_groomer_cog.prompt import EXECUTOR_MEANINGS, system_prompt, task_message


def test_the_board_list_says_what_a_done_mark_means() -> None:
    text = system_prompt(
        {"infra": "Terraform."}, ["MINI-21 · First part (done)", "MINI-22 · Next"]
    )
    assert "- MINI-21 · First part (done)" in text
    assert "marked (done)" in text
    assert "do not ask whether it exists or is done" in text


def test_blocked_by_may_name_a_date_instead_of_a_question() -> None:
    text = system_prompt({"infra": "Terraform."}, [])
    assert "YYYY-MM-DD (a UTC day)" in text
    assert "gets that date here; do not ask how to express the wait" in text


def test_every_executor_in_the_contract_is_explained() -> None:
    assert set(EXECUTOR_MEANINGS) == set(get_args(GroomingExecutor))
    text = system_prompt({"infra": "Terraform."}, [])
    assert "{executor_lines}" not in text
    for name in get_args(GroomingExecutor):
        assert f"  - {name}: {EXECUTOR_MEANINGS[name]}." in text
    assert "No tag means the agent" in text
    assert "Do not ask who does the work" in text
    assert "propose the executor by what the task must reach" in text


def test_the_task_message_names_its_executor() -> None:
    assert "Executor: the agent (no executor tag)" in task_message(TASK)
    tagged = TASK.model_copy(update={"tags": ["Cowork", "admin"]})
    assert "Executor: cowork (its tag; groom it for that executor)" in task_message(
        tagged
    )


def test_the_propose_tool_offers_the_contracts_executors() -> None:
    (propose,) = [t for t in TOOLS if t["name"] == PROPOSE]
    schema = propose["input_schema"]["properties"]["executor"]
    assert schema["enum"] == list(get_args(GroomingExecutor))
    assert "executor" not in propose["input_schema"]["required"]


def test_splitting_is_only_in_the_prompt_when_it_is_on() -> None:
    off = system_prompt({"infra": "Terraform."}, [])
    on = system_prompt({"infra": "Terraform."}, [], split=True)
    assert "Splitting." not in off
    assert "Splitting." in on
    assert f"At most {GROOMING_MAX_CHILDREN} children" in on
    assert "blocked_by_siblings, by its position in children, counting from 0" in on
    assert "give the task itself only its title and body" in on
    assert "A task already in one of the two shapes is never split" in on


def test_the_propose_tool_takes_children_only_when_splitting() -> None:
    def propose(tools_: list[dict]) -> dict:
        (tool,) = [t for t in tools_ if t["name"] == PROPOSE]
        return tool["input_schema"]["properties"]

    assert "children" not in propose(tools())
    children = propose(tools(split=True))["children"]
    assert children["maxItems"] == GROOMING_MAX_CHILDREN
    item = children["items"]
    assert item["properties"]["executor"]["enum"] == list(get_args(GroomingExecutor))
    assert set(item["required"]) == {
        "title",
        "done_when",
        "where",
        "constraints",
        "effort",
        "body",
    }
    # The cached tool list is the same object when splitting is off.
    assert tools() is TOOLS


def test_the_first_request_follows_the_split_setting() -> None:
    body = request_body(TASK, {"infra": "x"}, [], Limits(split=True))
    assert "Splitting." in body["system"][0]["text"]
    assert any(
        "children" in t["input_schema"]["properties"]
        for t in body["tools"]
        if t["name"] == PROPOSE
    )
    assert (
        "Splitting."
        not in request_body(TASK, {"infra": "x"}, [], Limits())["system"][0]["text"]
    )


def test_the_split_setting_is_read_from_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("GROOMER_SPLIT", raising=False)
    assert Limits.from_env().split is False
    monkeypatch.setenv("GROOMER_SPLIT", "true")
    assert Limits.from_env().split is True
