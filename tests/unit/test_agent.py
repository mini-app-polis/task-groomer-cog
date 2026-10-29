from __future__ import annotations

import httpx
import pytest
from _fakes import PROPOSAL_INPUT, REGISTRY, TASK, Script, text_reply, tool_use, usage

from task_groomer_cog import agent
from task_groomer_cog.agent import BudgetSpent, GroomingFailed, Limits, groom


class FakeRepos:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def list_files(self, repo: str, path: str = "") -> str:
        self.calls.append(("list_files", repo, path))
        return f"{repo}@dev:/\nREADME.md"

    def read_file(self, repo: str, path: str, start_line: int = 1) -> str:
        self.calls.append(("read_file", repo, path, start_line))
        return "1: hello"

    def search(self, repo: str, pattern: str, path: str = "") -> str:
        from task_groomer_cog.repos import RepoError

        raise RepoError(f"{repo} is not a repository in the registry.")


@pytest.fixture(autouse=True)
def key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test")
    monkeypatch.setattr(agent.time, "sleep", lambda s: None)


def _groom(script: Script, *, limits: Limits | None = None, spent: float = 0.0):
    repos = FakeRepos()
    result = groom(
        TASK,
        registry=REGISTRY,
        board=["TASK-9 · Discord commands"],
        repos=repos,  # type: ignore[arg-type]
        spent_this_month=lambda: spent,
        limits=limits or Limits(),
        http=script.client(),
    )
    return result, repos


def test_a_run_reads_then_proposes() -> None:
    script = Script(
        tool_use("list_files", {"repo": "infra"}),
        tool_use("propose", PROPOSAL_INPUT, id_="t2"),
    )
    result, repos = _groom(script)

    assert result.proposal == PROPOSAL_INPUT
    assert result.rounds == 2
    assert repos.calls == [("list_files", "infra", "")]
    assert result.cost_usd == pytest.approx(2 * (1000 * 2 + 200 * 10) / 1_000_000)

    first, second = script.bodies
    assert first["model"] == "claude-sonnet-5-5"
    assert first["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "TASK-9 · Discord commands" in first["system"][0]["text"]
    assert "infra: Terraform for the fleet." in first["system"][0]["text"]
    assert "TASK-38 · idea" in first["messages"][0]["content"]
    assert second["messages"][-1]["content"][0]["type"] == "tool_result"
    assert "tool_choice" not in first


def test_a_tool_error_goes_back_to_the_model() -> None:
    script = Script(
        tool_use("search", {"repo": "infra", "pattern": "x"}),
        tool_use("propose", PROPOSAL_INPUT, id_="t2"),
    )
    _groom(script)
    result_block = script.bodies[1]["messages"][-1]["content"][0]
    assert result_block["is_error"] is True
    assert "not a repository" in result_block["content"]


def test_the_last_round_must_propose() -> None:
    script = Script(
        tool_use("list_files", {"repo": "infra"}),
        tool_use("propose", PROPOSAL_INPUT, id_="t2"),
    )
    _groom(script, limits=Limits(max_rounds=2))
    second = script.bodies[1]
    assert "tool_choice" not in second
    assert second["messages"][-1]["content"][-1] == {
        "type": "text",
        "text": agent.PROPOSE_NOW,
    }
    assert "tool_choice" not in script.bodies[0]


def test_a_one_round_run_asks_for_the_proposal_in_the_task_message() -> None:
    script = Script(tool_use("propose", PROPOSAL_INPUT))
    _groom(script, limits=Limits(max_rounds=1))
    only = script.bodies[0]
    assert "tool_choice" not in only
    assert only["messages"][0]["content"].endswith(agent.PROPOSE_NOW)


def test_nearing_the_input_cap_asks_for_the_proposal() -> None:
    big = tool_use("list_files", {"repo": "infra"})
    big["usage"] = usage(input_tokens=900)
    script = Script(big, tool_use("propose", PROPOSAL_INPUT, id_="t2"))
    _groom(script, limits=Limits(max_input_tokens=1000))
    second = script.bodies[1]
    assert "tool_choice" not in second
    assert second["messages"][-1]["content"][-1]["text"] == agent.PROPOSE_NOW


def test_a_reply_without_a_tool_is_told_to_propose() -> None:
    script = Script(text_reply("I think…"), tool_use("propose", PROPOSAL_INPUT))
    result, _ = _groom(script)
    assert result.proposal == PROPOSAL_INPUT
    assert script.bodies[1]["messages"][-1]["content"] == "Call the propose tool now."
    assert "tool_choice" not in script.bodies[1]


def test_no_proposal_within_the_rounds_fails_with_its_cost() -> None:
    script = Script(*[tool_use("list_files", {"repo": "infra"})] * 2)
    with pytest.raises(GroomingFailed) as raised:
        # The last round asks for the proposal; this script still reads.
        _groom(script, limits=Limits(max_rounds=2))
    assert raised.value.cost_usd > 0


def test_a_spent_budget_stops_before_any_request() -> None:
    script = Script()
    with pytest.raises(BudgetSpent):
        _groom(script, spent=20.0)
    assert script.bodies == []


def test_the_budget_counts_this_runs_cost_too() -> None:
    script = Script(tool_use("list_files", {"repo": "infra"}))
    with pytest.raises(BudgetSpent) as raised:
        _groom(script, spent=19.999)
    assert raised.value.cost_usd > 0


def test_a_transient_failure_is_tried_again() -> None:
    script = Script(httpx.Response(529), tool_use("propose", PROPOSAL_INPUT))
    result, _ = _groom(script)
    assert result.proposal == PROPOSAL_INPUT


def test_a_refusal_fails_the_run() -> None:
    script = Script(httpx.Response(400, text="bad request"))
    with pytest.raises(GroomingFailed, match="answered 400"):
        _groom(script)


def test_an_unpriced_model_is_refused_before_any_request() -> None:
    script = Script()
    with pytest.raises(Exception, match="has no price"):
        _groom(script, limits=Limits(model="claude-mystery"))


def test_without_a_key_nothing_is_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    with pytest.raises(GroomingFailed, match="ANTHROPIC_API_KEY"):
        _groom(Script())


def test_count_tokens_sends_only_what_it_counts() -> None:
    script = Script({"input_tokens": 4321})
    body = agent.request_body(TASK, REGISTRY, [], Limits())
    assert agent.count_tokens(body, http=script.client()) == 4321
    assert set(script.bodies[0]) == {"model", "system", "tools", "messages"}


def test_limits_come_from_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GROOMER_MODEL", "claude-haiku-5-5")
    monkeypatch.setenv("GROOMER_MAX_ROUNDS", "4")
    monkeypatch.setenv("GROOMER_MONTHLY_CAP_USD", "7.5")
    limits = Limits.from_env()
    assert (limits.model, limits.max_rounds, limits.monthly_cap_usd) == (
        "claude-haiku-5-5",
        4,
        7.5,
    )


def test_a_reply_cut_off_at_max_tokens_fails_the_run() -> None:
    cut = tool_use("propose", {"title": "Half"})
    cut["stop_reason"] = "max_tokens"
    with pytest.raises(GroomingFailed, match="max_tokens") as raised:
        _groom(Script(cut))
    assert raised.value.cost_usd > 0


def test_the_owners_answers_reach_the_model() -> None:
    from task_groomer_cog.prompt import task_message

    answered = TASK.model_copy(update={"comments": ["It is for me only."]})
    assert "It is for me only." in task_message(answered)
    assert "comments since" not in task_message(TASK)
