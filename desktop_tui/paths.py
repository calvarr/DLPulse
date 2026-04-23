"""Repo root + flet_app on sys.path (shared modules: download_dir, yt_core)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_FLET = _ROOT / "flet_app"
if str(_FLET) not in sys.path:
    sys.path.insert(0, str(_FLET))
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from download_dir import get_downloads_dir  # noqa: E402

PROJECT_ROOT = _ROOT
DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_DOWNLOADS_DIR = DOWNLOADS_DIR
