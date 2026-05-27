"""GitHub repository processing utilities.

This module validates GitHub URLs, clones repositories safely to a temporary
location, extracts allowed source files, and guarantees clone directory cleanup.
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

from git import Repo
from git.exc import GitCommandError

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".py", ".js", ".cpp"}
MAX_FILE_SIZE_BYTES = 1 * 1024 * 1024  # 1 MB
DEFAULT_CLONE_TIMEOUT_SECONDS = 60

_GITHUB_REPO_PATTERN = re.compile(
    r"^https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?/?$"
)


class RepositoryProcessingError(Exception):
    """Base exception for repository processing failures."""


class InvalidGitHubUrlError(RepositoryProcessingError):
    """Raised when a repository URL is not a valid GitHub repository URL."""


class RepositoryAccessError(RepositoryProcessingError):
    """Raised when repository access is denied or repository does not exist."""


class RepositoryCloneError(RepositoryProcessingError):
    """Raised when cloning fails for any reason other than access failures."""


def validate_github_repo_url(repo_url: str) -> str:
    """Validate and normalize a GitHub repository URL.

    Args:
        repo_url: Input URL expected in the form:
            https://github.com/<owner>/<repo>[.git]

    Returns:
        A normalized clone URL with `.git` suffix.

    Raises:
        InvalidGitHubUrlError: If the URL format is invalid.
    """
    raw_url = repo_url.strip()
    match = _GITHUB_REPO_PATTERN.match(raw_url)
    if not match:
        raise InvalidGitHubUrlError(
            "Invalid GitHub URL. Expected format: "
            "https://github.com/<owner>/<repo>."
        )

    owner = match.group("owner")
    repo = match.group("repo")
    normalized = f"https://github.com/{owner}/{repo}.git"
    return normalized


def _clone_repository(
    clone_url: str,
    destination: Path,
    branch: str,
    timeout_seconds: int,
) -> None:
    """Clone repository into destination with timeout and no interactive prompts."""
    env = {
        "GIT_TERMINAL_PROMPT": "0",
        "GCM_INTERACTIVE": "Never",
    }

    try:
        Repo.clone_from(
            clone_url,
            destination.as_posix(),
            depth=1,
            branch=branch,
            env=env,
            kill_after_timeout=timeout_seconds,
        )
    except GitCommandError as exc:
        stderr = (exc.stderr or "").lower()
        message = str(exc).lower()
        if (
            "repository not found" in stderr
            or "authentication failed" in stderr
            or "could not read from remote repository" in stderr
            or "repository not found" in message
        ):
            raise RepositoryAccessError(
                "Repository is private, inaccessible, or does not exist."
            ) from exc
        if "timed out" in stderr or "timed out" in message:
            raise RepositoryCloneError("Repository clone timed out.") from exc
        raise RepositoryCloneError("Repository clone failed.") from exc
    except Exception as exc:  # Defensive catch for process stability.
        raise RepositoryCloneError("Unexpected error during repository clone.") from exc


def _should_skip_dir(dir_name: str) -> bool:
    """Return True if a directory should not be traversed."""
    return dir_name == "node_modules" or dir_name == ".git" or dir_name.startswith(".")


def _collect_source_files(repo_root: Path) -> list[Path]:
    """Collect source files matching policy under a cloned repository."""
    collected: list[Path] = []

    for current_root, dir_names, file_names in os.walk(repo_root):
        dir_names[:] = [d for d in dir_names if not _should_skip_dir(d)]
        root_path = Path(current_root)

        for file_name in file_names:
            file_path = root_path / file_name
            if file_path.suffix.lower() not in ALLOWED_EXTENSIONS:
                continue

            try:
                if file_path.stat().st_size > MAX_FILE_SIZE_BYTES:
                    continue
            except OSError:
                logger.warning("Skipping unreadable file: %s", file_path)
                continue

            collected.append(file_path.resolve())

    return collected


def _copy_files_to_output(
    source_files: list[Path],
    repo_root: Path,
    output_root: Path,
) -> list[str]:
    """Copy source files to output directory preserving repository structure."""
    output_root.mkdir(parents=True, exist_ok=True)
    output_paths: list[str] = []

    for source_file in source_files:
        relative_path = source_file.relative_to(repo_root)
        destination_file = (output_root / relative_path).resolve()
        destination_file.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, destination_file)
        output_paths.append(str(destination_file))

    return output_paths


def process_github_repository(
    repo_url: str,
    branch: str = "main",
    output_dir: str | None = None,
    clone_timeout_seconds: int = DEFAULT_CLONE_TIMEOUT_SECONDS,
) -> list[str]:
    """Clone and process a GitHub repository safely.

    Behavior:
    - Validates GitHub URL format.
    - Clones repository into a temporary directory.
    - Extracts only allowed source files.
    - Skips hidden directories, `.git`, and `node_modules`.
    - Skips files larger than 1 MB.
    - Returns absolute paths of copied files in `output_dir`.

    Notes:
    - The clone temporary directory is always deleted, including failure paths.
    - If `output_dir` is omitted, a new temporary output directory is created and
      returned file paths point there.

    Raises:
        InvalidGitHubUrlError
        RepositoryAccessError
        RepositoryCloneError
    """
    clone_url = validate_github_repo_url(repo_url)
    output_root = Path(output_dir).resolve() if output_dir else Path(
        tempfile.mkdtemp(prefix="repo_sources_")
    ).resolve()

    logger.info("Processing repository: %s (branch=%s)", clone_url, branch)
    logger.debug("Output directory: %s", output_root)

    try:
        with tempfile.TemporaryDirectory(prefix="repo_clone_") as temp_clone_dir:
            clone_root = Path(temp_clone_dir).resolve() / "repo"
            _clone_repository(
                clone_url=clone_url,
                destination=clone_root,
                branch=branch,
                timeout_seconds=clone_timeout_seconds,
            )
            source_files = _collect_source_files(clone_root)
            output_paths = _copy_files_to_output(source_files, clone_root, output_root)
            logger.info("Repository processed successfully. Files collected: %d", len(output_paths))
            return output_paths
    except RepositoryProcessingError:
        raise
    except Exception as exc:  # Defensive catch for system reliability.
        logger.exception("Unexpected repository processing failure.")
        raise RepositoryProcessingError("Unexpected repository processing failure.") from exc


async def fetch_repo_code(repo_url: str, branch: str = "main") -> str:
    """Compatibility helper for services that require code as a single string."""
    with tempfile.TemporaryDirectory(prefix="repo_text_") as output_dir:
        file_paths = process_github_repository(
            repo_url=repo_url,
            branch=branch,
            output_dir=output_dir,
        )

        chunks: list[str] = []
        for file_path in file_paths:
            path_obj = Path(file_path)
            try:
                content = path_obj.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                logger.warning("Unable to read file during aggregation: %s", file_path)
                continue
            chunks.append(f"// FILE: {file_path}\n{content}")

        return "\n\n".join(chunks)
