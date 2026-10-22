from __future__ import annotations

from task_groomer_cog.prompt import system_prompt


def test_the_board_list_says_what_a_done_mark_means() -> None:
    text = system_prompt(
        {"infra": "Terraform."}, ["MINI-21 · First part (done)", "MINI-22 · Next"]
    )
    assert "- MINI-21 · First part (done)" in text
    assert "marked (done)" in text
    assert "do not ask whether it exists or is done" in text
