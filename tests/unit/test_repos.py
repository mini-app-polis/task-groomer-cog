from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from task_groomer_cog.repos import RepoError, Repos

FILES = {
    "README.md": "# infra\nTerraform for the fleet.\n",
    "cogs.tf": "\n".join(f'module "cog{i}" {{}}' for i in range(500)),
    "modules/cog-worker/main.tf": 'resource "aws_sqs_queue" "jobs" {}\n',
    "modules/cog-worker/variables.tf": 'variable "name" {}\n',
    "node_modules/x/index.js": "ignored",
    "logo.png": "binary",
}


def _zip(root: str = "mini-app-polis-infra-abc123/") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for path, text in FILES.items():
            archive.writestr(root + path, text)
    return buffer.getvalue()


def _repos(*, dev: bool = True, seen: list[str] | None = None) -> Repos:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request.url.path)
        if request.url.path.endswith("/dev") and not dev:
            return httpx.Response(404)
        return httpx.Response(200, content=_zip())

    return Repos(
        {"infra", "watcher-cog"},
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def test_list_files_shows_two_levels_and_skips_noise() -> None:
    out = _repos().list_files("infra")
    assert out.splitlines()[0] == "infra@dev:/"
    assert "modules/cog-worker/…" in out
    assert "README.md" in out
    assert "node_modules" not in out
    assert "logo.png" not in out


def test_read_file_numbers_lines_and_pages() -> None:
    out = _repos().read_file("infra", "cogs.tf")
    assert '1: module "cog0" {}' in out
    assert "(lines 1-400 of 500; read on with start_line)" in out
    rest = _repos().read_file("infra", "cogs.tf", start_line=401)
    assert '401: module "cog400" {}' in rest
    assert "read on" not in rest


def test_search_finds_lines_with_their_place() -> None:
    out = _repos().search("infra", "aws_sqs_queue")
    assert "modules/cog-worker/main.tf:1:" in out


def test_a_bad_pattern_is_searched_for_literally() -> None:
    assert "no matches" in _repos().search("infra", "([")


def test_a_repository_is_fetched_once_per_run() -> None:
    seen: list[str] = []
    repos = _repos(seen=seen)
    repos.list_files("infra")
    repos.read_file("infra", "README.md")
    assert seen == ["/repos/mini-app-polis/infra/zipball/dev"]


def test_a_repository_without_dev_is_read_on_main() -> None:
    repos = _repos(dev=False)
    assert repos.branch("infra") == "main"


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda r: r.list_files("secret-repo"), "not a repository in the registry"),
        (lambda r: r.read_file("infra", "nope.txt"), "has no file nope.txt"),
        (lambda r: r.read_file("infra", "../../etc/passwd"), "has no file etc/passwd"),
        (lambda r: r.list_files("infra", "missing"), "has nothing at missing"),
    ],
)
def test_what_cannot_be_read_says_why(call, message: str) -> None:
    with pytest.raises(RepoError, match=message):
        call(_repos())


def test_a_github_failure_is_a_tool_error() -> None:
    repos = Repos(
        {"infra"},
        client=httpx.Client(
            transport=httpx.MockTransport(lambda r: httpx.Response(500))
        ),
    )
    with pytest.raises(RepoError, match="GitHub answered 500"):
        repos.list_files("infra")
