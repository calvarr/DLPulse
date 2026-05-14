#!/usr/bin/env python3
"""
Interactive CLI (Android-style menus in the terminal; no browser, no Textual).
Uses yt_core (same format presets as Android / web).
"""
from __future__ import annotations

import os
import re
import sys
import uuid
from pathlib import Path

_TUI = Path(__file__).resolve().parent
_ROOT = _TUI.parent
_FLET = _ROOT / "flet_app"
sys.path.insert(0, str(_TUI))
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_FLET))

import paths  # noqa: F401

from yt_core import (
    FORMAT_PRESETS,
    detect_content_type,
    extract_url_info,
    fetch_playlist_entries,
    get_format_preset,
    get_playlist_count,
    run_download,
    search_youtube,
)

from cast_http import guess_mime_for_cast, media_url, start_cast_server
from chromecast_helper import discover_chromecasts, get_lan_ip, play_url, stop_last_cast, stop_projection
from paths import DOWNLOADS_DIR

try:
    from rich.console import Console
    from rich.panel import Panel
    from rich.table import Table

    _rich = Console()
except ImportError:
    _rich = None


def say(msg: str = "", *, style: str | None = None) -> None:
    if _rich and style:
        _rich.print(msg, style=style)
    elif _rich:
        _rich.print(msg)
    else:
        print(msg)


def header(title: str) -> None:
    line = "═" * min(56, max(20, len(title) + 8))
    if _rich:
        _rich.print(Panel.fit(f"[bold]{title}[/]", border_style="cyan"))
    else:
        print(line)
        print(f"  {title}")
        print(line)


def prompt(msg: str, default: str = "") -> str:
    try:
        s = input(f"{msg}{f' [{default}]' if default else ''}: ").strip()
        return s if s else default
    except (EOFError, KeyboardInterrupt):
        print()
        raise SystemExit(0)


def prompt_yes(msg: str, default: bool = True) -> bool:
    hint = "Y/n" if default else "y/N"
    default_s = "y" if default else "n"
    s = prompt(f"{msg} ({hint})", default_s).lower()
    if not s:
        return default
    if s in ("y", "yes"):
        return True
    if s in ("n", "no"):
        return False
    return default


def choose_format() -> int:
    say("\nFormat presets (same as Android app):")
    for i, (label, _, _) in enumerate(FORMAT_PRESETS):
        say(f"  [{i}] {label}")
    while True:
        s = prompt("Pick index (0–9)", "0")
        try:
            n = int(s)
            if 0 <= n < len(FORMAT_PRESETS):
                return n
        except ValueError:
            pass
        say("Invalid number.", style="red")


def scan_library() -> list[tuple[str, Path]]:
    """List of (display path, full path)."""
    rows: list[tuple[str, Path]] = []
    if not DOWNLOADS_DIR.is_dir():
        return rows
    for job in sorted(DOWNLOADS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not job.is_dir() or ".." in job.name:
            continue
        try:
            for f in sorted(job.iterdir(), key=lambda x: x.name.lower()):
                if f.is_file():
                    rel = f"{job.name}/{f.name}"
                    rows.append((rel, f))
        except OSError:
            continue
    return rows


def menu_main() -> None:
    while True:
        header("DLPulse — CLI (Android-style)")
        say("  [1] Search YouTube")
        say("  [2] Enter URL (video / playlist / channel)")
        say("  [3] Library (downloaded files)")
        say("  [4] Chromecast (cast from library)")
        say("  [5] Check yt-dlp / pip version")
        say("  [6] Stop casting (last device you cast to)")
        say("  [0] Exit")
        c = prompt("Choice", "0")
        if c == "1":
            flow_search()
        elif c == "2":
            flow_url()
        elif c == "3":
            flow_library()
        elif c == "4":
            flow_chromecast()
        elif c == "5":
            flow_version()
        elif c == "6":
            flow_stop_last_cast()
        elif c == "0":
            say("Goodbye.")
            return
        else:
            say("Unknown option.", style="yellow")


def flow_search() -> None:
    header("YouTube search")
    q = prompt("Search query")
    if not q:
        return
    say("Searching…", style="dim")
    hits = search_youtube(q, max_results=12)
    if not hits:
        say("No results.", style="red")
        prompt("Enter for menu")
        return
    if _rich:
        t = Table(title="Results")
        t.add_column("#", style="cyan", width=3)
        t.add_column("Titlu")
        for i, h in enumerate(hits):
            t.add_row(str(i), (h.get("title") or "")[:70])
        _rich.print(t)
    else:
        for i, h in enumerate(hits):
            print(f"  [{i}] {h.get('title', '')[:75]}")
    say("\nEnter comma-separated indices (e.g. 0,2,5) or «all» for every result.")
    sel = prompt("Selection")
    if not sel:
        return
    if sel.strip().lower() in ("all", "tot", "*"):
        chosen = hits
    else:
        chosen = []
        for part in re.split(r"[\s,;]+", sel):
            part = part.strip()
            if not part:
                continue
            try:
                idx = int(part)
                if 0 <= idx < len(hits):
                    chosen.append(hits[idx])
            except ValueError:
                pass
    if not chosen:
        say("Nothing selected.")
        return
    fmt = choose_format()
    preset = get_format_preset(fmt)
    if not preset:
        return
    spec, extra = preset
    say(f"\nDownloading {len(chosen)} video(s)…", style="green")
    for h in chosen:
        url = h.get("url") or ""
        if not url:
            continue
        job = str(uuid.uuid4())
        out = DOWNLOADS_DIR / job
        out.mkdir(parents=True, exist_ok=True)
        say(f"→ {h.get('title', '')[:60]}…")
        ok, files, err = run_download(url, spec, extra, str(out), no_playlist=True)
        if ok:
            for f in files:
                say(f"  OK: {job}/{f}", style="green")
        else:
            say(f"  Error: {err}", style="red")
    prompt("\nEnter for menu")


def flow_url() -> None:
    header("YouTube URL (or any site yt-dlp supports)")
    url = prompt("URL")
    if not url:
        return
    say("Resolving URL…", style="dim")
    info = extract_url_info(url)
    if not info:
        say("Could not open URL.", style="red")
        prompt("Enter")
        return
    ctype, desc = detect_content_type(info)
    say(f"[green]{desc}[/]" if _rich else desc)
    say(f"Type: {ctype}")

    if ctype in ("playlist", "channel"):
        n = get_playlist_count(info)
        say(f"Estimated entries: {n}")
        entries, err = fetch_playlist_entries(url, max_entries=500)
        if err:
            say(err, style="red")
            prompt("Enter")
            return
        if not entries:
            say("List is empty.")
            prompt("Enter")
            return
        show = entries[:200]
        say(f"\nFirst {min(30, len(show))} entries (up to {len(entries)} total):")
        for i, e in enumerate(show[:30]):
            say(f"  [{i}] {(e.get('title') or '')[:70]}")
        if len(show) > 30:
            say(f"  … {len(show) - 30} more, indices 30–{len(show)-1}")
        say("\nComma-separated indices, or «all» for the full playlist (may take a long time). Enter = cancel.")
        sel = prompt("Selection / all")
        if not sel:
            return
        if sel.strip().lower() in ("all", "tot"):
            to_dl = list(entries)
            if len(to_dl) > 25 and not prompt_yes(f"Download all {len(to_dl)} videos?", False):
                return
        else:
            to_dl = []
            for part in re.split(r"[\s,;]+", sel):
                try:
                    idx = int(part.strip())
                    if 0 <= idx < len(show):
                        to_dl.append(show[idx])
                except ValueError:
                    pass
        if not to_dl:
            say("Nothing selected.")
            return
        fmt = choose_format()
        preset = get_format_preset(fmt)
        if not preset:
            return
        spec, extra = preset
        for e in to_dl:
            u = e.get("url") or ""
            if not u:
                continue
            job = str(uuid.uuid4())
            out = DOWNLOADS_DIR / job
            out.mkdir(parents=True, exist_ok=True)
            say(f"→ {(e.get('title') or '')[:50]}…")
            ok, files, err = run_download(u, spec, extra, str(out), no_playlist=True)
            if ok:
                for f in files:
                    say(f"  OK: {job}/{f}", style="green")
            else:
                say(f"  Error: {err}", style="red")
        prompt("Enter")
        return

    # single video
    no_pl = prompt_yes("Only this video (ignore playlist if URL is inside a playlist)?", True)
    fmt = choose_format()
    preset = get_format_preset(fmt)
    if not preset:
        return
    spec, extra = preset
    job = str(uuid.uuid4())
    out = DOWNLOADS_DIR / job
    out.mkdir(parents=True, exist_ok=True)
    say("Downloading…", style="yellow")
    ok, files, err = run_download(url, spec, extra, str(out), no_playlist=no_pl)
    if ok:
        for f in files:
            say(f"Done: {job}/{f}", style="green")
    else:
        say(f"Error: {err}", style="red")
    prompt("Enter")


def flow_library() -> None:
    while True:
        header("Library (downloads)")
        rows = scan_library()
        if not rows:
            say("No files in downloads/.")
            prompt("Enter for main menu")
            return
        if _rich:
            t = Table(title=f"{DOWNLOADS_DIR} — {len(rows)} files")
            t.add_column("#", style="cyan", width=4)
            t.add_column("File")
            t.add_column("Size")
            for i, (rel, p) in enumerate(rows[:200]):
                t.add_row(str(i), rel, str(p.stat().st_size))
            _rich.print(t)
            if len(rows) > 200:
                say(f"… showing 200 of {len(rows)}")
        else:
            for i, (rel, p) in enumerate(rows[:100]):
                print(f"  [{i:3}] {rel}  ({p.stat().st_size} B)")
        say("\n  o = open downloads folder  |  empty Enter = back to menu")
        c0 = prompt("File index (number) or o")
        if not c0.strip():
            return
        if c0.strip().lower() == "o":
            p = str(DOWNLOADS_DIR)
            if sys.platform == "darwin":
                os.system(f'open "{p}"')
            elif sys.platform == "win32":
                os.startfile(p)  # type: ignore[name-defined]
            else:
                os.system(f'xdg-open "{p}"')
            continue
        try:
            idx = int(c0.strip())
        except ValueError:
            say("Enter a number or o.")
            continue
        if idx < 0 or idx >= len(rows):
            say("Invalid index.")
            continue
        rel, path = rows[idx]
        say(f"Selected: {rel}")
        sub = prompt("Action: r=rename  s=delete  c=cast  Enter=cancel", "").lower()
        if sub == "r":
            new_name = prompt("New filename (name only, no path)")
            if not new_name or ".." in new_name or "/" in new_name:
                say("Invalid name.")
                continue
            dest = path.parent / new_name
            if dest.exists():
                say("Already exists.")
                continue
            try:
                path.rename(dest)
                say("Renamed.", style="green")
            except OSError as e:
                say(str(e), style="red")
        elif sub == "s":
            if prompt_yes(f"Delete {rel}?", False):
                try:
                    path.unlink()
                    jd = path.parent
                    if jd.is_dir() and not any(jd.iterdir()):
                        jd.rmdir()
                    say("Deleted.", style="green")
                except OSError as e:
                    say(str(e), style="red")
        elif sub == "c":
            _cast_file(rel)


def _cast_file(rel: str) -> None:
    low = rel.lower()
    if low.endswith(".mkv"):
        say("Warning: MKV may not play on the default Chromecast receiver.", style="yellow")
    say("Scanning for Chromecasts…", style="dim")
    casts = discover_chromecasts(wait_s=3.0)
    if not casts:
        say("No devices found.")
        return
    for i, c in enumerate(casts):
        say(f"  [{i}] {c.cast_info.friendly_name}")
    s = prompt("Device index", "0")
    try:
        ix = int(s)
    except ValueError:
        return
    if ix < 0 or ix >= len(casts):
        return
    port = start_cast_server(port=0)
    ip = get_lan_ip()
    url = media_url(rel, ip, port)
    mime = guess_mime_for_cast(Path(rel).name)
    say(f"URL: {url}", style="dim")
    try:
        play_url(casts[ix], url, mime)
        say("Casting started.", style="green")
    except Exception as e:
        say(str(e), style="red")


def flow_chromecast() -> None:
    header("Chromecast")
    say(
        "1) List devices  2) Start casting (file from library)  "
        "3) Stop casting  0) Back"
    )
    c = prompt("Choice")
    if c == "1":
        say("Scanning…", style="dim")
        casts = discover_chromecasts()
        for i, cc in enumerate(casts):
            info = cc.cast_info
            say(f"  [{i}] {info.friendly_name} — {getattr(info, 'host', '?')} ({getattr(info, 'model_name', '?')})")
        say(f"\nTotal: {len(casts)}")
    elif c == "2":
        rows = scan_library()
        if not rows:
            say("Library is empty. Download something first.")
            prompt("Enter")
            return
        for i, (rel, _) in enumerate(rows[:50]):
            say(f"  [{i}] {rel}")
        s = prompt("File index")
        try:
            ix = int(s)
            if 0 <= ix < len(rows):
                _cast_file(rows[ix][0])
        except ValueError:
            pass
    elif c == "3":
        casts = discover_chromecasts(wait_s=2.0)
        if not casts:
            return
        for i, cc in enumerate(casts):
            say(f"  [{i}] {cc.cast_info.friendly_name}")
        s = prompt("Device index (stop casting)")
        try:
            ix = int(s)
            stop_projection(casts[ix])
            say("Casting stopped.")
        except (ValueError, IndexError, Exception) as e:
            say(str(e), style="red")
    prompt("Enter")


def flow_stop_last_cast() -> None:
    header("Stop casting (last device)")
    ok, msg = stop_last_cast()
    say(msg, style="green" if ok else "yellow")
    prompt("Enter")


def flow_version() -> None:
    import subprocess

    try:
        import yt_dlp

        say(f"yt-dlp (modul): {getattr(yt_dlp.version, '__version__', '?')}")
    except Exception as e:
        say(str(e), style="red")
    try:
        r = subprocess.run(
            [sys.executable, "-m", "pip", "show", "yt-dlp"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        say(r.stdout[:500] if r.stdout else "(no output)")
    except Exception as e:
        say(str(e), style="dim")
    if prompt_yes("Run pip install -U yt-dlp now?", False):
        subprocess.run([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
    prompt("Enter")


def main() -> None:
    say(
        "\n[dim]No browser. Chromecast discovery uses the local network (mDNS).[/]\n"
        if _rich
        else "\nAndroid-style CLI — no browser.\n"
    )
    try:
        menu_main()
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
