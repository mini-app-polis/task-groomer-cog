from __future__ import annotations

import httpx
import pytest

from task_groomer_cog import registry

DOC = """services:
- id: watcher-cog
  description: 'Drive folder
    watcher.'
- id: infra
- type: no-id
"""


def _client(status: int, text: str) -> httpx.Client:
    return httpx.Client(
        transport=httpx.MockTransport(lambda request: httpx.Response(status, text=text))
    )


def test_the_registry_maps_repositories_to_descriptions() -> None:
    assert registry.load_registry(client=_client(200, DOC)) == {
        "watcher-cog": "Drive folder watcher.",
        "infra": "",
    }


@pytest.mark.parametrize(
    ("status", "text"), [(500, "down"), (200, "patterns: {}"), (200, "services: []")]
)
def test_an_unreadable_registry_stops_the_run(status: int, text: str) -> None:
    with pytest.raises(registry.RegistryUnavailable):
        registry.load_registry(client=_client(status, text))
