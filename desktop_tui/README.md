# DLPulse — desktop (CLI + optional TUI)

Same repo as `yt_core.py`. **Does not use a browser** for anything.

## Interactive CLI (`android_cli.py`) — recommended

Interactive menus (search, URL/playlist, library, Chromecast, yt-dlp check), **no browser**, **no Textual**:

```bash
python desktop_tui/android_cli.py
```

Main menu includes **[6] Stop casting (last device)** — stops the last TV/speaker you cast to without scanning the network again. Same format presets as `yt_core` / Android (`FORMAT_PRESETS`).

## Scriptable CLI (`pure_cli.py`)

Subcommands for automation (no menu):

```bash
python desktop_tui/pure_cli.py devices
python desktop_tui/pure_cli.py library
python desktop_tui/pure_cli.py search "song title"
python desktop_tui/pure_cli.py download "https://youtube.com/watch?v=..." --format 0
python desktop_tui/pure_cli.py cast "job-uuid/file.mp4" --index 0
python desktop_tui/pure_cli.py stop --index 0
```

**Chromecast** = mDNS on the LAN, no browser.

## Optional: Textual UI (`app.py`)

Tabs in the terminal (still **no browser**; Textual is a console UI, not a web window).

Same main flows as Android: search, URL/playlist, library, Chromecast.

## Requirements

- Python 3.10+ (recent PyChromecast may need a current Python)
- `ffmpeg` on PATH (for yt-dlp, same as the rest of the project)
- LAN: PC and Chromecast on the same Wi‑Fi for casting

## Install

From the project root `yt/`:

```bash
source .venv/bin/activate   # or create a new venv
pip install -r desktop_tui/requirements.txt
```

## Run

Plain CLI:

```bash
cd /path/to/yt
python desktop_tui/pure_cli.py --help
```

TUI (Textual):

```bash
python desktop_tui/app.py
```

## Usage

| Tab | Role |
|-----|------|
| **Search** | Query → tick results → format → **Download selected** |
| **URL / playlist** | **Inspect** URL → first 30 titles in log (like `pure_cli info`) → tick rows → download; single video: **Download URL** |
| **Library** | Files under `downloads/` (job subfolders like the web app) → rename, delete, open folder |
| **Chromecast** | Matches **`pure_cli`**: optional **Discovery wait** (seconds), table shows **Host:port** + log lines like `devices`. Optional **name filter** = `cast --name`. **Start casting** = table row *or* name match. **Stop last device** = session memory (no rescan). **Stop casting (selected)** = pick row, like `stop --index`. |

Files are stored in **`../downloads/`** (project root), shared with CLI and the web server.

## Chromecast

- The HTTP server serves files with **Range** (Default Media Receiver–friendly).
- The URL the TV uses is `http://<LAN_IP>:<port>/media/...` — it must be reachable on your LAN (firewall).
- **MKV** may fail on the Default Media Receiver; prefer **MP4** from video presets.

## Differences from Android

- UI runs in the terminal (Textual), not Material on a phone.
- No Android-only features (notifications, SAF, etc.).
- Cast uses **PyChromecast** + local HTTP, conceptually similar to the mobile app flow.
