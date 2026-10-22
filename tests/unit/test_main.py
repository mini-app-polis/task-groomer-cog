from __future__ import annotations

import pytest
from mini_app_polis.asana import AsanaTaskRecord

from task_groomer_cog import __main__ as cli
from task_groomer_cog.agent import RunResult


def _task(gid: str, name: str, section: str = "Ideas", completed: bool = False):
    return AsanaTaskRecord(
        gid=gid,
        name=name,
        notes="Done when:",
        completed=completed,
        section_gid=None,
        section_name=section,
    )


BOARD = [
    _task("1", "MINI-1 · One"),
    _task("2", "MINI-2 · Two", section="Ready"),
    _task("3", "MINI-3 · TEMPLATE — do not work"),
    _task("4", "Four", section="Blocked"),
    _task("5", "MINI-5 · Done", completed=True),
]


@pytest.fixture
def board(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setenv("ASANA_INBOX_PROJECT_ID", "p")
    monkeypatch.setattr(
        cli.AsanaClient, "list_project_tasks", lambda self, project: list(BOARD)
    )
    monkeypatch.setattr(cli, "load_registry", lambda: {"infra": "Terraform."})
    counted: list[dict] = []

    def fake_count(body):
        counted.append(body)
        return 10_000

    monkeypatch.setattr(cli, "count_tokens", fake_count)
    return counted


def test_the_estimate_prices_a_short_run_below_a_long_one() -> None:
    guess = cli.estimate(10_000, "claude-sonnet-5-5", 150_000)
    assert 0 < guess.low_usd < guess.high_usd
    # Round one writes 10K to the cache at $2.50/M: the floor of any run.
    assert guess.low_usd > 10_000 * 2.5 / 1_000_000


def test_the_estimate_stops_at_the_input_cap() -> None:
    capped = cli.estimate(60_000, "claude-sonnet-5-5", 150_000)
    uncapped = cli.estimate(60_000, "claude-sonnet-5-5", 10_000_000)
    assert capped.high_usd < uncapped.high_usd


class _Lines:
    """Stands in for the logger and keeps what was logged."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def info(self, message: str, *args: object) -> None:
        self.lines.append(message % args)


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> _Lines:
    lines = _Lines()
    monkeypatch.setattr(cli, "log", lines)
    return lines


def test_a_board_dry_run_counts_the_grooming_columns_only(
    board: list[dict], logged: _Lines
) -> None:
    assert cli.main(["--board", "--dry-run"]) == 0
    out = "\n".join(logged.lines)
    assert "MINI-1 · One" in out and "Four" in out
    assert not any("MINI-2 · Two" in line for line in logged.lines)
    assert "2 task(s) with claude-sonnet-5-5" in out
    assert "Nothing was spent." in out
    assert len(board) == 2
    # A task is not shown its own name among the other open tasks.
    assert "- MINI-1 · One" not in board[0]["system"][0]["text"]


def test_a_task_dry_run_counts_that_task(board: list[dict]) -> None:
    assert cli.main(["--task", "4", "--dry-run"]) == 0
    assert len(board) == 1


def test_the_board_is_an_estimate_only(board: list[dict]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--board"])


def test_an_unknown_task_is_refused(board: list[dict]) -> None:
    with pytest.raises(SystemExit):
        cli.main(["--task", "999", "--dry-run"])


def test_a_real_run_logs_the_proposal_and_cost(
    board: list[dict],
    monkeypatch: pytest.MonkeyPatch,
    logged: _Lines,
) -> None:
    monkeypatch.setattr(
        cli,
        "groom",
        lambda task, **kw: RunResult(
            proposal={"title": "Do one", "questions": []},
            model="claude-sonnet-5-5",
            cost_usd=0.0912,
            rounds=2,
            tool_calls=["list_files({})"],
        ),
    )
    assert cli.main(["--task", "1"]) == 0
    out = "\n".join(logged.lines)
    assert '"title": "Do one"' in out
    assert "$0.0912" in out
