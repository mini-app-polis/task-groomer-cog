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
import time
from collections.abc import Callable

import httpx
import yaml

DEFAULT_URL = (
    "https://raw.githubusercontent.com/mini-app-polis/ecosystem-standards/"
    "main/ecosystem.yaml"
)

TIMEOUT_SECONDS = 20.0

#: Reads of the registry before the run gives up.
ATTEMPTS = 3

#: Answers worth asking again: rate limits and the server's own failures.
_TRANSIENT = frozenset({429, 500, 502, 503, 504})


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


def load_registry(
    *,
    client: httpx.Client | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    """Read the registry. Raises :class:`RegistryUnavailable` on any failure.

    A transient failure (no answer, a rate limit, a 5xx) is tried again, up
    to :data:`ATTEMPTS` reads in all.
    """
    url = (os.environ.get("ECOSYSTEM_YAML_URL") or "").strip() or DEFAULT_URL
    for attempt in range(1, ATTEMPTS + 1):
        try:
            http = client or httpx.Client(timeout=TIMEOUT_SECONDS)
            response = http.get(url)
            if response.status_code in _TRANSIENT and attempt < ATTEMPTS:
                sleep(2.0**attempt)
                continue
            response.raise_for_status()
            return parse_registry(response.text)
        except RegistryUnavailable:
            raise
        except httpx.TransportError as exc:
            if attempt == ATTEMPTS:
                raise RegistryUnavailable(f"could not read {url}: {exc}") from exc
            sleep(2.0**attempt)
        except Exception as exc:  # noqa: BLE001 — every failure means the same
            raise RegistryUnavailable(f"could not read {url}: {exc}") from exc
    raise RegistryUnavailable(f"could not read {url}")  # pragma: no cover
