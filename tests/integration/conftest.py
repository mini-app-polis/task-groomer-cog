"""Integration harness: the Lambda handler, end to end, with the world stubbed.

What runs for real: ``worker.lambda_handler``, the grooming loop with its
caps, the read-only repository tools over real zip archives, the registry
parser, proposal building, and the shared ``KaianoApiClient`` with its
request and response contract models.

What is stubbed, all at the HTTP boundary with respx (TEST-007): the
Messages API, GitHub's archive downloads, the ecosystem registry, and
api-kaianolevine-com. Every request is matched; an unmatched one fails the
test, so nothing here reaches a real service and no model call is paid for.
Run reports are caught by the autouse fixture in tests/conftest.py.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from typing import Any

import pytest
import respx

from .harness import (
    API_BASE,
    API_KEY,
    GITHUB_ZIPBALL,
    MESSAGES_URL,
    REGISTRY_URL,
    REGISTRY_YAML,
    REPOS,
    FakeApi,
    FakeGitHub,
    FakeModel,
    LambdaContext,
)


@pytest.fixture(autouse=True)
def _environment(monkeypatch: pytest.MonkeyPatch) -> None:
    # Production, so the client reads KAIANO_API_BASE_URL, not the _DEV one.
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("KAIANO_API_BASE_URL", API_BASE)
    monkeypatch.setenv("TASK_GROOMER_COG_API_KEY", API_KEY)
    monkeypatch.delenv("KAIANO_API_KEY", raising=False)
    monkeypatch.delenv("KAIANO_API_BASE_URL_DEV", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("ECOSYSTEM_YAML_URL", REGISTRY_URL)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    for name in (
        "GROOMER_MODEL",
        "GROOMER_MAX_ROUNDS",
        "GROOMER_MAX_INPUT_TOKENS",
        "GROOMER_MAX_OUTPUT_TOKENS",
        "GROOMER_SPLIT",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GROOMER_MONTHLY_CAP_USD", "20")
    # No waiting out a retry backoff in a test.
    monkeypatch.setattr("task_groomer_cog.agent.time.sleep", lambda _s: None)
    monkeypatch.setattr(
        "mini_app_polis.api.client._sleep", lambda _s: None, raising=False
    )


@pytest.fixture
def world() -> Iterator[tuple[FakeModel, FakeGitHub, FakeApi]]:
    """Every service the handler reaches, stubbed and recorded."""
    model, github, api = FakeModel(), FakeGitHub(), FakeApi()
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        router.post(MESSAGES_URL).mock(side_effect=model)
        router.get(REGISTRY_URL).respond(200, text=REGISTRY_YAML)
        for repo in REPOS:
            router.get(GITHUB_ZIPBALL.format(repo=repo, branch="dev")).mock(
                side_effect=github.handler(repo)
            )
        router.post(f"{API_BASE}/v1/tasks/groomings/claims").mock(side_effect=api.claim)
        router.post(f"{API_BASE}/v1/tasks/groomings/claims/release").mock(
            side_effect=api.release
        )
        router.get(f"{API_BASE}/v1/tasks/groomings/spend").mock(side_effect=api.spend)
        router.post(f"{API_BASE}/v1/tasks/groomings/runs").mock(
            side_effect=api.record_run
        )
        router.post(f"{API_BASE}/v1/tasks/groomings/proposals").mock(
            side_effect=api.submit_proposal
        )
        yield model, github, api


@pytest.fixture
def handler() -> Callable[..., dict]:
    """``lambda_handler``, imported as Lambda would import it."""
    from task_groomer_cog.worker import lambda_handler

    def invoke(event: dict, context: Any | None = None) -> dict:
        return lambda_handler(
            event, context if context is not None else LambdaContext()
        )

    return invoke
