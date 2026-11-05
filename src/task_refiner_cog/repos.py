"""Read-only access to the fleet's repositories, for the model's tools.

A repository is fetched once per run as a GitHub zipball and read in memory:
nothing is written to disk, and nothing can be pushed. The ``dev`` branch is
read, where the fleet's work lands first; a repository without one is read
on ``main``.

Only repositories in the registry can be opened, and paths are resolved
inside the archive, so a tool call cannot reach anything else.

  GITHUB_TOKEN  read access to the org's repositories. Every fleet
                repository is public, so it is optional, but without it
                GitHub allows 60 requests an hour.
"""

from __future__ import annotations

import io
import os
import posixpath
import re
import time
import zipfile
from collections.abc import Callable, Collection

import httpx

ORG = "mini-app-polis"
BRANCHES: tuple[str, ...] = ("dev", "main")
TIMEOUT_SECONDS = 60.0

#: Downloads of one archive before the tool call fails. A blip at GitHub
#: would otherwise end a paid run that has already spent its tokens.
ATTEMPTS = 3

#: Answers worth asking again: rate limits and GitHub's own failures.
TRANSIENT = frozenset({429, 500, 502, 503, 504})

#: What a tool result may hold, so one call cannot swamp the context.
MAX_LIST_ENTRIES = 300
MAX_READ_LINES = 400
MAX_READ_CHARS = 40_000
MAX_SEARCH_MATCHES = 40
MAX_SEARCHED_FILE_BYTES = 500_000

#: Never listed or searched: generated, vendored or binary.
_SKIPPED = re.compile(
    r"(^|/)(\.git|node_modules|\.venv|dist|build|__pycache__|\.mypy_cache|"
    r"\.ruff_cache|\.pytest_cache)(/|$)|\.(png|jpe?g|gif|webp|ico|pdf|zip|"
    r"woff2?|ttf|mp3|mp4|lock)$|(^|/)(uv\.lock|package-lock\.json|pnpm-lock\.yaml)$"
)


class RepoError(Exception):
    """A tool call that cannot be answered. Its text goes back to the model."""


class Repos:
    """The repositories one run has opened."""

    def __init__(
        self,
        registry: Collection[str],
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._registry = frozenset(registry)
        self._client = client
        self._sleep = sleep
        self._archives: dict[str, tuple[str, zipfile.ZipFile, str]] = {}

    def _http(self) -> httpx.Client:
        # no-retry: this only builds the client; it makes no request. The
        # archive download goes through _download, which tries again.
        if self._client is None:
            self._client = httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
        return self._client

    def _open(self, repo: str) -> tuple[str, zipfile.ZipFile, str]:
        """The repository's archive, its root inside it, and the branch read."""
        if repo not in self._registry:
            raise RepoError(
                f"{repo} is not a repository in the registry. "
                "Use a name from the registry list."
            )
        if repo in self._archives:
            return self._archives[repo]
        headers = {"Accept": "application/vnd.github+json"}
        token = (os.environ.get("GITHUB_TOKEN") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        for branch in BRANCHES:
            url = f"https://api.github.com/repos/{ORG}/{repo}/zipball/{branch}"
            response = self._download(url, headers)
            if response.status_code == 404:
                continue
            if response.status_code != 200:
                raise RepoError(
                    f"GitHub answered {response.status_code} for {repo}@{branch}"
                )
            archive = zipfile.ZipFile(io.BytesIO(response.content))
            names = archive.namelist()
            root = names[0].split("/", 1)[0] + "/" if names else ""
            self._archives[repo] = (root, archive, branch)
            return self._archives[repo]
        raise RepoError(f"{repo} has neither a dev nor a main branch on GitHub")

    def _download(self, url: str, headers: dict[str, str]) -> httpx.Response:
        """One archive, tried again on a transient failure."""
        for attempt in range(1, ATTEMPTS + 1):
            try:
                response = self._http().get(url, headers=headers)
            except httpx.TransportError as exc:
                if attempt == ATTEMPTS:
                    raise RepoError(f"GitHub could not be reached: {exc}") from exc
            else:
                if response.status_code not in TRANSIENT or attempt == ATTEMPTS:
                    return response
            self._sleep(2.0**attempt)
        raise RepoError("GitHub could not be reached")  # pragma: no cover

    @staticmethod
    def _clean(path: str) -> str:
        cleaned = posixpath.normpath("/" + (path or "").strip()).lstrip("/")
        return "" if cleaned == "." else cleaned

    def branch(self, repo: str) -> str:
        """The branch the repository was read from: dev, or main without one."""
        return self._open(repo)[2]

    def list_files(self, repo: str, path: str = "") -> str:
        """Files and directories under ``path``, two levels deep."""
        root, archive, branch = self._open(repo)
        base = self._clean(path)
        prefix = root + (base + "/" if base else "")
        entries: set[str] = set()
        for name in archive.namelist():
            if not name.startswith(prefix) or name == prefix:
                continue
            rest = name[len(prefix) :]
            if _SKIPPED.search(rest):
                continue
            parts = rest.rstrip("/").split("/")
            if len(parts) > 2:
                entries.add("/".join(parts[:2]) + "/…")
            else:
                entries.add(rest)
        if not entries:
            raise RepoError(f"{repo}@{branch} has nothing at {base or '/'}")
        shown = sorted(entries)[:MAX_LIST_ENTRIES]
        more = len(entries) - len(shown)
        tail = f"\n(+{more} more)" if more > 0 else ""
        return f"{repo}@{branch}:{base or '/'}\n" + "\n".join(shown) + tail

    def read_file(self, repo: str, path: str, start_line: int = 1) -> str:
        """Up to ``MAX_READ_LINES`` lines of one file, from ``start_line``."""
        root, archive, branch = self._open(repo)
        clean = self._clean(path)
        try:
            raw = archive.read(root + clean)
        except KeyError:
            raise RepoError(f"{repo}@{branch} has no file {clean}") from None
        text = raw.decode("utf-8", errors="replace")
        lines = text.splitlines()
        start = max(1, start_line)
        chunk = lines[start - 1 : start - 1 + MAX_READ_LINES]
        body = "\n".join(f"{start + i}: {line}" for i, line in enumerate(chunk))
        body = body[:MAX_READ_CHARS]
        end = start + len(chunk) - 1
        note = (
            f"\n(lines {start}-{end} of {len(lines)}; read on with start_line)"
            if end < len(lines)
            else ""
        )
        return f"{repo}@{branch}:{clean}\n{body}{note}"

    def search(self, repo: str, pattern: str, path: str = "") -> str:
        """Lines matching ``pattern`` (a regular expression), with their place."""
        root, archive, branch = self._open(repo)
        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            regex = re.compile(re.escape(pattern), re.IGNORECASE)
        base = self._clean(path)
        prefix = root + (base + "/" if base else "")
        matches: list[str] = []
        for info in archive.infolist():
            name = info.filename
            if (
                info.is_dir()
                or not name.startswith(prefix)
                or _SKIPPED.search(name[len(root) :])
                or info.file_size > MAX_SEARCHED_FILE_BYTES
            ):
                continue
            text = archive.read(info).decode("utf-8", errors="replace")
            for number, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    matches.append(
                        f"{name[len(root) :]}:{number}: {line.strip()[:200]}"
                    )
                    if len(matches) >= MAX_SEARCH_MATCHES:
                        return (
                            f"{repo}@{branch} /{pattern}/ (first {len(matches)})\n"
                            + "\n".join(matches)
                        )
        if not matches:
            return f"{repo}@{branch} /{pattern}/: no matches"
        return f"{repo}@{branch} /{pattern}/\n" + "\n".join(matches)
