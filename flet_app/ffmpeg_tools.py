from __future__ import annotations

import os
import shutil
import sys
from functools import lru_cache
from pathlib import Path


def _is_executable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _tool_names(name: str) -> tuple[str, ...]:
    return (f"{name}.exe", name) if os.name == "nt" else (name,)


def _candidate_dirs() -> list[Path]:
    dirs: list[Path] = []

    def add(path: Path | None) -> None:
        if path is None:
            return
        try:
            p = path.expanduser().resolve()
        except OSError:
            return
        if p not in dirs:
            dirs.append(p)

    for env_name in ("DLPULSE_FFMPEG_DIR", "FFMPEG_DIR"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw:
            add(Path(raw))

    exe_dir = Path(sys.executable).resolve().parent
    module_dir = Path(__file__).resolve().parent
    for base in (exe_dir, module_dir, module_dir.parent):
        add(base)
        add(base / "bin")
        add(base / "usr" / "bin")
        add(base / "Resources" / "bin")

    # macOS app bundles usually run from Contents/MacOS; bundled tools live better
    # under Contents/Resources/bin.
    for parent in exe_dir.parents:
        if parent.name == "Contents":
            add(parent / "Resources" / "bin")
            add(parent / "MacOS")
            break

    return dirs


def apply_bundled_tool_path() -> None:
    """Expose bundled command-line tools (ffmpeg/ffprobe) to subprocess users."""
    existing = os.environ.get("PATH", "")
    parts = [p for p in existing.split(os.pathsep) if p]
    prepend: list[str] = []
    for directory in _candidate_dirs():
        if not directory.is_dir():
            continue
        if any((directory / name).is_file() for name in (*_tool_names("ffmpeg"), *_tool_names("ffprobe"))):
            s = str(directory)
            if s not in prepend and s not in parts:
                prepend.append(s)
    if prepend:
        os.environ["PATH"] = os.pathsep.join([*prepend, *parts])


def _find_named_tool(name: str) -> str | None:
    for directory in _candidate_dirs():
        for exe_name in _tool_names(name):
            candidate = directory / exe_name
            if _is_executable(candidate):
                return str(candidate)
    found = shutil.which(name)
    return found


@lru_cache(maxsize=1)
def find_ffmpeg() -> str | None:
    """Return the best ffmpeg executable available to this app."""
    for env_name in ("DLPULSE_FFMPEG", "FFMPEG_BINARY", "IMAGEIO_FFMPEG_EXE"):
        raw = (os.environ.get(env_name) or "").strip()
        if raw and _is_executable(Path(raw)):
            return str(Path(raw).expanduser().resolve())

    apply_bundled_tool_path()
    named = _find_named_tool("ffmpeg")
    if named:
        return named

    try:
        import imageio_ffmpeg

        imageio_exe = Path(imageio_ffmpeg.get_ffmpeg_exe())
        if _is_executable(imageio_exe):
            os.environ.setdefault("IMAGEIO_FFMPEG_EXE", str(imageio_exe))
            return str(imageio_exe)
    except Exception:
        return None
    return None


def ffmpeg_available() -> bool:
    return bool(find_ffmpeg())


def ffmpeg_location_for_ytdlp() -> str | None:
    """yt-dlp accepts either a directory or a concrete ffmpeg executable path."""
    return find_ffmpeg()
