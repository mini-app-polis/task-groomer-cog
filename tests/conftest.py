"""Shared fixtures for task-groomer-cog."""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _no_outbound_reports(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Keep run reports and findings in the test, and out of the API.

    Returns the list each would-be post is appended to.
    """
    posted: list[tuple] = []

    def fake_post_run_finding(*args, **kwargs):
        posted.append((args, kwargs))

    monkeypatch.setattr(
        "mini_app_polis.pipeline_status.post_run_finding", fake_post_run_finding
    )
    monkeypatch.setattr(
        "task_groomer_cog.worker.post_run_finding", fake_post_run_finding
    )
    return posted
