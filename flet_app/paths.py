from __future__ import annotations

import sys
from pathlib import Path

_FLET_APP = Path(__file__).resolve().parent
if str(_FLET_APP) not in sys.path:
    sys.path.insert(0, str(_FLET_APP))

from download_dir import *  # noqa: E402, F403

# Rădăcina repo-ului (părintele lui flet_app/) — pentru cod care încă se raportează la proiect.
PROJECT_ROOT = _FLET_APP.parent
DOWNLOADS_DIR = get_downloads_dir()
DEFAULT_DOWNLOADS_DIR = DOWNLOADS_DIR
