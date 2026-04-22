#!/usr/bin/env python3
"""
Plain CLI: no browser, no Textual — terminal only (stdout/stderr).
Chromecast discovery via mDNS on the local LAN (same idea as PyChromecast / Android TV).

Examples:
  python pure_cli.py devices
  python pure_cli.py library
  python pure_cli.py download "https://youtube.com/watch?v=..." --format 0
  python pure_cli.py cast "job-id/file.mp4" --index 0
"""
from __future__ import annotations

import argparse
import os
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
    run_download,
    search_youtube,
)

from cast_http import guess_mime_for_cast, media_url, start_cast_server
from chromecast_helper import discover_chromecasts, get_lan_ip, play_url, stop_projection
from paths import DOWNLOADS_DIR


def cmd_devices(args: argparse.Namespace) -> int:
    print("Scanning for Chromecast / Google Cast devices on the LAN (mDNS)…", file=sys.stderr)
    casts = discover_chromecasts(wait_s=args.wait)
    if not casts:
        print(
            "No devices found. Check: same Wi‑Fi, firewall UDP 5353, Avahi on Linux.",
            file=sys.stderr,
        )
        return 1
    for i, c in enumerate(casts):
        info = c.cast_info
        host = getattr(info, "host", "?")
        port = getattr(info, "port", 8009)
        print(f"  [{i}]  {info.friendly_name}")
        print(f"       host: {host}:{port}  model: {getattr(info, 'model_name', '?')}")
        print(f"       uuid: {getattr(info, 'uuid', '?')}")
    print(f"\nTotal: {len(casts)} device(s). Use --index N with the «cast» command.")
    return 0


def cmd_library(_args: argparse.Namespace) -> int:
    if not DOWNLOADS_DIR.is_dir():
        print("(downloads folder missing or empty)")
        return 0
    n = 0
    for job in sorted(DOWNLOADS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if not job.is_dir():
            continue
        for f in sorted(job.iterdir(), key=lambda x: x.name.lower()):
            if f.is_file():
                rel = f"{job.name}/{f.name}"
                print(f"  {rel}  ({f.stat().st_size} B)")
                n += 1
    print(f"\n{n} file(s) (paths relative to downloads/).")
    return 0


def cmd_search(args: argparse.Namespace) -> int:
    q = (args.query or "").strip()
    if not q:
        print("Missing query.", file=sys.stderr)
        return 1
    hits = search_youtube(q, max_results=args.limit)
    for i, h in enumerate(hits):
        print(f"  [{i}] {h.get('title', '')[:80]}")
        print(f"       {h.get('url', '')}")
    print(f"\n{len(hits)} result(s).")
    return 0


def cmd_download(args: argparse.Namespace) -> int:
    url = (args.url or "").strip()
    if not url:
        return 1
    preset = get_format_preset(args.format)
    if not preset:
        print("Invalid format index.", file=sys.stderr)
        return 1
    spec, extra = preset
    job = str(uuid.uuid4())
    out = DOWNLOADS_DIR / job
    out.mkdir(parents=True, exist_ok=True)
    print(f"Downloading to {out} …", file=sys.stderr)
    ok, files, err = run_download(url, spec, extra, str(out), no_playlist=args.no_playlist)
    if ok:
        for f in files:
            print(f"OK: {job}/{f}")
        return 0
    print(f"Error: {err}", file=sys.stderr)
    return 1


def cmd_cast(args: argparse.Namespace) -> int:
    rel = (args.file or "").strip().replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        print("Invalid path. Example: uuid-folder/video.mp4", file=sys.stderr)
        return 1
    path = (DOWNLOADS_DIR / rel).resolve()
    if not path.is_file():
        print(f"File not found: {path}", file=sys.stderr)
        return 1
    print("Discovering devices…", file=sys.stderr)
    casts = discover_chromecasts(wait_s=args.wait)
    if not casts:
        print("No Chromecast found.", file=sys.stderr)
        return 1
    cast = None
    if args.name:
        name = args.name.lower()
        for c in casts:
            if name in (c.cast_info.friendly_name or "").lower():
                cast = c
                break
        if cast is None:
            print(f"No device matching name: {args.name}", file=sys.stderr)
            return 1
    else:
        idx = args.index
        if idx < 0 or idx >= len(casts):
            print(f"Invalid index. You have {len(casts)} device(s) (0–{len(casts)-1}).", file=sys.stderr)
            for i, c in enumerate(casts):
                print(f"  [{i}] {c.cast_info.friendly_name}", file=sys.stderr)
            return 1
        cast = casts[idx]

    port = start_cast_server(port=0)
    ip = get_lan_ip()
    url = media_url(rel, ip, port)
    mime = guess_mime_for_cast(path.name)
    print(f"HTTP: http://{ip}:{port}/media/…", file=sys.stderr)
    print(f"Casting to: {cast.cast_info.friendly_name}", file=sys.stderr)
    print(f"URL: {url}", file=sys.stderr)
    try:
        play_url(cast, url, mime)
        print("Casting started.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    casts = discover_chromecasts(wait_s=args.wait)
    if not casts:
        print("No Chromecast found.", file=sys.stderr)
        return 1
    if args.index < 0 or args.index >= len(casts):
        print(f"Index must be 0–{len(casts)-1}", file=sys.stderr)
        return 1
    try:
        stop_projection(casts[args.index])
        print("Casting stopped.")
    except Exception as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_info(args: argparse.Namespace) -> int:
    url = (args.url or "").strip()
    if not url:
        return 1
    info = extract_url_info(url)
    if not info:
        print("Could not read URL.")
        return 1
    ctype, desc = detect_content_type(info)
    print(desc)
    print(f"type: {ctype}")
    if ctype in ("playlist", "channel"):
        entries, err = fetch_playlist_entries(url, max_entries=30)
        if err:
            print(err)
            return 1
        for i, e in enumerate(entries):
            print(f"  [{i}] {e.get('title', '')[:70]}")
            print(f"       {e.get('url', '')}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(
        description="DLPulse — media downloader + Chromecast — CLI only (no browser). "
        "Cast devices are found via mDNS on the LAN."
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("devices", help="List Chromecast / Cast devices on the network")
    s.add_argument("--wait", type=float, default=3.0, help="Discovery scan seconds (default 3)")
    s.set_defaults(func=cmd_devices)

    s = sub.add_parser("library", help="List files under downloads/")
    s.set_defaults(func=cmd_library)

    s = sub.add_parser("search", help="Search YouTube (ytsearch)")
    s.add_argument("query", help="Search query")
    s.add_argument("--limit", type=int, default=10)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("download", help="Download a URL into downloads/<uuid>/")
    s.add_argument("url")
    s.add_argument("--format", type=int, default=0, help=f"Preset index 0–{len(FORMAT_PRESETS)-1}")
    s.add_argument("--no-playlist", action="store_true", help="First video only")
    s.set_defaults(func=cmd_download)

    s = sub.add_parser("info", help="URL info (playlist: first entries)")
    s.add_argument("url")
    s.set_defaults(func=cmd_info)

    s = sub.add_parser(
        "cast",
        help="Serve a file from downloads/ and start casting to Chromecast (local HTTP)",
    )
    s.add_argument(
        "file",
        help='Relative path under downloads/, e.g. "a1b2.../video.mp4"',
    )
    s.add_argument("--index", type=int, default=0, help="Device index from «devices»")
    s.add_argument("--name", help="Match friendly name (instead of --index)")
    s.add_argument("--wait", type=float, default=3.0, help="Seconds to scan for devices")
    s.set_defaults(func=cmd_cast)

    s = sub.add_parser(
        "stop",
        help="Stop casting on a device (stop media + quit Cast app)",
    )
    s.add_argument("--index", type=int, default=0)
    s.add_argument("--wait", type=float, default=2.0)
    s.set_defaults(func=cmd_stop)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
