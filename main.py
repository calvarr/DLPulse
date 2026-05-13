from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path


def _run_with_flet_cli() -> None:
    if os.environ.get("DLPULSE_USE_FLET_CLI") != "1":
        return

    root = Path(__file__).resolve().parent
    flet_exe = root / ".venv" / "bin" / "flet"
    if not flet_exe.exists():
        return

    env = os.environ.copy()
    if env.get("DLPULSE_FLET_RUN") == "1":
        return
    env["DLPULSE_FLET_RUN"] = "1"
    env.setdefault("FLET_DESKTOP_FLAVOR", "full")
    env.pop("GDK_GL", None)
    os.execve(str(flet_exe), [str(flet_exe), "run", "flet_app/main.py", *sys.argv[1:]], env)


if __name__ == "__main__":
    _run_with_flet_cli()
    runpy.run_module("flet_app.main", run_name="__main__")
