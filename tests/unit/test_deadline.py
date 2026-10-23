from __future__ import annotations

import time

import pytest
from mini_app_polis.lambda_worker import RunOutOfTime, deadline

from task_groomer_cog.worker import DEADLINE_MARGIN_SECONDS


class _Context:
    def __init__(self, remaining_ms: float) -> None:
        self._remaining_ms = remaining_ms

    def get_remaining_time_in_millis(self) -> float:
        return self._remaining_ms


def test_no_context_means_no_deadline() -> None:
    with deadline(None):
        pass


def test_too_little_time_refuses_to_start() -> None:
    with pytest.raises(RunOutOfTime), deadline(_Context(1000)):
        pass  # pragma: no cover


def test_a_run_past_the_margin_is_stopped() -> None:
    ms = (DEADLINE_MARGIN_SECONDS + 0.05) * 1000
    with (
        pytest.raises(RunOutOfTime),
        deadline(_Context(ms), margin_seconds=DEADLINE_MARGIN_SECONDS),
    ):
        time.sleep(1)
