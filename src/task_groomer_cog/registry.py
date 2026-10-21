"""The fleet's repositories, as the ecosystem registry describes them.

ecosystem-standards' ``ecosystem.yaml`` lists every repository
(``services[].id``) with a description of what it is and how it runs. The
groomer gives the model those descriptions, so it can tell which
repositories a vague task is about, and refuses to open a repository that
is not listed.

  ECOSYSTEM_YAML_URL  where to read it; defaults to the file on main
"""

from __future__ import annotations

import os

import httpx
import yaml

DEFAULT_URL = (
    "https://raw.githubusercontent.com/mini-app-polis/ecosystem-standards/"
    "main/ecosystem.yaml"
)

TIMEOUT_SECONDS = 20.0


class RegistryUnavailable(RuntimeError):
    """The registry could not be read, so no repository can be judged."""


def parse_registry(text: str) -> dict[str, str]:
    """Repository name → its description, in the registry's order."""
    document = yaml.safe_load(text) or {}
    services = document.get("services") if isinstance(document, dict) else None
    if not isinstance(services, list):
        raise RegistryUnavailable("the registry has no services list")
    registry: dict[str, str] = {}
    for service in services:
        if isinstance(service, dict) and service.get("id"):
            description = " ".join(str(service.get("description") or "").split())
            registry[str(service["id"])] = description
    if not registry:
        raise RegistryUnavailable("the registry lists no repositories")
    return registry


def load_registry(*, client: httpx.Client | None = None) -> dict[str, str]:
    """Read the registry. Raises :class:`RegistryUnavailable` on any failure."""
    url = (os.environ.get("ECOSYSTEM_YAML_URL") or "").strip() or DEFAULT_URL
    try:
        http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
        response = http.get(url)
        response.raise_for_status()
        return parse_registry(response.text)
    except RegistryUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 — every failure means the same
        raise RegistryUnavailable(f"could not read {url}: {exc}") from exc
