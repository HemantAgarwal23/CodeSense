"""GitHub pull request access for PR reviews.

Fetches the files a pull request changes (content at the PR head plus the line
numbers its diff adds) and posts findings back as a review with inline comments.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx

GITHUB_API_URL = "https://api.github.com"
REQUEST_TIMEOUT_SECONDS = 20.0
FILES_PER_PAGE = 100
MAX_FILE_PAGES = 3
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB

_PR_URL_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/pull/(?P<number>\d+)(?:/[A-Za-z0-9_./-]*)?$"
)
_HUNK_HEADER_PATTERN = re.compile(r"^@@ -\d+(?:,\d+)? \+(?P<start>\d+)(?:,\d+)? @@")


class PullRequestError(Exception):
    """Base exception for pull request review failures."""


class InvalidPullRequestUrlError(PullRequestError):
    """Raised when a URL is not a GitHub pull request URL."""


class PullRequestAccessError(PullRequestError):
    """Raised when GitHub denies access or the pull request does not exist."""


@dataclass(frozen=True)
class PullRequestRef:
    owner: str
    repo: str
    number: int

    @property
    def api_path(self) -> str:
        return f"/repos/{self.owner}/{self.repo}/pulls/{self.number}"


@dataclass
class ChangedFile:
    path: str
    content: str
    changed_lines: set[int] = field(default_factory=set)


@dataclass
class PullRequest:
    ref: PullRequestRef
    title: str
    html_url: str
    head_sha: str
    files: list[ChangedFile] = field(default_factory=list)
    supported_files_changed: int = 0


def parse_pull_request_url(url: str) -> PullRequestRef:
    """Validate a GitHub pull request URL and split it into its parts."""
    match = _PR_URL_PATTERN.match((url or "").strip())
    if not match:
        raise InvalidPullRequestUrlError(
            "Invalid pull request URL. Expected format: https://github.com/<owner>/<repo>/pull/<number>."
        )
    return PullRequestRef(match.group("owner"), match.group("repo"), int(match.group("number")))


def changed_lines_from_patch(patch: str | None) -> set[int]:
    """Return the new-file line numbers that a unified diff patch adds or modifies."""
    added: set[int] = set()
    current_line = 0
    in_hunk = False
    for raw_line in (patch or "").splitlines():
        header = _HUNK_HEADER_PATTERN.match(raw_line)
        if header:
            current_line = int(header.group("start"))
            in_hunk = True
            continue
        # "\ No newline at end of file" markers do not consume a line.
        if not in_hunk or raw_line.startswith("\\"):
            continue
        if raw_line.startswith("+"):
            added.add(current_line)
            current_line += 1
        elif not raw_line.startswith("-"):
            current_line += 1
    return added


def _client(token: str) -> httpx.AsyncClient:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "codesense-ai",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return httpx.AsyncClient(base_url=GITHUB_API_URL, headers=headers, timeout=REQUEST_TIMEOUT_SECONDS)


async def _request(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    action: str,
    **kwargs: Any,
) -> httpx.Response:
    try:
        response = await client.request(method, path, **kwargs)
    except httpx.HTTPError as exc:
        raise PullRequestError(f"Could not reach GitHub to {action}.") from exc

    if response.status_code in {401, 403, 404}:
        raise PullRequestAccessError(
            f"GitHub denied the request to {action} (HTTP {response.status_code}). "
            "Check that the pull request exists and that GITHUB_TOKEN can access it."
        )
    if response.is_error:
        raise PullRequestError(f"GitHub request to {action} failed (HTTP {response.status_code}).")
    return response


async def fetch_pull_request(
    url: str,
    token: str = "",
    supported_extensions: tuple[str, ...] = (),
    max_files: int = 50,
) -> PullRequest:
    """Load pull request metadata and up to `max_files` supported changed files."""
    ref = parse_pull_request_url(url)
    async with _client(token) as client:
        details = (await _request(client, "GET", ref.api_path, "load the pull request")).json()

        listed: list[dict[str, Any]] = []
        for page in range(1, MAX_FILE_PAGES + 1):
            response = await _request(
                client,
                "GET",
                f"{ref.api_path}/files",
                "list changed files",
                params={"per_page": FILES_PER_PAGE, "page": page},
            )
            batch = response.json()
            listed.extend(batch)
            if len(batch) < FILES_PER_PAGE:
                break

        candidates = [
            item
            for item in listed
            if item.get("status") != "removed"
            and (
                not supported_extensions
                or PurePosixPath(str(item.get("filename", ""))).suffix.lower() in supported_extensions
            )
        ]
        pull_request = PullRequest(
            ref=ref,
            title=str(details.get("title") or ""),
            html_url=str(details.get("html_url") or url),
            head_sha=str(details["head"]["sha"]),
            supported_files_changed=len(candidates),
        )

        for item in candidates[:max_files]:
            path = str(item["filename"])
            response = await _request(
                client,
                "GET",
                f"/repos/{ref.owner}/{ref.repo}/contents/{quote(path)}",
                f"download {path}",
                params={"ref": pull_request.head_sha},
                headers={"Accept": "application/vnd.github.raw+json"},
            )
            if len(response.content) > MAX_FILE_SIZE_BYTES:
                continue
            pull_request.files.append(
                ChangedFile(
                    path=path,
                    content=response.text,
                    changed_lines=changed_lines_from_patch(item.get("patch")),
                )
            )

    return pull_request


async def post_review_comments(
    pull_request: PullRequest,
    comments: list[dict[str, Any]],
    token: str,
) -> int:
    """Post findings as one GitHub review with inline comments. Returns the number posted."""
    if not comments:
        return 0
    payload = {
        "commit_id": pull_request.head_sha,
        "event": "COMMENT",
        "body": f"CodeSense AI found {len(comments)} issue(s) on lines changed in this pull request.",
        "comments": [
            {"path": comment["path"], "line": comment["line"], "side": "RIGHT", "body": comment["body"]}
            for comment in comments
        ],
    }
    async with _client(token) as client:
        await _request(client, "POST", f"{pull_request.ref.api_path}/reviews", "post review comments", json=payload)
    return len(comments)
