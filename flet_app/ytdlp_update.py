"""PyPI version check and in-app pip upgrade for yt-dlp."""

from __future__ import annotations

import importlib
import subprocess
import sys


def get_installed_ytdlp_version() -> str:
    try:
        import yt_dlp

        return str(getattr(yt_dlp.version, "__version__", "?"))
    except Exception as e:
        return f"? ({e})"


def fetch_pypi_latest_ytdlp_version(timeout: float = 18.0) -> str | None:
    import json
    import urllib.error
    import urllib.request

    req = urllib.request.Request(
        "https://pypi.org/pypi/yt-dlp/json",
        headers={"User-Agent": "DLPulseFlet/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        return (data.get("info") or {}).get("version")
    except (urllib.error.URLError, OSError, json.JSONDecodeError, TypeError):
        return None


def is_newer_pypi_version(latest: str, installed: str) -> bool:
    if not latest or not installed or installed.startswith("?"):
        return False
    try:
        from packaging.version import Version

        return Version(latest) > Version(installed)
    except Exception:
        return latest != installed


def pip_upgrade_ytdlp() -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=420,
        )
        tail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-2500:]
        return r.returncode == 0, tail or ("OK" if r.returncode == 0 else "pip failed")
    except Exception as e:
        return False, str(e)


def reload_ytdlp_module() -> str:
    try:
        import yt_dlp

        importlib.reload(yt_dlp)
        if hasattr(yt_dlp, "version"):
            importlib.reload(yt_dlp.version)
        return str(getattr(yt_dlp.version, "__version__", "?"))
    except Exception as e:
        return f"? ({e})"
