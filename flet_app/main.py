#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import os
import shlex
from urllib.parse import quote
from contextlib import asynccontextmanager
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace


def _apply_linux_gl_env() -> None:
    if not sys.platform.startswith("linux"):
        return

    def _truthy(name: str) -> bool:
        return os.environ.get(name, "").strip().lower() in ("1", "true", "yes")

    if _truthy("FLET_SW_GL"):
        os.environ["LIBGL_ALWAYS_SOFTWARE"] = "1"
        os.environ.setdefault("GALLIUM_DRIVER", "llvmpipe")
    elif not _truthy("FLET_NO_WAYLAND_FIX"):
        wayland = bool(os.environ.get("WAYLAND_DISPLAY")) or (
            os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland"
        )
        if wayland:
            os.environ.setdefault("GDK_BACKEND", "x11")
            os.environ.setdefault("QT_QPA_PLATFORM", "xcb")
            if not os.environ.get("DISPLAY"):
                os.environ.setdefault("DISPLAY", ":0")
            os.environ.pop("WAYLAND_DISPLAY", None)
    os.environ.setdefault("MESA_NO_ERROR", "1")


_apply_linux_gl_env()

import flet as ft

# When the entrypoint is ``flet_app.main`` (e.g. ``flet build``), keep the same
# ``sys.path`` as when running ``python main.py`` from this directory.
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import paths  # noqa: F401

from file_browser_dialog import show_folder_browser_dialog

from yt_core import (
    FORMAT_PRESETS,
    detect_content_type,
    extract_url_info,
    fetch_playlist_entries,
    get_format_preset,
    normalize_youtube_radio_mix_url,
    run_download,
    search_keywords_multi,
    youtube_url_for_single_video_download,
)
from cast_http import (
    get_cast_server_port,
    guess_mime_for_cast,
    is_cast_server_running,
    media_url,
    start_cast_server,
    stop_cast_server,
    stream_url,
)
from chromecast_helper import (
    discover_chromecasts,
    get_lan_ip,
    media_progress,
    pause as cast_pause,
    play as cast_play_receiver,
    play_url_to_casts,
    queue_set_repeat_mode,
    queue_set_shuffle,
    seek_media,
    set_receiver_volume,
    stop_projection,
)
from paths import (
    bump_app_launch_count,
    get_audio_player_command,
    get_cast_discovery_wait_s,
    get_downloads_dir,
    get_github_update_dismissed_main_sha,
    get_video_player_command,
    mark_ytdlp_pypi_checked,
    set_audio_player_command,
    set_cast_discovery_wait_s,
    set_downloads_dir,
    set_github_update_dismissed_main_sha,
    set_video_player_command,
    should_check_ytdlp_pypi,
)
from ytdlp_update import (
    fetch_pypi_latest_ytdlp_version,
    get_installed_ytdlp_version,
    is_newer_pypi_version,
    pip_upgrade_ytdlp,
    reload_ytdlp_module,
)
from github_update import (
    GITHUB_PROJECT_URL,
    GITHUB_RELEASES_URL,
    check_app_github_update,
    commit_page_url,
    get_app_package_version,
    get_local_commit_sha,
)

_AUDIO_SUFFIXES = frozenset(
    {".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus", ".wav", ".wma", ".m4b", ".alac"}
)


def _is_audio_file(path: Path) -> bool:
    return path.suffix.lower() in _AUDIO_SUFFIXES


def _thumbnail_from_yt_entry(d: dict) -> str:
    t = (d.get("thumbnail") or "").strip()
    if t:
        return t
    vid = d.get("id") or ""
    if isinstance(vid, str) and len(vid) == 11 and not vid.startswith("UC"):
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return ""


def _thumbnail_from_extract_info(info: dict) -> str:
    t = (info.get("thumbnail") or "").strip()
    if t:
        return t
    thumbs = info.get("thumbnails") or []
    if isinstance(thumbs, list) and thumbs:
        last = thumbs[-1]
        if isinstance(last, dict):
            u = (last.get("url") or "").strip()
            if u:
                return u
    vid = info.get("id") or ""
    if isinstance(vid, str) and len(vid) == 11 and not vid.startswith("UC"):
        return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return ""


def _search_hit_from_extract_info(info: dict, page_url: str) -> dict:
    """Same shape as ``search_youtube`` rows: id, title, url, thumbnail."""
    thumb = _thumbnail_from_extract_info(info)
    vid = info.get("id") or ""
    if not thumb and isinstance(vid, str) and len(vid) == 11:
        thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    title = (info.get("title") or "").strip() or "Untitled"
    return {"id": vid, "title": title, "url": page_url, "thumbnail": thumb}


def _thumb_tile(url: str, w: int = 120, h: int = 68) -> ft.Control:
    if not url:
        return ft.Container(
            width=w,
            height=h,
            bgcolor=ft.Colors.GREY_800,
            border_radius=4,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.VIDEO_LIBRARY, size=22, color=ft.Colors.GREY_500),
        )
    return ft.Image(
        src=url,
        width=w,
        height=h,
        fit=ft.BoxFit.COVER,
        border_radius=4,
        error_content=ft.Container(
            width=w,
            height=h,
            bgcolor=ft.Colors.GREY_800,
            alignment=ft.Alignment.CENTER,
            content=ft.Icon(ft.Icons.BROKEN_IMAGE_OUTLINED, size=22, color=ft.Colors.GREY_500),
        ),
    )


def _fmt_idx(drop: ft.Dropdown | None) -> int:
    if not drop or not drop.value:
        return 0
    try:
        return int(str(drop.value))
    except (TypeError, ValueError):
        return 0


def _preset_requires_ffmpeg_conversion(fmt_i: int) -> bool:
    """True for presets that need FFmpeg re-encode/extract (e.g. MP3/M4A)."""
    preset = get_format_preset(fmt_i)
    if not preset:
        return False
    _spec, extra = preset
    post = extra.get("postprocessors") or []
    if not isinstance(post, list):
        return False
    for item in post:
        if not isinstance(item, dict):
            continue
        if str(item.get("key", "")).strip() == "FFmpegExtractAudio":
            return True
    return False


def dismiss_dialog(dlg: ft.DialogControl) -> None:
    dlg.open = False
    dlg.update()


def play_media_file(path: Path) -> None:
    p = path.expanduser().resolve()
    if not p.is_file():
        raise OSError("Not a file or file missing.")
    cmd = get_audio_player_command() if _is_audio_file(p) else get_video_player_command()
    if not cmd:
        if sys.platform == "darwin":
            subprocess.Popen(["open", str(p)])
        elif sys.platform == "win32":
            os.startfile(str(p))  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", str(p)])
        return
    argv = shlex.split(cmd, posix=os.name != "nt")
    if not argv:
        raise ValueError("Player command is empty.")
    subprocess.Popen([*argv, str(p)])


def _write_temp_m3u_playlist(paths: list[Path]) -> Path:
    resolved = [p.expanduser().resolve() for p in paths]
    for p in resolved:
        if not p.is_file():
            raise OSError(f"Not found: {p}")
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        newline="\n",
        suffix=".m3u",
        prefix="yt_playlist_",
        delete=False,
    )
    out_path = Path(tmp.name)
    try:
        tmp.write("#EXTM3U\n")
        for p in resolved:
            tmp.write(p.as_uri() + "\n")
        tmp.close()
        return out_path
    except Exception:
        tmp.close()
        try:
            out_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def play_media_files(paths: list[Path]) -> None:
    if not paths:
        raise ValueError("No files selected.")
    resolved = [p.expanduser().resolve() for p in paths]
    for p in resolved:
        if not p.is_file():
            raise OSError(f"Not found: {p}")
    if len(resolved) == 1:
        play_media_file(resolved[0])
        return

    playlist = _write_temp_m3u_playlist(resolved)
    pl_str = str(playlist)

    all_audio = all(_is_audio_file(p) for p in resolved)
    cmd = get_audio_player_command() if all_audio else get_video_player_command()
    if not cmd:
        if sys.platform == "darwin":
            subprocess.Popen(["open", pl_str])
        elif sys.platform == "win32":
            os.startfile(pl_str)  # type: ignore[attr-defined]
        else:
            subprocess.Popen(["xdg-open", pl_str])
        return

    argv = shlex.split(cmd, posix=os.name != "nt")
    if not argv:
        raise ValueError("Player command is empty.")
    subprocess.Popen([*argv, pl_str])


def _resolve_external_player_argv_for_stream() -> list[str] | None:
    """
    External player for streams (Search & Download): **Video** player command first,
    then **Audio** (SoundCloud / music are often audio-only), then mpv / VLC on PATH
    (or common install paths on Windows/macOS).
    Does not use the default browser (xdg-open / startfile on raw page URLs).
    """
    seen: set[str] = set()
    for raw in (get_video_player_command(), get_audio_player_command()):
        cmd = (raw or "").strip()
        if not cmd or cmd in seen:
            continue
        seen.add(cmd)
        argv = shlex.split(cmd, posix=os.name != "nt")
        if argv:
            return argv
    exe = shutil.which("mpv")
    if exe:
        return [exe]
    exe = shutil.which("vlc")
    if exe:
        return [exe]
    if sys.platform == "win32":
        for p in (
            r"C:\Program Files\VideoLAN\VLC\vlc.exe",
            r"C:\Program Files (x86)\VideoLAN\VLC\vlc.exe",
        ):
            if os.path.isfile(p):
                return [p]
    if sys.platform == "darwin":
        vlc = "/Applications/VLC.app/Contents/MacOS/VLC"
        if os.path.isfile(vlc):
            return [vlc]
    return None


def _inject_mpv_stream_gui_flags(argv: list[str]) -> list[str]:
    """
    mpv hides its window for audio-only streams by default — no OSC, feels “stuck”.
    Add ``--force-window=immediate`` unless the user already set window / no-video options.
    """
    if not argv:
        return argv
    name = Path(argv[0]).name.lower()
    if name not in ("mpv", "mpv.exe"):
        return argv
    joined = " ".join(argv).lower()
    if "force-window" in joined or "--no-video" in joined or "-novideo" in joined:
        return argv
    return [argv[0], "--force-window=immediate", *argv[1:]]


def play_stream_urls(urls: list[str]) -> None:
    """
    Play page URLs (YouTube, SoundCloud, …) without downloading, via the local relay
    ``http://127.0.0.1:<port>/remote_stream?u=…`` (HTTP redirect or ffmpeg mux),
    so the player receives a media stream instead of opening the site in a browser.
    """
    clean: list[str] = []
    for u in urls:
        t = (u or "").strip()
        if not t:
            continue
        clean.append(youtube_url_for_single_video_download(t))
    if not clean:
        raise ValueError("No URLs to play.")

    argv = _resolve_external_player_argv_for_stream()
    if not argv:
        raise ValueError(
            "No player found. Install mpv or VLC, or set “Video player command” / “Audio player command” in Settings."
        )
    argv = _inject_mpv_stream_gui_flags(argv)

    if not is_cast_server_running():
        start_cast_server(port=0)
    port = get_cast_server_port()
    if port <= 0:
        raise RuntimeError("Local stream server did not start (cannot build /remote_stream URLs).")

    relay = [
        f"http://127.0.0.1:{port}/remote_stream?u={quote(u, safe='')}"
        for u in clean
    ]
    popen_kw: dict = {"stdin": subprocess.DEVNULL}
    if os.name != "nt":
        popen_kw["start_new_session"] = True
    subprocess.Popen([*argv, *relay], **popen_kw)


def _format_duration_hms(seconds: float) -> str:
    if seconds < 0 or seconds != seconds:
        return "0:00"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


_TAB_PANEL_SHADOW = ft.BoxShadow(
    blur_radius=20,
    spread_radius=0,
    color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
    offset=ft.Offset(0, 6),
)

# Dark UI: page + card surfaces (gradient, blue-grey / slate)
_PAGE_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment(-1, -1),
    end=ft.Alignment(1, 0.85),
    colors=["#070b12", "#101a2e", "#0a1628"],
)
_TAB_GRADIENT = ft.LinearGradient(
    begin=ft.Alignment.TOP_LEFT,
    end=ft.Alignment.BOTTOM_RIGHT,
    colors=["#151d2e", "#0d1422", "#121c30"],
)


def main(page: ft.Page) -> None:
    page.title = "DLPulse"
    page.theme_mode = ft.ThemeMode.DARK
    page.bgcolor = "#070b12"
    page.padding = 16
    page.window.min_width = 520
    page.window.min_height = 400
    page.theme = ft.Theme(
        color_scheme=ft.ColorScheme(
            primary=ft.Colors.TEAL_300,
            on_primary=ft.Colors.BLACK,
            primary_container=ft.Colors.BLUE_GREY_900,
            on_primary_container=ft.Colors.GREY_100,
            secondary=ft.Colors.BLUE_GREY_400,
            on_secondary=ft.Colors.BLACK,
            surface=ft.Colors.GREY_900,
            on_surface=ft.Colors.GREY_100,
            surface_container_highest=ft.Colors.GREY_800,
        ),
    )

    st = SimpleNamespace()
    st.search_hits: list[dict] = []
    st.pl_entries: list[dict] = []
    st.cast_devices: list = []
    st.cast_port: int = 0
    st.file_to_cast: str | None = None
    # Relative paths from last "Prepare for Cast" (one or many) — only for stream URL display.
    st.cast_stream_rels: list[str] | None = None
    st.lib_rows: list[tuple[str, Path]] = []
    st.cast_prog_guard: bool = False
    st.cast_poll_run: bool = True
    st.cast_last_play_idxs: list[int] = []
    st.cast_repeat_idx: int = 0
    st.cast_shuffle: bool = False
    st.active_result_kind: str = "none"
    # Which sites were queried for the last keyword search (for result row labels).
    st.last_search_sources: frozenset[str] = frozenset()
    st.main_tabs: ft.Tabs | None = None
    st.library_loaded_once: bool = False
    # Search & Download: optional session folder (None = always use Settings path).
    st.search_session_dir: Path | None = None
    # Library tab: browse-only folder for the list; download destination stays in Settings.
    st.library_view_dir: Path | None = None
    st.ytdlp_update_available = False
    st.ytdlp_pypi_latest: str | None = None
    st.ytdlp_update_note: str | None = None
    st.github_banner_remote_sha: str | None = None  # tip of main when banner was last shown (for dismiss)

    def effective_search_download_dir() -> Path:
        if st.search_session_dir is not None:
            return st.search_session_dir
        return get_downloads_dir()

    status = ft.Text("", size=12, color=ft.Colors.AMBER_200)
    busy_ring = ft.ProgressRing(
        width=20,
        height=20,
        stroke_width=2.5,
        color=ft.Colors.TEAL_400,
        visible=False,
    )
    busy_caption = ft.Text("", size=11, color=ft.Colors.GREY_400, visible=False)
    busy_row = ft.Row(
        [busy_ring, busy_caption],
        spacing=10,
        visible=False,
        alignment=ft.MainAxisAlignment.END,
        vertical_alignment=ft.CrossAxisAlignment.CENTER,
    )

    def set_busy(active: bool, detail: str = "") -> None:
        busy_row.visible = active
        busy_ring.visible = active
        busy_caption.visible = active
        busy_caption.value = (detail or "Please wait…") if active else ""

    def clear_busy() -> None:
        set_busy(False)

    @asynccontextmanager
    async def async_busy(detail: str, *, min_display_s: float | None = None):
        """Show spinner + caption. If ``min_display_s`` is set, keep the spinner visible
        for at least that many seconds (useful when the player ``Popen`` returns immediately)."""
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        set_busy(True, detail)
        set_status(detail)
        page.update()
        try:
            yield
        finally:
            if min_display_s is not None:
                elapsed = loop.time() - t0
                rem = float(min_display_s) - elapsed
                if rem > 0:
                    await asyncio.sleep(rem)
            clear_busy()
            page.update()

    def set_status(msg: str) -> None:
        status.value = msg

    async def play_search_results_async(urls: list[str]) -> None:
        """Play one or more URLs from Search & Download in the external player."""
        cleaned = [u.strip() for u in urls if (u or "").strip()]
        if not cleaned:
            set_status("No URL to play.")
            page.update()
            return
        try:
            async with async_busy(
                "Search & Download — opening local stream relay in your video player…",
                min_display_s=0.45,
            ):
                await asyncio.to_thread(play_stream_urls, cleaned)
            set_status("Player launch requested — check the video window or taskbar.")
        except (OSError, ValueError) as e:
            set_status(str(e))
        page.update()

    async def pick_folder_dialog(initial: str | None) -> str | None:
        """Pick a folder using the in-app browser (no native OS dialogs)."""
        start = initial if initial and os.path.isdir(initial) else str(Path.home())
        return await show_folder_browser_dialog(
            page,
            initial=start,
            title="Choose save folder",
            pick_mode=True,
            dismiss_dialog_fn=dismiss_dialog,
        )

    dl_queue = ft.Column(spacing=8, visible=False)

    preset_options = [ft.dropdown.Option(key=str(i), text=p[0][:80]) for i, p in enumerate(FORMAT_PRESETS)]
    dd_results_fmt = ft.Dropdown(label="Format preset", options=preset_options, value="0", width=400)
    cb_download_cover = ft.Checkbox(
        label="Download cover image (thumbnail)",
        value=False,
        tooltip="When enabled, yt-dlp tries to download/embed artwork for YouTube and SoundCloud.",
    )

    col_results = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO)
    result_checks: list[ft.Checkbox] = []

    def on_results_select_all(e: ft.ControlEvent) -> None:
        v = bool(e.control.value)
        for cb in result_checks:
            cb.value = v
        page.update()

    def on_any_result_check(_: ft.ControlEvent) -> None:
        if result_checks and all(cb.value for cb in result_checks):
            results_cb_all.value = True
        else:
            results_cb_all.value = False
        page.update()

    results_cb_all = ft.Checkbox(label="Select all", value=False, on_change=on_results_select_all)

    def rebuild_results() -> None:
        col_results.controls.clear()
        result_checks.clear()
        results_cb_all.value = False
        if st.active_result_kind == "search":
            items: list = list(st.search_hits)
        elif st.active_result_kind == "playlist":
            items = list(st.pl_entries)
        else:
            return
        for item in items:
            cb = ft.Checkbox(value=False, on_change=on_any_result_check)
            result_checks.append(cb)
            thumb = _thumbnail_from_yt_entry(item)
            u = (item.get("url") or "").strip()
            raw_title = (item.get("title") or "").strip()
            src = (item.get("source") or "").strip().lower()
            if (
                st.active_result_kind == "search"
                and len(st.last_search_sources) > 1
                and src == "soundcloud"
            ):
                title_disp = f"[SC] {raw_title}"[:120]
            elif (
                st.active_result_kind == "search"
                and len(st.last_search_sources) > 1
                and src == "youtube"
            ):
                title_disp = f"[YT] {raw_title}"[:120]
            else:
                title_disp = raw_title[:120]
            row_cells: list[ft.Control] = [
                _thumb_tile(thumb),
                cb,
                ft.Text(title_disp, size=13, expand=True, color=ft.Colors.GREY_200),
            ]
            if u:

                async def _row_play(_: ft.ControlEvent, url: str = u) -> None:
                    await play_search_results_async([url])

                row_cells.append(
                    ft.IconButton(
                        icon=ft.Icons.PLAY_CIRCLE_OUTLINE,
                        tooltip="Play this row directly (stream, no download)",
                        icon_size=22,
                        icon_color=ft.Colors.TEAL_300,
                        on_click=_row_play,
                    )
                )
            col_results.controls.append(
                ft.Row(
                    row_cells,
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                )
            )

    def _input_looks_like_url(s: str) -> bool:
        t = s.strip()
        if not t:
            return False
        tl = t.lower()
        if tl.startswith(("http://", "https://")):
            return True
        if "youtube.com" in tl or "youtu.be" in tl:
            return True
        return False

    cb_src_youtube = ft.Checkbox(label="YouTube", value=True)
    cb_src_soundcloud = ft.Checkbox(label="SoundCloud", value=False)

    tf_query = ft.TextField(
        label="Keywords (search) or paste a URL (YouTube, SoundCloud, playlist, …)",
        expand=True,
        autofocus=True,
        # Room for the floating label so it is not clipped by the tab bar / divider when the layout is tight.
        content_padding=ft.Padding.only(left=12, right=12, top=16, bottom=12),
    )

    async def _search_keywords(q: str) -> None:
        want_yt = bool(cb_src_youtube.value)
        want_sc = bool(cb_src_soundcloud.value)
        if not want_yt and not want_sc:
            set_status("Select at least one search source: YouTube and/or SoundCloud.")
            page.update()
            return
        parts = [p for p, ok in (("YouTube", want_yt), ("SoundCloud", want_sc)) if ok]
        busy_lbl = " + ".join(parts)
        async with async_busy(f"Searching {busy_lbl} (network)…"):
            hits, used = await asyncio.to_thread(
                search_keywords_multi,
                q,
                youtube=want_yt,
                soundcloud=want_sc,
                max_per_source=12,
            )
        st.search_hits = hits
        st.last_search_sources = used
        st.pl_entries = []
        st.active_result_kind = "search"
        rebuild_results()
        set_status("")
        page.update()

    async def _resolve_url(url: str) -> None:
        set_busy(True, "Reading playlist / channel from URL (yt-dlp)…")
        set_status("Resolving URL…")
        page.update()
        raw = url.strip()
        # Same as Android YoutubeUrl.normalize: do not strip list= / mix — only fix Music host.
        probe = (
            raw.replace("music.youtube.com", "www.youtube.com", 1)
            if "music.youtube.com" in raw
            else raw
        )

        # Android: YtdlpJson.fetchPlaylistEntries(applicationContext, raw) on the raw URL.
        # Flat extract with normalize_url=False keeps watch?v=…&list=RDEM_… so mix shows all rows.
        entries, err = await asyncio.to_thread(
            fetch_playlist_entries, probe, 500, normalize_url=False
        )
        if err:
            clear_busy()
            set_status(err[:200])
            page.update()
            return
        if entries:
            st.search_hits = []
            st.last_search_sources = frozenset()
            st.pl_entries = entries
            st.active_result_kind = "playlist"
            rebuild_results()
            clear_busy()
            set_status(
                f"{len(entries)} videos — tick rows to play or download."
            )
            page.update()
            return

        busy_caption.value = "Inspecting single video or nested playlist (yt-dlp)…"
        page.update()
        canonical = normalize_youtube_radio_mix_url(probe)
        info = await asyncio.to_thread(extract_url_info, canonical)
        if not info:
            st.pl_entries = []
            st.search_hits = []
            st.last_search_sources = frozenset()
            st.active_result_kind = "none"
            rebuild_results()
            clear_busy()
            set_status("URL unreachable or blocked.")
            page.update()
            return
        ctype, _ = detect_content_type(info)
        st.search_hits = []
        if ctype in ("playlist", "channel"):
            st.pl_entries = []
            st.last_search_sources = frozenset()
            st.active_result_kind = "none"
            rebuild_results()
            busy_caption.value = "Loading full playlist (this can take a while)…"
            page.update()
            entries2, err2 = await asyncio.to_thread(fetch_playlist_entries, canonical, 500)
            if err2:
                set_status(err2[:200])
            else:
                st.pl_entries = entries2
                st.last_search_sources = frozenset()
                st.active_result_kind = "playlist"
                rebuild_results()
                set_status(f"{len(entries2)} videos loaded — tick rows to play or download.")
        else:
            st.pl_entries = []
            st.search_hits = [_search_hit_from_extract_info(info, canonical)]
            st.last_search_sources = frozenset()
            st.active_result_kind = "search"
            rebuild_results()
            set_status("")
        clear_busy()
        page.update()

    async def on_query(_: ft.ControlEvent) -> None:
        q = tf_query.value.strip()
        if not q:
            set_status("Enter search words or a URL.")
            page.update()
            return
        if _input_looks_like_url(q):
            await _resolve_url(q)
        else:
            await _search_keywords(q)

    tf_query.on_submit = on_query
    btn_query = ft.Button(content="Search / open URL", icon=ft.Icons.SEARCH, on_click=on_query)

    async def do_download_urls(
        urls: list[str], fmt_i: int, no_pl: bool, download_cover: bool
    ) -> None:
        preset = get_format_preset(fmt_i)
        if not preset:
            set_status("Invalid format.")
            return
        spec, extra = preset
        loop = asyncio.get_running_loop()
        urls_f = [u for u in urls if u]
        if not urls_f:
            return

        set_busy(True, f"Preparing download of {len(urls_f)} item(s)…")
        set_status("Preparing download…")
        page.update()

        n = len(urls_f)
        dl_queue.controls.clear()
        slot_pos = ft.Text("", size=11, color=ft.Colors.GREY_500)
        dl_name = ft.Text(
            "",
            size=14,
            weight=ft.FontWeight.W_500,
            color=ft.Colors.TEAL_200,
            selectable=True,
        )
        pct = ft.Text("—", width=52, size=12, color=ft.Colors.TEAL_200)
        bar = ft.ProgressBar(value=0, expand=True, bar_height=8)
        st_line = ft.Text("", size=11, color=ft.Colors.GREY_500)
        dl_queue.controls.append(
            ft.Column(
                spacing=6,
                tight=True,
                controls=[
                    slot_pos,
                    dl_name,
                    ft.Row([pct, bar], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                    st_line,
                ],
            )
        )
        dl_queue.visible = True
        clear_busy()
        page.update()

        def apply_progress(payload: dict) -> None:
            msg = (payload.get("message") or "").strip()
            frac = payload.get("fraction")
            base = (payload.get("filename") or "").strip()
            title = (payload.get("title") or "").strip()
            if base:
                dl_name.value = base
            elif title:
                dl_name.value = title[:200]
            if frac is not None:
                fv = float(frac)
                bar.value = fv
                pct.value = f"{int(fv * 100)}%"
            else:
                pct.value = "…"
                bar.value = None
            st_line.value = msg[:200] if msg else ""
            page.update()

        status_after: str | None = None
        try:
            out = effective_search_download_dir()
            out.mkdir(parents=True, exist_ok=True)
            for i, u in enumerate(urls_f):
                slot_pos.value = f"Downloading {i + 1} of {n}"
                dl_name.value = "…"
                pct.value = "0%"
                bar.value = 0
                st_line.value = "Starting…"
                st_line.color = ft.Colors.GREY_500
                page.update()

                def make_cb():
                    def progress_cb(payload: dict) -> None:
                        p = dict(payload)
                        loop.call_soon_threadsafe(lambda p=p: apply_progress(p))

                    return progress_cb

                ok, files, err = await asyncio.to_thread(
                    run_download,
                    u,
                    spec,
                    extra,
                    str(out),
                    no_pl,
                    download_cover,
                    make_cb(),
                )
                if ok:
                    bar.value = 1.0
                    pct.value = "100%"
                    if files:
                        dl_name.value = files[0]
                    st_line.value = "Done."
                    st_line.color = ft.Colors.GREEN_400
                    status_after = None
                else:
                    pct.value = "—"
                    bar.value = None
                    st_line.value = (err or "Error")[:220]
                    st_line.color = ft.Colors.RED_300
                    status_after = (err or "Download failed.")[:300]
                page.update()
        except OSError as e:
            status_after = str(e).strip()[:300] or "Could not create or use the save folder."
        except Exception as e:
            status_after = (str(e).strip() or type(e).__name__)[:300]
        finally:
            set_status(status_after or "")
            dl_queue.controls.clear()
            dl_queue.visible = False
            clear_busy()
        set_busy(True, "Refreshing library list…")
        page.update()
        refresh_library()
        clear_busy()
        page.update()

    def _selected_result_urls() -> list[str]:
        urls: list[str] = []
        if st.active_result_kind == "search":
            for i, h in enumerate(st.search_hits):
                if i < len(result_checks) and result_checks[i].value:
                    urls.append(h.get("url") or "")
        elif st.active_result_kind == "playlist":
            for i, e in enumerate(st.pl_entries):
                if i < len(result_checks) and result_checks[i].value:
                    urls.append(e.get("url") or "")
        return [u for u in urls if u]

    async def on_dl_results(_: ft.ControlEvent) -> None:
        if st.active_result_kind not in ("search", "playlist"):
            set_status("Search with keywords or open a playlist/channel URL first.")
            page.update()
            return
        fmt_i = _fmt_idx(dd_results_fmt)
        if _preset_requires_ffmpeg_conversion(fmt_i) and not shutil.which("ffmpeg"):
            set_status(
                "This audio conversion preset needs ffmpeg (MP3/M4A). Install ffmpeg, then try again."
            )
            page.update()
            return
        urls = _selected_result_urls()
        if not urls:
            set_status("Tick one or more rows in the list above.")
            page.update()
            return
        await do_download_urls(urls, fmt_i, True, bool(cb_download_cover.value))

    async def on_play_selected(_: ft.ControlEvent) -> None:
        if st.active_result_kind not in ("search", "playlist"):
            set_status("Search with keywords or open a playlist/channel URL first.")
            page.update()
            return
        urls = _selected_result_urls()
        if not urls:
            set_status("Tick one or more rows to play.")
            page.update()
            return
        await play_search_results_async(urls)

    btn_play_selected = ft.OutlinedButton(
        content="Play selected",
        icon=ft.Icons.PLAY_ARROW,
        on_click=on_play_selected,
    )

    btn_dl_results = ft.Button(
        content="Download selected",
        icon=ft.Icons.DOWNLOAD,
        bgcolor=ft.Colors.GREEN_700,
        color=ft.Colors.WHITE,
        icon_color=ft.Colors.WHITE,
        on_click=on_dl_results,
    )

    search_dl_settings_lbl = ft.Text(
        f"Default folder (Settings): {get_downloads_dir()}",
        size=11,
        color=ft.Colors.GREY_400,
        selectable=True,
    )
    search_dl_session_lbl = ft.Text(
        "",
        size=11,
        color=ft.Colors.GREY_500,
        selectable=True,
        visible=False,
    )
    btn_clear_search_session = ft.OutlinedButton(
        content="Use Settings folder",
        visible=False,
    )

    def refresh_search_dl_folder_label() -> None:
        search_dl_settings_lbl.value = f"Default folder (Settings): {get_downloads_dir()}"
        if st.search_session_dir is not None:
            search_dl_session_lbl.value = f"Session (temporary): files save to {st.search_session_dir}"
            search_dl_session_lbl.color = ft.Colors.TEAL_200
            search_dl_session_lbl.visible = True
            btn_clear_search_session.visible = True
        else:
            search_dl_session_lbl.value = ""
            search_dl_session_lbl.visible = False
            btn_clear_search_session.visible = False

    async def on_clear_search_session(_: ft.ControlEvent) -> None:
        st.search_session_dir = None
        refresh_search_dl_folder_label()
        set_status("This tab uses the Settings folder again for downloads.")
        page.update()

    btn_clear_search_session.on_click = lambda e: asyncio.create_task(on_clear_search_session(e))

    lib_list = ft.ListView(spacing=0, padding=0, height=272, auto_scroll=True)
    lib_sel_hint = ft.Text(
        "No files selected. Tick rows or use Select all — order top to bottom is the playlist order.",
        size=12,
        color=ft.Colors.GREY_400,
    )
    lib_checks: list[ft.Checkbox] = []

    def update_lib_sel_hint() -> None:
        if not lib_checks:
            lib_sel_hint.value = "No files in list."
            return
        sel = [i for i, cb in enumerate(lib_checks) if cb.value]
        if not sel:
            lib_sel_hint.value = (
                "No files selected. Tick rows or use Select all — order top to bottom is the playlist order."
            )
        elif len(sel) == 1:
            lib_sel_hint.value = st.lib_rows[sel[0]][0][:120]
        else:
            lib_sel_hint.value = f"{len(sel)} files selected — playlist order: top to bottom ({len(sel)} tracks)."

    def on_lib_select_all(e: ft.ControlEvent) -> None:
        v = bool(e.control.value)
        for cb in lib_checks:
            cb.value = v
        update_lib_sel_hint()
        page.update()

    def on_any_lib_check(_: ft.ControlEvent) -> None:
        if lib_checks and all(cb.value for cb in lib_checks):
            lib_cb_all.value = True
        else:
            lib_cb_all.value = False
        update_lib_sel_hint()
        page.update()

    lib_cb_all = ft.Checkbox(label="Select all", value=False, on_change=on_lib_select_all)

    # Fixed widths: avoid expand+scroll layouts that stretch TextFields to full tab height.
    tf_save_root = ft.TextField(
        label="Save folder",
        value=str(get_downloads_dir()),
        width=480,
    )

    lib_view_lbl = ft.Text("", size=11, color=ft.Colors.TEAL_300, visible=False, selectable=True)
    btn_lib_reset_list = ft.OutlinedButton(
        content="Use save folder",
        icon=ft.Icons.VIEW_LIST,
        visible=False,
    )

    def scan_library() -> list[tuple[str, Path]]:
        """Lists files: Library Browse uses ``library_view_dir`` only; else Settings + optional session folder."""
        rows: list[tuple[str, Path]] = []
        roots: list[Path] = []
        if st.library_view_dir is not None:
            try:
                r = st.library_view_dir.expanduser().resolve()
            except OSError:
                r = st.library_view_dir.expanduser()
            roots.append(r)
            multi = False
        else:
            try:
                r0 = get_downloads_dir().expanduser().resolve()
            except OSError:
                r0 = get_downloads_dir().expanduser()
            roots.append(r0)
            if st.search_session_dir is not None:
                try:
                    r1 = st.search_session_dir.expanduser().resolve()
                except OSError:
                    r1 = st.search_session_dir.expanduser()
                if r1 != r0:
                    roots.append(r1)
            multi = len(roots) > 1
        all_files: list[tuple[float, str, Path]] = []
        try:
            for root in roots:
                if not root.is_dir():
                    continue
                for p in root.rglob("*"):
                    if not p.is_file():
                        continue
                    try:
                        rel = p.relative_to(root)
                    except ValueError:
                        continue
                    rel_s = rel.as_posix()
                    if multi:
                        anchor = root.name or root.as_posix().rstrip("/").split("/")[-1] or "folder"
                        label = f"{anchor}/{rel_s}"
                    else:
                        label = rel_s
                    try:
                        mtime = p.stat().st_mtime
                    except OSError:
                        mtime = 0.0
                    all_files.append((mtime, label, p))
        except OSError:
            return rows
        by_key: dict[Path, tuple[float, str, Path]] = {}
        for mtime, label, p in all_files:
            try:
                key = p.resolve()
            except OSError:
                key = p
            prev = by_key.get(key)
            if prev is None or mtime > prev[0]:
                by_key[key] = (mtime, label, p)
        merged = sorted(by_key.values(), key=lambda t: t[0], reverse=True)
        for _mt, rel_s, p in merged[:800]:
            rows.append((rel_s, p))
        return rows

    def refresh_library() -> None:
        if st.library_view_dir is not None:
            lib_view_lbl.value = f"Browsing (list only): {st.library_view_dir}"
            lib_view_lbl.visible = True
            btn_lib_reset_list.visible = True
        else:
            lib_view_lbl.visible = False
            btn_lib_reset_list.visible = False

        st.lib_rows = scan_library()
        st.file_to_cast = None
        st.cast_stream_rels = None
        lib_checks.clear()
        lib_cb_all.value = False
        lib_cast_hint.value = ""
        lib_list.controls.clear()

        if not st.lib_rows:
            if st.library_view_dir is not None:
                empty_lines = [
                    ft.Text("No files in this folder yet.", size=14, weight=ft.FontWeight.W_500, color=ft.Colors.GREY_200),
                    ft.Text(
                        f"Browsing: {st.library_view_dir}",
                        size=12,
                        color=ft.Colors.GREY_400,
                        selectable=True,
                    ),
                    ft.Text(
                        f"Save folder (Settings / downloads): {get_downloads_dir()}",
                        size=12,
                        color=ft.Colors.GREY_500,
                        selectable=True,
                    ),
                ]
            else:
                empty_lines = [
                    ft.Text("No files in the save folder yet.", size=14, weight=ft.FontWeight.W_500, color=ft.Colors.GREY_200),
                    ft.Text(
                        f"Primary folder: {get_downloads_dir()}",
                        size=12,
                        color=ft.Colors.GREY_400,
                        selectable=True,
                    ),
                ]
                if st.search_session_dir is not None:
                    empty_lines.append(
                        ft.Text(
                            f"Session folder (Search): {st.search_session_dir}",
                            size=12,
                            color=ft.Colors.GREY_400,
                            selectable=True,
                        )
                    )
                empty_lines.append(
                    ft.Text(
                        "Use Search & Download to fetch files, then press Refresh — or copy files here.",
                        size=12,
                        color=ft.Colors.GREY_500,
                    )
                )
            lib_list.controls.append(
                ft.Container(
                    padding=16,
                    content=ft.Column(
                        empty_lines,
                        spacing=6,
                        tight=True,
                    ),
                )
            )
        else:
            for idx, (rel, p) in enumerate(st.lib_rows[:400]):
                cb = ft.Checkbox(value=False, on_change=on_any_lib_check)
                lib_checks.append(cb)
                lib_list.controls.append(
                    ft.ListTile(
                        leading=cb,
                        title=ft.Text(rel[:95], size=13, color=ft.Colors.GREY_100),
                        subtitle=ft.Text(f"{p.stat().st_size} bytes", size=11, color=ft.Colors.GREY_500),
                    )
                )
        update_lib_sel_hint()
        st.library_loaded_once = True

    async def on_lib_use_save_folder(_: ft.ControlEvent) -> None:
        st.library_view_dir = None
        async with async_busy("Rebuilding library list…"):
            refresh_library()
        set_status("Library lists the save folder from Settings again.")
        page.update()

    btn_lib_reset_list.on_click = lambda e: asyncio.create_task(on_lib_use_save_folder(e))

    async def on_lib_ref(_: ft.ControlEvent) -> None:
        async with async_busy("Scanning library folder for files…"):
            refresh_library()
        set_status("")
        page.update()

    async def on_lib_open(_: ft.ControlEvent) -> None:
        try:
            ini = str(st.library_view_dir or get_downloads_dir())
            ini = ini if os.path.isdir(ini) else str(get_downloads_dir())
            async with async_busy("Opening folder browser…"):
                picked = await show_folder_browser_dialog(
                    page,
                    initial=ini,
                    title="Choose folder to list",
                    pick_mode=True,
                    dismiss_dialog_fn=dismiss_dialog,
                )
            if picked:
                st.library_view_dir = Path(picked).expanduser()
                set_status(f"Library lists: {picked}")
            async with async_busy("Scanning chosen folder…"):
                refresh_library()
        except Exception as e:
            set_status(str(e))
        page.update()

    tf_rename = ft.TextField(label="New filename", expand=True)

    async def on_rename(_: ft.ControlEvent) -> None:
        sel = [i for i, cb in enumerate(lib_checks) if cb.value]
        if len(sel) != 1:
            set_status("Select exactly one file to rename.")
            page.update()
            return
        rel, path = st.lib_rows[sel[0]]
        parts = rel.split("/", 1)
        tf_rename.value = parts[1] if len(parts) == 2 else ""

        dlg: ft.AlertDialog | None = None

        async def save_rename(_: ft.ControlEvent) -> None:
            new_name = (tf_rename.value or "").strip()
            if not new_name or ".." in new_name or "/" in new_name:
                dismiss_dialog(dlg)
                return
            dest = path.parent / new_name
            if dest.exists():
                set_status("A file with that name already exists.")
                dismiss_dialog(dlg)
                page.update()
                return
            try:
                path.rename(dest)
                set_status("Renamed.")
                refresh_library()
            except OSError as e:
                set_status(str(e))
            dismiss_dialog(dlg)
            page.update()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Rename file"),
            content=tf_rename,
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: dismiss_dialog(dlg)),
                ft.FilledButton("OK", on_click=save_rename),
            ],
        )
        page.show_dialog(dlg)
        page.update()

    async def on_del(_: ft.ControlEvent) -> None:
        sel = [i for i, cb in enumerate(lib_checks) if cb.value]
        if not sel:
            set_status("Select at least one file.")
            page.update()
            return
        paths = [st.lib_rows[i][1] for i in sel]
        lines = [st.lib_rows[i][0][:100] for i in sel[:25]]
        preview = "\n".join(lines)
        if len(sel) > 25:
            preview += f"\n… and {len(sel) - 25} more"

        async def confirm(_: ft.ControlEvent) -> None:
            deleted = 0
            last_err: str | None = None
            for p in paths:
                try:
                    p.unlink()
                    deleted += 1
                except OSError as e:
                    last_err = str(e)
            if last_err:
                set_status(last_err if deleted == 0 else f"Deleted {deleted}, then: {last_err}")
            else:
                set_status(f"Deleted {deleted} file(s).")
            refresh_library()
            dismiss_dialog(dlg_del)
            page.update()

        dlg_del = ft.AlertDialog(
            modal=True,
            title=ft.Text(f"Delete {len(paths)} file(s)?"),
            content=ft.Container(
                content=ft.Column(
                    [ft.Text(preview, size=12, selectable=True)],
                    scroll=ft.ScrollMode.AUTO,
                    tight=True,
                ),
                height=min(260, 80 + len(sel) * 18),
                width=420,
            ),
            actions=[
                ft.TextButton("Cancel", on_click=lambda _: dismiss_dialog(dlg_del)),
                ft.FilledButton("Delete", color=ft.Colors.RED, on_click=confirm),
            ],
        )
        page.show_dialog(dlg_del)
        page.update()

    async def on_browse_save(e: ft.ControlEvent) -> None:
        btn = e.control
        try:
            initial = (tf_save_root.value or "").strip() or str(get_downloads_dir())
            ini = initial if os.path.isdir(initial) else str(get_downloads_dir())
            async with async_busy("Opening folder browser…"):
                picked = await pick_folder_dialog(ini)
            if picked:
                tf_save_root.value = picked
                page.update()
        except Exception as ex:
            set_status(str(ex))
            page.update()
        finally:
            btn.disabled = False

    async def on_apply_save(_: ft.ControlEvent) -> None:
        raw = (tf_save_root.value or "").strip()
        if not raw:
            set_status("Enter a folder path.")
            page.update()
            return
        try:
            p = set_downloads_dir(Path(raw))
            tf_save_root.value = str(p)
            st.search_session_dir = None
            st.library_view_dir = None
            refresh_search_dl_folder_label()
            async with async_busy("Scanning files in the new save folder…"):
                refresh_library()
            set_status(f"Save folder: {p}")
        except OSError as e:
            set_status(str(e))
        page.update()

    lib_cast_hint = ft.Text("", size=12, color=ft.Colors.GREY_500)

    async def on_play(_: ft.ControlEvent) -> None:
        sel = [i for i, cb in enumerate(lib_checks) if cb.value]
        if not sel:
            set_status("Select one or more files.")
            page.update()
            return
        paths = [st.lib_rows[i][1] for i in sel]
        try:
            async with async_busy("Opening video/audio in external player…"):
                await asyncio.to_thread(play_media_files, paths)
            set_status("")
        except (OSError, ValueError) as e:
            set_status(str(e))
        page.update()

    async def on_prepare_cast(_: ft.ControlEvent) -> None:
        sel = [i for i, cb in enumerate(lib_checks) if cb.value]
        if not sel:
            set_status("Select at least one file.")
            page.update()
            return
        st.cast_stream_rels = [st.lib_rows[i][0] for i in sel]
        rel, _p = st.lib_rows[sel[0]]
        st.file_to_cast = rel
        if len(sel) > 1:
            set_status(f"Cast uses the first selected file ({len(sel)} selected).")
        elif rel.lower().endswith(".mkv"):
            set_status("MKV may not play on Chromecast; MP4 is safer.")
        else:
            set_status("")
        try:
            await ensure_cast_http()
        except Exception as e:
            set_status(f"HTTP server error: {e}")
            page.update()
            return
        w = get_cast_discovery_wait_s()
        async with async_busy(
            f"Scanning for Chromecast devices (waiting up to {w:.0f}s)…"
        ):
            st.cast_devices = await asyncio.to_thread(discover_chromecasts, w)
        rebuild_cast_list()
        update_cast_stream_urls()
        lib_cast_hint.value = "Chromecast tab — tick one or more devices, then Start casting."
        if st.main_tabs is not None:
            st.main_tabs.selected_index = 2
        if len(sel) > 1:
            set_status("Casting the first selected file — tick Chromecast device(s) in the Cast tab.")
        else:
            set_status("")
        page.update()

    btn_browse_save = ft.Button(content="Browse…", icon=ft.Icons.FOLDER_OPEN, on_click=on_browse_save)
    btn_apply_save = ft.FilledButton("Apply folder", icon=ft.Icons.CHECK, on_click=on_apply_save)

    btn_lib_ref = ft.Button(content="Refresh list", icon=ft.Icons.REFRESH, on_click=on_lib_ref)
    btn_lib_open = ft.OutlinedButton("Browse folder", icon=ft.Icons.FOLDER_OPEN, on_click=on_lib_open)
    btn_play = ft.Button(content="Play", icon=ft.Icons.PLAY_ARROW, on_click=on_play)
    btn_ren = ft.Button(content="Rename", icon=ft.Icons.DRIVE_FILE_RENAME_OUTLINE, on_click=on_rename)
    btn_del = ft.Button(content="Delete", icon=ft.Icons.DELETE_OUTLINE, color=ft.Colors.RED, on_click=on_del)
    btn_cast_prep = ft.Button(
        content="Prepare for Cast",
        icon=ft.Icons.CAST_CONNECTED,
        bgcolor=ft.Colors.GREEN_800,
        color=ft.Colors.WHITE,
        icon_color=ft.Colors.WHITE,
        on_click=on_prepare_cast,
    )

    cast_pick_hint = ft.Text(
        "Tick one or more devices — the same media is sent to all checked devices.",
        size=11,
        color=ft.Colors.GREY_500,
    )
    cast_list = ft.ListView(
        spacing=2,
        padding=ft.Padding.symmetric(vertical=4),
        expand=True,
        scroll=ft.ScrollMode.AUTO,
        clip_behavior=ft.ClipBehavior.HARD_EDGE,
    )
    cast_checks: list[ft.Checkbox] = []

    def update_cast_pick_hint() -> None:
        if not cast_checks:
            cast_pick_hint.value = "Discover devices, then tick the Chromecasts to use."
            return
        n = sum(1 for c in cast_checks if c.value)
        cast_pick_hint.value = f"{n} device(s) selected for cast / controls."

    def on_cast_select_all(e: ft.ControlEvent) -> None:
        v = bool(e.control.value)
        for cb in cast_checks:
            cb.value = v
        update_cast_pick_hint()
        page.update()

    def on_any_cast_check(_: ft.ControlEvent) -> None:
        if cast_checks and all(cb.value for cb in cast_checks):
            cast_cb_all.value = True
        else:
            cast_cb_all.value = False
        update_cast_pick_hint()
        page.update()

    cast_cb_all = ft.Checkbox(label="Select all devices", value=False, on_change=on_cast_select_all)

    cast_time_lbl = ft.Text("— / —", size=12, color=ft.Colors.GREY_400)

    cast_stream_urls_field = ft.TextField(
        label="Stream URL (prepared file or selection — paste into VLC / mpv on another device)",
        value="",
        read_only=True,
        multiline=True,
        min_lines=1,
        max_lines=12,
        width=520,
        text_size=12,
    )

    def update_cast_stream_urls() -> None:
        # After stop_cast_server() (e.g. HTTP idle), st.cast_port may be stale.
        if st.cast_port > 0 and not is_cast_server_running():
            st.cast_port = 0
        if st.cast_port <= 0:
            cast_stream_urls_field.value = ""
            return
        rels = getattr(st, "cast_stream_rels", None)
        if not rels:
            ftc = getattr(st, "file_to_cast", None)
            rels = [str(ftc)] if ftc else []
        if not rels:
            cast_stream_urls_field.value = ""
            return
        ip = get_lan_ip()
        p = st.cast_port
        cast_stream_urls_field.value = "\n".join(stream_url(str(r), ip, p) for r in rels if r)

    def rebuild_cast_list() -> None:
        cast_checks.clear()
        cast_cb_all.value = False
        cast_list.controls.clear()
        for i, c in enumerate(st.cast_devices):
            info = c.cast_info
            host = getattr(info, "host", "?")
            port = getattr(info, "port", None) or 8009
            cb = ft.Checkbox(value=False, on_change=on_any_cast_check)
            cast_checks.append(cb)
            cast_list.controls.append(
                ft.ListTile(
                    leading=cb,
                    title=ft.Text(info.friendly_name or "—", size=13),
                    subtitle=ft.Text(f"{info.model_name or '—'} · {host}:{port}", size=11),
                )
            )
        update_cast_pick_hint()

    def _indices_for_cast_controls() -> list[int]:
        ticked = [i for i, cb in enumerate(cast_checks) if cb.value]
        if ticked:
            return ticked
        last = [i for i in st.cast_last_play_idxs if i < len(st.cast_devices)]
        return last

    async def ensure_cast_http() -> None:
        # Check both st.cast_port and the real server state.
        # The idle timer may stop the server without resetting st.cast_port.
        if st.cast_port > 0 and is_cast_server_running():
            update_cast_stream_urls()
            return
        async with async_busy(
            "Starting local HTTP server for Chromecast (binding a free port on this PC)…"
        ):
            st.cast_port = 0
            port = await asyncio.to_thread(lambda: start_cast_server(port=0))
            st.cast_port = port
        ip = get_lan_ip()
        set_status(f"Cast HTTP server: port {port} ({ip})")
        update_cast_stream_urls()

    async def on_cast_disc(_: ft.ControlEvent) -> None:
        w = get_cast_discovery_wait_s()
        async with async_busy(
            f"Scanning network for Chromecast devices (waiting up to {w:.0f}s)…"
        ):
            st.cast_devices = await asyncio.to_thread(discover_chromecasts, w)
        st.cast_last_play_idxs.clear()
        rebuild_cast_list()
        update_cast_stream_urls()
        set_status(f"{len(st.cast_devices)} Chromecast device(s) found.")
        page.update()

    async def on_cast_play(_: ft.ControlEvent) -> None:
        if not st.file_to_cast:
            set_status("Library → Prepare for Cast first.")
            page.update()
            return
        try:
            await ensure_cast_http()
        except Exception as e:
            set_status(f"HTTP server error: {e}")
            page.update()
            return
        if st.cast_port <= 0:
            set_status("Could not start HTTP server.")
            page.update()
            return
        if not st.cast_devices:
            set_status("Discover devices first.")
            page.update()
            return
        idxs = [i for i, cb in enumerate(cast_checks) if cb.value]
        if not idxs:
            set_status("Tick at least one Chromecast.")
            page.update()
            return
        targets = [st.cast_devices[i] for i in idxs]
        ip = get_lan_ip()
        url = media_url(st.file_to_cast, ip, st.cast_port)
        mime = guess_mime_for_cast(Path(st.file_to_cast).name)
        try:
            async with async_busy(
                f"Sending stream to {len(targets)} Chromecast(s) (Default Media Receiver)…"
            ):
                await asyncio.to_thread(play_url_to_casts, targets, url, mime)
            st.cast_last_play_idxs = list(idxs)
            for i in idxs:
                if i < len(cast_checks):
                    cast_checks[i].value = True
            update_cast_pick_hint()
            st.cast_repeat_idx = 0
            st.cast_shuffle = False
            btn_cast_repeat.content = "Repeat: off"
            btn_cast_shuffle.content = "Shuffle: off"
            set_status(f"Casting on {len(targets)} device(s).")
            update_cast_stream_urls()
        except Exception as e:
            set_status(str(e))
        page.update()

    async def on_cast_play_pause(_: ft.ControlEvent) -> None:
        idxs = _indices_for_cast_controls()
        if not idxs:
            set_status("Select device(s) or start casting first.")
            page.update()
            return
        prog = await asyncio.to_thread(media_progress, st.cast_devices[idxs[0]])
        state = (prog[2] if prog else "") or ""
        try:
            if state == "PAUSED":
                for i in idxs:
                    await asyncio.to_thread(cast_play_receiver, st.cast_devices[i])
            else:
                for i in idxs:
                    await asyncio.to_thread(cast_pause, st.cast_devices[i])
        except Exception as e:
            set_status(str(e))
        page.update()

    async def on_cast_repeat(_: ft.ControlEvent) -> None:
        idxs = _indices_for_cast_controls()
        if not idxs:
            set_status("Select device(s) or start casting first.")
            page.update()
            return
        st.cast_repeat_idx = (st.cast_repeat_idx + 1) % 3
        modes = ("REPEAT_OFF", "REPEAT_ALL", "REPEAT_SINGLE")
        labels = ("off", "all", "one")
        mode = modes[st.cast_repeat_idx]
        btn_cast_repeat.content = f"Repeat: {labels[st.cast_repeat_idx]}"
        for i in idxs:
            try:
                await asyncio.to_thread(queue_set_repeat_mode, st.cast_devices[i], mode)
            except Exception as e:
                set_status(str(e))
        page.update()

    async def on_cast_shuffle(_: ft.ControlEvent) -> None:
        idxs = _indices_for_cast_controls()
        if not idxs:
            set_status("Select device(s) or start casting first.")
            page.update()
            return
        st.cast_shuffle = not st.cast_shuffle
        btn_cast_shuffle.content = "Shuffle: on" if st.cast_shuffle else "Shuffle: off"
        for i in idxs:
            try:
                await asyncio.to_thread(queue_set_shuffle, st.cast_devices[i], st.cast_shuffle)
            except Exception as e:
                set_status(str(e))
        page.update()

    async def on_cast_vol_change(e: ft.ControlEvent) -> None:
        v = float(e.control.value)
        idxs = _indices_for_cast_controls()
        if not idxs:
            set_status("Select device(s) or start casting first.")
            page.update()
            return
        for i in idxs:
            try:
                await asyncio.to_thread(set_receiver_volume, st.cast_devices[i], v)
            except Exception as ex:
                set_status(f"Volume: {ex}")
        page.update()

    async def on_cast_seek(e: ft.ControlEvent) -> None:
        if st.cast_prog_guard:
            return
        pos = float(e.control.value)
        idxs = _indices_for_cast_controls()
        if not idxs:
            return
        for i in idxs:
            try:
                await asyncio.to_thread(seek_media, st.cast_devices[i], pos)
            except Exception as ex:
                set_status(str(ex))
        page.update()

    async def on_cast_stop(_: ft.ControlEvent) -> None:
        idxs = _indices_for_cast_controls()
        if not idxs:
            set_status("Select device(s) or start casting first.")
            page.update()
            return
        for i in idxs:
            try:
                await asyncio.to_thread(stop_projection, st.cast_devices[i])
            except Exception as e:
                set_status(str(e))
        set_status("Cast stopped on selected device(s).")
        page.update()

    cast_seek_slider = ft.Slider(
        min=0,
        max=1,
        value=0,
        expand=True,
        label="Position",
        on_change_end=on_cast_seek,
    )
    cast_vol_slider = ft.Slider(
        min=0,
        max=1,
        value=0.85,
        width=320,
        divisions=20,
        label="Cast volume",
        on_change_end=on_cast_vol_change,
    )

    btn_cast_play_pause = ft.OutlinedButton(
        content="Pause",
        icon=ft.Icons.PAUSE,
        on_click=on_cast_play_pause,
    )
    btn_cast_disc = ft.Button(content="Discover Chromecasts", icon=ft.Icons.CAST, on_click=on_cast_disc)
    btn_cast_start = ft.FilledButton(
        content="Start casting",
        icon=ft.Icons.PLAY_CIRCLE,
        bgcolor=ft.Colors.GREEN_700,
        color=ft.Colors.WHITE,
        icon_color=ft.Colors.WHITE,
        on_click=on_cast_play,
    )
    btn_cast_stop = ft.Button(content="Stop", icon=ft.Icons.STOP, on_click=on_cast_stop)
    btn_cast_repeat = ft.OutlinedButton(
        content="Repeat: off",
        icon=ft.Icons.REPEAT,
        on_click=on_cast_repeat,
    )
    btn_cast_shuffle = ft.OutlinedButton(
        content="Shuffle: off",
        icon=ft.Icons.SHUFFLE,
        on_click=on_cast_shuffle,
    )

    def _refresh_cast_progress_ui() -> None:
        if getattr(st, "cast_prog_guard", False):
            return
        idxs = _indices_for_cast_controls()
        if not idxs or not st.cast_devices:
            return
        try:
            prog = media_progress(st.cast_devices[idxs[0]])
            if not prog:
                return
            cur, dur, state = prog
            st.cast_prog_guard = True
            if dur and dur > 0:
                cast_seek_slider.max = dur
                cast_seek_slider.value = min(max(cur, 0.0), float(dur))
                cast_time_lbl.value = f"{_format_duration_hms(cur)} / {_format_duration_hms(dur)}"
            else:
                cast_time_lbl.value = f"{_format_duration_hms(cur)} / —"
            if state == "PAUSED":
                btn_cast_play_pause.icon = ft.Icons.PLAY_ARROW
                btn_cast_play_pause.content = "Resume"
            else:
                btn_cast_play_pause.icon = ft.Icons.PAUSE
                btn_cast_play_pause.content = "Pause"
            st.cast_prog_guard = False
        except Exception:
            st.cast_prog_guard = False

    async def on_search_download_to_folder(e: ft.ControlEvent) -> None:
        btn = e.control
        try:
            initial = str(effective_search_download_dir())
            ini = initial if os.path.isdir(initial) else str(Path.home())
            async with async_busy("Opening folder browser…"):
                picked = await pick_folder_dialog(ini)
            if picked:
                st.search_session_dir = Path(picked)
                refresh_search_dl_folder_label()
                set_status(f"Session folder (this tab only): {picked}")
        except Exception as ex:
            set_status(str(ex))
        finally:
            btn.disabled = False
        page.update()

    btn_search_download_to = ft.OutlinedButton(
        content="Download to…",
        icon=ft.Icons.FOLDER_OPEN,
        on_click=on_search_download_to_folder,
    )

    tab_search_url = ft.Container(
        content=ft.Column(
            [
                ft.Container(height=4),
                # Do not wrap this block in a scrolling Column: TextField(expand=True) collapses to zero height.
                ft.Row(
                    [tf_query, btn_query],
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Row(
                    [
                        ft.Text("Search in:", size=12, color=ft.Colors.GREY_500),
                        cb_src_youtube,
                        cb_src_soundcloud,
                        cb_download_cover,
                    ],
                    spacing=14,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        ft.Text(
                            "Results (same list for keyword search or playlist/channel):",
                            weight=ft.FontWeight.BOLD,
                            expand=True,
                        ),
                        results_cb_all,
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Container(
                    col_results,
                    height=300,
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    border_radius=8,
                    padding=8,
                ),
                ft.Column(
                    [
                        ft.Row(
                            [
                                dd_results_fmt,
                                btn_search_download_to,
                                btn_play_selected,
                                btn_dl_results,
                            ],
                            spacing=8,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                        search_dl_settings_lbl,
                        ft.Row(
                            [search_dl_session_lbl, btn_clear_search_session],
                            spacing=8,
                            wrap=True,
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                        ),
                    ],
                    spacing=4,
                    tight=True,
                ),
            ],
            spacing=8,
            expand=True,
        ),
        expand=True,
        padding=ft.Padding.only(left=14, top=22, right=14, bottom=14),
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    tab_lib = ft.Container(
        content=ft.Column(
            [
                ft.Text(
                    "By default, this list shows downloads from your Settings save folder. "
                    "Library Browse only picks which folder to display — it does not change the download path. "
                    "Change the save location only under Settings.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                lib_view_lbl,
                ft.Row(
                    controls=[
                        ft.Text("Downloaded files", size=14, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_200),
                        btn_lib_open,
                        btn_lib_reset_list,
                        lib_cb_all,
                        btn_lib_ref,
                        btn_play,
                        btn_ren,
                        btn_del,
                        btn_cast_prep,
                    ],
                    wrap=True,
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                lib_sel_hint,
                ft.Container(
                    content=lib_list,
                    height=288,
                    bgcolor=ft.Colors.GREY_900,
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    border_radius=8,
                    padding=8,
                    clip_behavior=ft.ClipBehavior.ANTI_ALIAS,
                ),
                lib_cast_hint,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
            expand=True,
        ),
        padding=ft.Padding.only(left=14, top=22, right=14, bottom=14),
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
        expand=True,
    )

    cast_panel_left = ft.Container(
        content=ft.Column(
            [
                ft.Text("Network devices", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.GREY_200),
                ft.Row([btn_cast_disc], wrap=True),
                cast_pick_hint,
                cast_cb_all,
                ft.Container(
                    content=cast_list,
                    height=340,
                    border=ft.Border.all(1, ft.Colors.GREY_700),
                    border_radius=8,
                    clip_behavior=ft.ClipBehavior.HARD_EDGE,
                ),
            ],
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=1,
        padding=ft.Padding.only(right=8),
    )
    cast_panel_right = ft.Container(
        content=ft.Column(
            [
                cast_stream_urls_field,
                ft.Text(
                    "Playback",
                    weight=ft.FontWeight.W_600,
                    size=14,
                    color=ft.Colors.GREY_200,
                ),
                ft.Text(
                    "Volume, transport, repeat, and shuffle apply to checked devices or the last successful cast.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ft.Row([cast_time_lbl], alignment=ft.MainAxisAlignment.START),
                ft.Row([cast_seek_slider], vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row(
                    [
                        ft.Text("Volume", size=12, color=ft.Colors.GREY_500),
                        cast_vol_slider,
                    ],
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    wrap=True,
                ),
                ft.Row(
                    [
                        btn_cast_start,
                        btn_cast_play_pause,
                        btn_cast_stop,
                        btn_cast_repeat,
                        btn_cast_shuffle,
                    ],
                    wrap=True,
                ),
            ],
            spacing=8,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=1,
        padding=ft.Padding.only(left=8),
    )
    tab_cast = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        cast_panel_left,
                        ft.VerticalDivider(width=1, color=ft.Colors.GREY_700),
                        cast_panel_right,
                    ],
                    expand=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
            ],
            spacing=10,
            expand=True,
            scroll=ft.ScrollMode.AUTO,
        ),
        expand=True,
        padding=14,
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    tf_audio_player = ft.TextField(
        label="Audio player command",
        value=get_audio_player_command(),
        expand=True,
        hint_text="Empty = OS default · mpv · vlc",
    )
    tf_video_player = ft.TextField(
        label="Video player command",
        value=get_video_player_command(),
        expand=True,
        hint_text="Search “Play” stream: this first, then Audio command · mpv gets a window for audio-only",
    )
    tf_cast_disc_wait = ft.TextField(
        label="Chromecast discovery wait (seconds)",
        value=str(get_cast_discovery_wait_s()),
        width=280,
        hint_text="0.5–120 · used when scanning for devices",
    )
    settings_info_txt = ft.Text("", selectable=True, size=13, color=ft.Colors.GREY_200)
    ytdlp_update_msg = ft.Text("", size=12, color=ft.Colors.AMBER_200, visible=False)

    async def on_ytdlp_update_click(_: ft.ControlEvent) -> None:
        btn_ytdlp_update.disabled = True
        page.update()
        ok, log, new_v = False, "", "?"
        try:
            async with async_busy("Updating yt-dlp via pip (network download, can take several minutes)…"):
                ok, log = await asyncio.to_thread(pip_upgrade_ytdlp)
                mark_ytdlp_pypi_checked()
                new_v = await asyncio.to_thread(reload_ytdlp_module)
        finally:
            btn_ytdlp_update.disabled = False
        if ok:
            st.ytdlp_update_available = False
            st.ytdlp_pypi_latest = None
            st.ytdlp_update_note = (
                f"yt-dlp updated to {new_v}. Restart the app if downloads behave oddly."
            )
            set_status("yt-dlp updated.")
        else:
            set_status(f"yt-dlp update failed: {(log or '')[:280]}")
        refresh_settings_tab()
        page.update()

    btn_ytdlp_update = ft.FilledButton(
        "Update yt-dlp",
        icon=ft.Icons.DOWNLOAD,
        visible=False,
        on_click=on_ytdlp_update_click,
    )

    async def on_check_ytdlp_updates(_: ft.ControlEvent) -> None:
        """Manual PyPI check (not tied to the launch-interval cadence)."""
        btn_check_ytdlp_updates.disabled = True
        page.update()
        try:
            async with async_busy("Checking PyPI for the latest yt-dlp version…"):
                latest = await asyncio.to_thread(fetch_pypi_latest_ytdlp_version)
                installed = await asyncio.to_thread(get_installed_ytdlp_version)
            mark_ytdlp_pypi_checked()
            if latest and is_newer_pypi_version(latest, installed):
                st.ytdlp_update_available = True
                st.ytdlp_pypi_latest = latest
                st.ytdlp_update_note = None
                set_status(f"yt-dlp update available: {latest} — use Update yt-dlp below.")
            else:
                st.ytdlp_update_available = False
                st.ytdlp_pypi_latest = None
                st.ytdlp_update_note = None
                if latest:
                    set_status(f"yt-dlp is up to date (PyPI: {latest}, installed: {installed}).")
                else:
                    set_status("Could not read version from PyPI (offline or error).")
            refresh_settings_tab()
        finally:
            btn_check_ytdlp_updates.disabled = False
        page.update()

    btn_check_ytdlp_updates = ft.OutlinedButton(
        "Check for yt-dlp updates",
        icon=ft.Icons.NEW_RELEASES,
        on_click=on_check_ytdlp_updates,
    )

    def refresh_settings_tab() -> None:
        tf_save_root.value = str(get_downloads_dir())
        tf_audio_player.value = get_audio_player_command()
        tf_video_player.value = get_video_player_command()
        tf_cast_disc_wait.value = str(get_cast_discovery_wait_s())
        v = get_installed_ytdlp_version()
        settings_info_txt.value = f"yt-dlp (installed): {v}"
        if st.ytdlp_update_note:
            ytdlp_update_msg.value = st.ytdlp_update_note
            ytdlp_update_msg.color = ft.Colors.GREEN_400
            ytdlp_update_msg.visible = True
            btn_ytdlp_update.visible = False
        elif st.ytdlp_update_available and st.ytdlp_pypi_latest:
            ytdlp_update_msg.value = (
                f"Update available on PyPI: {st.ytdlp_pypi_latest} (installed: {v})"
            )
            ytdlp_update_msg.color = ft.Colors.AMBER_200
            ytdlp_update_msg.visible = True
            btn_ytdlp_update.visible = True
        else:
            ytdlp_update_msg.visible = False
            btn_ytdlp_update.visible = False

    async def on_save_player_settings(_: ft.ControlEvent) -> None:
        set_audio_player_command(tf_audio_player.value or "")
        set_video_player_command(tf_video_player.value or "")
        try:
            w = float((tf_cast_disc_wait.value or "3").strip())
            set_cast_discovery_wait_s(w)
        except ValueError:
            set_status("Chromecast wait: invalid number.")
            page.update()
            return
        set_status("Settings saved.")
        page.update()

    async def on_refresh_settings_info(_: ft.ControlEvent) -> None:
        async with async_busy("Reloading settings and version info…"):
            refresh_settings_tab()
        page.update()

    tab_settings = ft.Container(
        content=ft.Column(
            [
                ft.Text("Settings", weight=ft.FontWeight.BOLD, size=20, color=ft.Colors.GREY_200),
                ft.Text(
                    "Configure downloads, Chromecast discovery, and local playback. One Save applies everything below.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ft.Container(height=4),
                # — Downloads
                ft.Text("Downloads", weight=ft.FontWeight.W_600, size=15, color=ft.Colors.TEAL_200),
                ft.Text(
                    "Folder used for new files from Search & Download. Changing it does not affect Library “Browse” view.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ft.Row(
                    controls=[tf_save_root, btn_browse_save, btn_apply_save],
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.START,
                ),
                ft.Divider(height=1, color=ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                # — Chromecast
                ft.Text("Chromecast", weight=ft.FontWeight.W_600, size=15, color=ft.Colors.TEAL_200),
                ft.Text(
                    "How long to wait when scanning the network for Cast devices (Discover / Prepare for Cast).",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                tf_cast_disc_wait,
                ft.Divider(height=1, color=ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                # — Local playback
                ft.Text("Playback on this PC", weight=ft.FontWeight.W_600, size=15, color=ft.Colors.TEAL_200),
                ft.Text(
                    "Commands for Library → Play. Leave empty for the system default. "
                    "Multiple files open as a temporary playlist (Library order); command picked by file type.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                tf_audio_player,
                tf_video_player,
                ft.Row(
                    [
                        ft.FilledButton("Save settings", icon=ft.Icons.SAVE, on_click=on_save_player_settings),
                        ft.OutlinedButton("Refresh info", icon=ft.Icons.REFRESH, on_click=on_refresh_settings_info),
                    ],
                    wrap=True,
                ),
                ft.Divider(height=1, color=ft.Colors.with_opacity(0.35, ft.Colors.GREY_600)),
                ft.Text("About", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.GREY_300),
                settings_info_txt,
                ft.Text(
                    "yt-dlp: the app checks PyPI automatically from time to time. "
                    "You can also press “Check for yt-dlp updates” anytime; if a newer version exists, "
                    "use “Update yt-dlp” (runs pip in this Python environment).",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ft.Row(
                    [btn_check_ytdlp_updates, btn_ytdlp_update],
                    spacing=10,
                    wrap=True,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ytdlp_update_msg,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        ),
        expand=True,
        padding=18,
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    _DONATE_BMC_URL = "https://buymeacoffee.com/medcodex"
    _QR_COFFEE_PATH = Path(__file__).resolve().parent / "cofe.png"

    async def on_open_donate_url(_: ft.ControlEvent) -> None:
        await page.launch_url(_DONATE_BMC_URL)

    _donate_title = ft.Text("Donate", weight=ft.FontWeight.BOLD, size=20, color=ft.Colors.GREY_200)
    _donate_blurb = ft.Text(
        "Donations are always welcome. They help cover time spent on maintenance, "
        "testing, and new features. A huge thank you to everyone who chooses to support this project — "
        "your generosity is genuinely appreciated. "
        "Link and QR open the same page — scan the code on your phone if you prefer.",
        size=13,
        color=ft.Colors.GREY_300,
    )
    _donate_link_hdr = ft.Text("Donation link", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200)
    _donate_link_txt = ft.Text(
        _DONATE_BMC_URL,
        size=13,
        color=ft.Colors.TEAL_100,
        selectable=True,
    )
    _donate_open_btn = ft.FilledButton(
        "Open Buy Me a Coffee",
        icon=ft.Icons.LOCAL_CAFE,
        on_click=on_open_donate_url,
    )
    # Vertical stack only: Row + expand inside a scrolling Column can get zero size in TabBarView.
    _donate_qr_block: ft.Control
    if _QR_COFFEE_PATH.is_file():
        _donate_qr_block = ft.Container(
            content=ft.Image(
                src=str(_QR_COFFEE_PATH),
                width=200,
                height=200,
                fit=ft.BoxFit.CONTAIN,
                border_radius=8,
            ),
            padding=8,
            bgcolor=ft.Colors.with_opacity(0.12, ft.Colors.WHITE),
            border_radius=12,
            margin=ft.Margin.only(top=4),
        )
    else:
        _donate_qr_block = ft.Text(
            f"(QR image not found: add {_QR_COFFEE_PATH.name} next to main.py in flet_app.)",
            size=11,
            color=ft.Colors.AMBER_200,
        )

    tab_donate = ft.Container(
        content=ft.Column(
            [
                _donate_title,
                _donate_blurb,
                _donate_link_hdr,
                _donate_link_txt,
                _donate_open_btn,
                _donate_qr_block,
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            tight=True,
        ),
        expand=True,
        padding=18,
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    tab_credits = ft.Container(
        content=ft.Column(
            [
                ft.Text("Open source thanks", weight=ft.FontWeight.BOLD, size=20, color=ft.Colors.GREY_200),
                ft.Text(
                    "DLPulse builds on the work of many open-source projects and communities. "
                    "Thank you to their authors, maintainers, and contributors.",
                    size=13,
                    color=ft.Colors.GREY_300,
                ),
                ft.Container(height=8),
                ft.Text("Projects we rely on (non-exhaustive)", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200),
                ft.Text(
                    "• Flet & Flutter — cross-platform UI\n"
                    "• yt-dlp — media extraction and downloads from many sites\n"
                    "• Python — runtime and ecosystem\n"
                    "• PyChromecast & Zeroconf — Chromecast discovery and control\n"
                    "• Flask & Werkzeug — local HTTP serving for casting\n"
                    "• Mutagen — audio metadata when needed\n"
                    "• Packaging, Rich, Textual (CLI/TUI paths), and countless transitive libraries",
                    size=12,
                    color=ft.Colors.GREY_400,
                    selectable=True,
                ),
                ft.Container(height=10),
                ft.Text(
                    "If you maintain one of these projects: thank you. Bug reports and patches upstream help everyone.",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
        padding=18,
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    async def on_open_github_project(_: ft.ControlEvent) -> None:
        await page.launch_url(GITHUB_PROJECT_URL)

    async def on_open_github_releases_index(_: ft.ControlEvent) -> None:
        await page.launch_url(GITHUB_RELEASES_URL)

    _install_pkg_ver = get_app_package_version()
    _install_commit_sha = get_local_commit_sha()

    async def on_open_install_commit(_: ft.ControlEvent) -> None:
        if _install_commit_sha:
            await page.launch_url(commit_page_url(_install_commit_sha))

    _about_build_lines: list = [
        ft.Text("This install", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200),
        ft.Text(
            f"Package version: {_install_pkg_ver}",
            size=13,
            color=ft.Colors.GREY_300,
            selectable=True,
        ),
    ]
    if _install_commit_sha:
        _about_build_lines.extend(
            [
                ft.Text(
                    "Built from the following Git commit (same as CI / GitHub Actions for this artifact):",
                    size=11,
                    color=ft.Colors.GREY_500,
                ),
                ft.Text(
                    _install_commit_sha,
                    size=12,
                    color=ft.Colors.TEAL_100,
                    selectable=True,
                    font_family="monospace",
                ),
                ft.OutlinedButton(
                    "Open this commit on GitHub",
                    icon=ft.Icons.COMMIT,
                    on_click=on_open_install_commit,
                ),
            ]
        )
    else:
        _about_build_lines.append(
            ft.Text(
                "Git commit for this install is not recorded (unpackaged run, or build without "
                "``flet_app/build_commit.txt``). The update banner still compares to GitHub when possible.",
                size=11,
                color=ft.Colors.GREY_500,
            )
        )

    tab_about_app = ft.Container(
        content=ft.Column(
            [
                ft.Text("About DLPulse", weight=ft.FontWeight.BOLD, size=20, color=ft.Colors.GREY_200),
                *_about_build_lines,
                ft.Container(height=8),
                ft.Text(
                    "DLPulse is a desktop front-end around yt-dlp and a few helpers. "
                    "yt-dlp supports a very large list of sites — not only YouTube.",
                    size=13,
                    color=ft.Colors.GREY_300,
                ),
                ft.Container(height=6),
                ft.Text("Project page", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200),
                ft.Text(
                    GITHUB_PROJECT_URL,
                    size=13,
                    color=ft.Colors.TEAL_100,
                    selectable=True,
                ),
                ft.OutlinedButton(
                    "Open repository on GitHub",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=on_open_github_project,
                ),
                ft.Container(height=10),
                ft.Text("All releases", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200),
                ft.Text(
                    "Browse every tagged release and continuous builds on GitHub.",
                    size=12,
                    color=ft.Colors.GREY_400,
                ),
                ft.Text(
                    GITHUB_RELEASES_URL,
                    size=13,
                    color=ft.Colors.TEAL_100,
                    selectable=True,
                ),
                ft.OutlinedButton(
                    "Open releases page",
                    icon=ft.Icons.OPEN_IN_NEW,
                    on_click=on_open_github_releases_index,
                ),
                ft.Container(height=8),
                ft.Text("How to download from other sites", weight=ft.FontWeight.W_600, size=15, color=ft.Colors.TEAL_200),
                ft.Text(
                    "In the Search & Download tab, paste a page URL into the same field as keywords, then press "
                    "“Search / open URL”. For keyword search, tick YouTube and/or SoundCloud under “Search in”, then "
                    "search — results are merged when both are selected ([YT] / [SC] labels). yt-dlp resolves any "
                    "supported site (video, playlist, or channel where applicable).",
                    size=12,
                    color=ft.Colors.GREY_400,
                ),
                ft.Container(height=8),
                ft.Text("Tips", weight=ft.FontWeight.W_600, size=14, color=ft.Colors.TEAL_200),
                ft.Text(
                    "• Some sites need cookies or extra options — use yt-dlp’s supported URL patterns.\n"
                    "• If a site is not supported, yt-dlp will report an error in the status message.\n"
                    "• Chromecast and local playback work best with common formats (e.g. MP4).",
                    size=12,
                    color=ft.Colors.GREY_500,
                    selectable=True,
                ),
            ],
            spacing=10,
            scroll=ft.ScrollMode.AUTO,
            expand=True,
        ),
        expand=True,
        padding=18,
        gradient=_TAB_GRADIENT,
        border_radius=14,
        shadow=_TAB_PANEL_SHADOW,
    )

    async def on_tabs_change(e: ft.ControlEvent) -> None:
        try:
            idx = int(e.control.selected_index)
        except (TypeError, ValueError, AttributeError):
            return
        if idx == 1:
            if not st.library_loaded_once:
                async with async_busy("Loading library tab (scanning folders)…"):
                    refresh_library()
                page.update()
        elif idx == 2:
            update_cast_stream_urls()
            page.update()
        elif idx == 3:
            refresh_settings_tab()
            page.update()

    tabs = ft.Tabs(
        length=7,
        selected_index=0,
        on_change=on_tabs_change,
        content=ft.Column(
            [
                ft.TabBar(
                    tabs=[
                        ft.Tab(label="Search & Download", icon=ft.Icons.SEARCH),
                        ft.Tab(label="Library", icon=ft.Icons.VIDEO_LIBRARY),
                        ft.Tab(label="Chromecast", icon=ft.Icons.CAST),
                        ft.Tab(label="Settings", icon=ft.Icons.SETTINGS),
                        ft.Tab(label="Donate", icon=ft.Icons.VOLUNTEER_ACTIVISM),
                        ft.Tab(label="Open source", icon=ft.Icons.GROUP_WORK),
                        ft.Tab(label="About", icon=ft.Icons.INFO_OUTLINE),
                    ],
                    label_color=ft.Colors.GREY_100,
                    unselected_label_color=ft.Colors.GREY_500,
                    indicator_color=ft.Colors.TEAL_400,
                    divider_color=ft.Colors.GREY_700,
                ),
                ft.TabBarView(
                    controls=[
                        tab_search_url,
                        tab_lib,
                        tab_cast,
                        tab_settings,
                        tab_donate,
                        tab_credits,
                        tab_about_app,
                    ],
                    expand=True,
                ),
            ],
            expand=True,
        ),
        expand=True,
    )
    st.main_tabs = tabs

    async def on_github_banner_open(_: ft.ControlEvent) -> None:
        await page.launch_url(GITHUB_PROJECT_URL)

    async def on_github_banner_dismiss(_: ft.ControlEvent) -> None:
        if st.github_banner_remote_sha:
            set_github_update_dismissed_main_sha(st.github_banner_remote_sha)
        github_update_banner.visible = False
        page.update()

    github_banner_msg = ft.Text(
        "",
        size=12,
        color=ft.Colors.GREY_100,
        expand=True,
    )
    github_update_banner = ft.Container(
        visible=False,
        padding=ft.Padding.only(left=12, right=8, top=10, bottom=10),
        bgcolor=ft.Colors.with_opacity(0.22, ft.Colors.TEAL_900),
        border=ft.Border.all(1, ft.Colors.with_opacity(0.45, ft.Colors.TEAL_400)),
        border_radius=10,
        content=ft.Row(
            [
                ft.Icon(ft.Icons.NEW_RELEASES, color=ft.Colors.TEAL_200, size=22),
                github_banner_msg,
                ft.TextButton("Open GitHub", on_click=on_github_banner_open),
                ft.IconButton(
                    icon=ft.Icons.CLOSE,
                    tooltip="Dismiss until main advances again on GitHub",
                    icon_size=20,
                    icon_color=ft.Colors.GREY_400,
                    on_click=on_github_banner_dismiss,
                ),
            ],
            spacing=8,
            wrap=True,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        ),
    )

    async def _apply_github_update_check() -> None:
        try:
            info = await asyncio.to_thread(check_app_github_update)
        except Exception:
            return
        dismissed = get_github_update_dismissed_main_sha()
        if not info.show_banner:
            github_update_banner.visible = False
            page.update()
            return
        if (
            info.remote_main_sha
            and dismissed
            and dismissed.lower() == info.remote_main_sha.lower()
        ):
            github_update_banner.visible = False
            page.update()
            return
        github_banner_msg.value = info.message
        st.github_banner_remote_sha = info.remote_main_sha
        github_update_banner.visible = True
        page.update()

    async def _github_check_after_startup_delay() -> None:
        await asyncio.sleep(2.8)
        await _apply_github_update_check()

    async def _github_update_poll_loop() -> None:
        while True:
            await asyncio.sleep(6 * 3600)
            await _apply_github_update_check()

    startup_splash_ring = ft.ProgressRing(
        width=52,
        height=52,
        stroke_width=4,
        color=ft.Colors.TEAL_300,
    )
    startup_splash = ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.94, "#070b12"),
        alignment=ft.Alignment.CENTER,
        opacity=1,
        animate_opacity=ft.Animation(380, ft.AnimationCurve.EASE_OUT),
        content=ft.Column(
            [
                ft.Text(
                    "DLPulse",
                    size=28,
                    weight=ft.FontWeight.BOLD,
                    color=ft.Colors.TEAL_200,
                ),
                ft.Container(height=20),
                startup_splash_ring,
                ft.Container(height=16),
                ft.Text(
                    "Loading…",
                    size=14,
                    color=ft.Colors.GREY_400,
                ),
                ft.Text(
                    "Preparing library and settings",
                    size=12,
                    color=ft.Colors.GREY_600,
                ),
            ],
            horizontal_alignment=ft.CrossAxisAlignment.CENTER,
            tight=True,
        ),
    )

    main_body = ft.Container(
        content=ft.Column(
            [
                ft.Row(
                    [
                        ft.Text(
                            "DLPulse",
                            size=22,
                            weight=ft.FontWeight.BOLD,
                            color=ft.Colors.GREY_100,
                        ),
                        ft.Column(
                            [
                                status,
                                busy_row,
                            ],
                            tight=True,
                            horizontal_alignment=ft.CrossAxisAlignment.END,
                        ),
                    ],
                    alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                ),
                github_update_banner,
                dl_queue,
                tabs,
            ],
            spacing=10,
            expand=True,
        ),
        expand=True,
        gradient=_PAGE_GRADIENT,
    )

    page.add(
        ft.Stack(
            controls=[main_body, startup_splash],
            expand=True,
        )
    )

    async def _initial_load_task() -> None:
        """First frame shows the splash; then refresh library/settings and hide the overlay."""
        await asyncio.sleep(0.06)
        try:
            refresh_library()
            refresh_search_dl_folder_label()
            refresh_settings_tab()
            update_cast_stream_urls()
        finally:
            startup_splash.opacity = 0
            page.update()
            await asyncio.sleep(0.42)
            startup_splash.visible = False
            page.update()

    async def _cast_progress_loop() -> None:
        while st.cast_poll_run:
            await asyncio.sleep(1.2)
            try:
                _refresh_cast_progress_ui()
            except Exception:
                pass
            try:
                page.update()
            except Exception:
                pass

    page.run_task(_cast_progress_loop)

    async def _ytdlp_pypi_check_task() -> None:
        await asyncio.sleep(1.8)
        try:
            bump_app_launch_count()
            if not should_check_ytdlp_pypi():
                return
            latest = await asyncio.to_thread(fetch_pypi_latest_ytdlp_version)
            installed = await asyncio.to_thread(get_installed_ytdlp_version)
            mark_ytdlp_pypi_checked()
            if latest and is_newer_pypi_version(latest, installed):
                st.ytdlp_update_available = True
                st.ytdlp_pypi_latest = latest
                refresh_settings_tab()
                set_status(f"yt-dlp update available: {latest}")
                page.update()
        except Exception:
            try:
                mark_ytdlp_pypi_checked()
            except Exception:
                pass

    page.run_task(_ytdlp_pypi_check_task)
    page.run_task(_github_check_after_startup_delay)
    page.run_task(_github_update_poll_loop)

    page.update()
    page.run_task(_initial_load_task)


if __name__ == "__main__":
    # Linux GL/Wayland env is applied in _apply_linux_gl_env() before import flet.
    ft.run(main)