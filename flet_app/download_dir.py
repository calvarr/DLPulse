from __future__ import annotations

import json
from pathlib import Path

SETTINGS_JSON = Path(__file__).resolve().parent / ".yt_flet_settings.json"

_cached_root: Path | None = None


def _default_root() -> Path:
    return Path(__file__).resolve().parent / "downloads"


def _read_settings() -> dict:
    try:
        if not SETTINGS_JSON.is_file():
            return {}
        return json.loads(SETTINGS_JSON.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_settings(data: dict) -> None:
    try:
        SETTINGS_JSON.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError:
        pass


def _load_saved_dir() -> Path | None:
    try:
        data = _read_settings()
        raw = (data.get("download_dir") or data.get("path") or "").strip()
        if not raw:
            return None
        return Path(raw).expanduser().resolve()
    except (OSError, ValueError):
        return None


def get_downloads_dir() -> Path:
    global _cached_root
    if _cached_root is not None:
        return _cached_root
    loaded = _load_saved_dir()
    _cached_root = loaded if loaded else _default_root()
    try:
        _cached_root.mkdir(parents=True, exist_ok=True)
    except OSError:
        _cached_root = _default_root()
        _cached_root.mkdir(parents=True, exist_ok=True)
    return _cached_root


def set_downloads_dir(path: Path) -> Path:
    global _cached_root
    p = path.expanduser().resolve()
    p.mkdir(parents=True, exist_ok=True)
    _cached_root = p
    data = _read_settings()
    data["download_dir"] = str(p)
    _write_settings(data)
    return p


def get_video_player_command() -> str:
    return (_read_settings().get("video_player") or "").strip()


def set_video_player_command(cmd: str) -> None:
    data = _read_settings()
    data["video_player"] = cmd.strip()
    if "download_dir" not in data:
        data["download_dir"] = str(get_downloads_dir())
    _write_settings(data)


def get_audio_player_command() -> str:
    data = _read_settings()
    a = (data.get("audio_player") or "").strip()
    if a:
        return a
    return (data.get("video_player") or "").strip()


def set_audio_player_command(cmd: str) -> None:
    data = _read_settings()
    data["audio_player"] = cmd.strip()
    if "download_dir" not in data:
        data["download_dir"] = str(get_downloads_dir())
    _write_settings(data)


def get_cast_discovery_wait_s() -> float:
    raw = _read_settings().get("cast_discovery_wait_s", 3)
    try:
        v = float(raw) if not isinstance(raw, str) else float(raw.strip())
        return max(0.5, min(v, 120.0))
    except (TypeError, ValueError):
        return 3.0


def set_cast_discovery_wait_s(seconds: float) -> None:
    data = _read_settings()
    data["cast_discovery_wait_s"] = max(0.5, min(float(seconds), 120.0))
    if "download_dir" not in data:
        data["download_dir"] = str(get_downloads_dir())
    _write_settings(data)


# — yt-dlp PyPI check cadence (used by Flet app Settings)
_YTDLP_CHECK_EVERY_N_LAUNCHES = 5
_YTDLP_RECHECK_DAYS = 7


def bump_app_launch_count() -> int:
    """Increment launch counter; returns new value."""
    data = _read_settings()
    n = int(data.get("app_launch_count", 0)) + 1
    data["app_launch_count"] = n
    _write_settings(data)
    return n


def should_check_ytdlp_pypi() -> bool:
    """True every N launches, if never checked, or if last PyPI check is older than ~7 days."""
    import time

    data = _read_settings()
    n = int(data.get("app_launch_count", 0))
    last = float(data.get("ytdlp_pypi_last_check_ts", 0) or 0)
    now = time.time()
    if n > 0 and n % _YTDLP_CHECK_EVERY_N_LAUNCHES == 0:
        return True
    if last <= 0:
        return True
    if now - last >= _YTDLP_RECHECK_DAYS * 86400:
        return True
    return False


def mark_ytdlp_pypi_checked() -> None:
    import time

    data = _read_settings()
    data["ytdlp_pypi_last_check_ts"] = time.time()
    _write_settings(data)


def get_github_update_dismissed_main_sha() -> str | None:
    """If set, banner for that ``main`` tip was dismissed by the user."""
    s = (_read_settings().get("github_update_dismissed_main_sha") or "").strip()
    return s[:40] if len(s) >= 7 else None


def set_github_update_dismissed_main_sha(sha: str) -> None:
    data = _read_settings()
    data["github_update_dismissed_main_sha"] = (sha or "").strip()[:40]
    if "download_dir" not in data:
        data["download_dir"] = str(get_downloads_dir())
    _write_settings(data)
