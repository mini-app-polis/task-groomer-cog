from __future__ import annotations

from typing import get_args

from _fakes import TASK
from mini_app_polis.api.contract import GroomingExecutor

from task_groomer_cog.agent import PROPOSE, TOOLS
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
