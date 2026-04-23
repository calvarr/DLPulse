"""Compare this build's Git commit with github.com/main (GitHub API, unauthenticated)."""

from __future__ import annotations

import importlib.metadata
import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

GITHUB_REPO = "calvarr/DLPulse"
GITHUB_PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
# Public repo: users can download builds without a GitHub account.
GITHUB_RELEASES_URL = f"{GITHUB_PROJECT_URL}/releases"
_API_BASE = f"https://api.github.com/repos/{GITHUB_REPO}"
_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "DLPulseFlet/1.0",
}


@dataclass(frozen=True)
class AppGitHubUpdateInfo:
    """Whether to show an in-app banner about newer commits on GitHub."""

    show_banner: bool
    message: str
    remote_main_sha: str | None


def _flet_app_dir() -> Path:
    return Path(__file__).resolve().parent


def get_app_package_version() -> str:
    """PEP 621 / wheel version of the ``dlpulse`` distribution (e.g. ``1.0.2``)."""
    try:
        return importlib.metadata.version("dlpulse")
    except importlib.metadata.PackageNotFoundError:
        return "0.0.0"


def commit_page_url(full_sha: str) -> str:
    """``https://github.com/…/commit/<sha>`` for the tree this install was built from."""
    t = (full_sha or "").strip().lower()[:40]
    if len(t) < 7 or any(c not in "0123456789abcdef" for c in t):
        return GITHUB_PROJECT_URL
    return f"{GITHUB_PROJECT_URL}/commit/{t}"


def get_local_commit_sha() -> str | None:
    """SHA embedded at CI build time, or ``git rev-parse HEAD`` when developing from a clone."""
    env_raw = (os.environ.get("DLPULSE_BUILD_COMMIT") or "").strip()
    if env_raw:
        token = env_raw.split()[0]
        if len(token) >= 7 and token.lower() not in ("unknown", "none", "null"):
            if all(c in "0123456789abcdefABCDEF" for c in token[:40]):
                return token[:40].lower()

    marker = _flet_app_dir() / "build_commit.txt"
    if marker.is_file():
        raw = marker.read_text(encoding="utf-8").strip()
        token = raw.split()[0] if raw else ""
        if len(token) >= 7 and token.lower() not in ("unknown", "none", "null"):
            return token[:40].lower()

    base = _flet_app_dir()
    for d in (base.parent, *base.parents):
        if not (d / ".git").exists():
            continue
        try:
            r = subprocess.run(
                ["git", "-C", str(d), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if r.returncode == 0 and (sha := r.stdout.strip()):
                return sha[:40].lower()
        except (OSError, subprocess.SubprocessError):
            continue
    return None


def _http_json(url: str, timeout: float = 18.0) -> dict | None:
    req = Request(url, headers=_HEADERS, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except (HTTPError, URLError, OSError, json.JSONDecodeError, TypeError):
        return None


def _branch_head_sha(branch: str = "main", timeout: float = 18.0) -> str | None:
    data = _http_json(f"{_API_BASE}/commits/{branch}", timeout=timeout)
    if not data:
        return None
    sha = (data.get("sha") or "").strip()
    return sha[:40] if len(sha) >= 7 else None


def check_app_github_update(timeout: float = 20.0) -> AppGitHubUpdateInfo:
    """
    If this build's commit is behind ``main`` on GitHub, return a banner message.
    If local SHA cannot be determined, returns ``show_banner=False`` (no noise).
    """
    local = get_local_commit_sha()
    if not local:
        return AppGitHubUpdateInfo(False, "", None)

    main_sha = _branch_head_sha("main", timeout=timeout)
    if not main_sha:
        return AppGitHubUpdateInfo(False, "", None)

    if local.lower() == main_sha.lower():
        return AppGitHubUpdateInfo(False, "", main_sha)

    compare_url = f"{_API_BASE}/compare/{local}...main"
    data = _http_json(compare_url, timeout=timeout)
    if not data:
        # Fallback: we already know tips differ; avoid claiming a commit count.
        msg = (
            "The default branch on GitHub may be newer than this build. "
            "Open the repository to pull the latest changes or download a fresh build."
        )
        return AppGitHubUpdateInfo(True, msg, main_sha)

    behind = int(data.get("behind_by") or 0)
    ahead = int(data.get("ahead_by") or 0)
    status = str(data.get("status") or "").lower()

    if status == "identical" or (behind == 0 and ahead == 0):
        return AppGitHubUpdateInfo(False, "", main_sha)
    if behind > 0:
        plural = "commit" if behind == 1 else "commits"
        msg = (
            f"GitHub has new changes: branch main is {behind} {plural} ahead of this build. "
            "Open the repository to update or download a newer build."
        )
        return AppGitHubUpdateInfo(True, msg, main_sha)
    if ahead > 0 and behind == 0:
        return AppGitHubUpdateInfo(False, "", main_sha)

    msg = (
        f"This build and github.com/main have diverged (ahead {ahead}, behind {behind}). "
        "See the repository for details."
    )
    return AppGitHubUpdateInfo(True, msg, main_sha)
