#!/usr/bin/env python3
"""
DLPulse — Textual TUI: search, URL/playlist, local library,
Chromecast (PyChromecast). Uses yt_core from the project root.
"""
from __future__ import annotations

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
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.screen import Screen
from textual.widgets import (
    Button,
    Checkbox,
    DataTable,
    Footer,
    Header,
    Input,
    Label,
    RichLog,
    Select,
    Static,
    TabbedContent,
    TabPane,
)

from cast_http import guess_mime_for_cast, media_url, start_cast_server
from chromecast_helper import (
    discover_chromecasts,
    get_lan_ip,
    play_url,
    stop_last_cast,
    stop_projection,
)
from paths import DOWNLOADS_DIR


class RenameScreen(Screen[tuple[str, str, str] | None]):
    """Rename a file in the library."""

    def __init__(self, job: str, name: str) -> None:
        super().__init__()
        self._job = job
        self._name = name

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label(f"New name (was: {self._name}):"),
            Input(value=self._name, id="ren-in"),
            Horizontal(
                Button("OK", id="ren-ok", variant="success"),
                Button("Cancel", id="ren-cancel"),
            ),
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "ren-ok":
            nv = self.query_one("#ren-in", Input).value.strip()
            if nv and nv != self._name:
                self.dismiss((self._job, self._name, nv))
            else:
                self.dismiss(None)
        elif event.button.id == "ren-cancel":
            self.dismiss(None)


class YtDesktopApp(App):
    TITLE = "DLPulse (TUI)"
    CSS = """
    Screen {
        layout: vertical;
    }
    /* Main area fills space between header and footer */
    #main-tabs {
        height: 1fr;
        min-height: 0;
    }
    TabbedContent {
        height: 1fr;
        min-height: 0;
    }
    TabPane {
        height: 1fr;
        min-height: 0;
    }
    .tab-pane-body {
        height: 1fr;
        min-height: 0;
        layout: vertical;
    }
    /* Primary content: lists / results grow; logs stay a fixed strip at bottom */
    .pane-label {
        height: auto;
        margin-top: 1;
        color: $text-muted;
    }
    #search-results, #pl-results {
        height: 1fr;
        min-height: 6;
        border: solid $primary;
    }
    #lib-table, #cast-table {
        height: 1fr;
        min-height: 8;
        border: solid $primary;
    }
    RichLog {
        height: 12;
        max-height: 40%;
        min-height: 6;
        border: solid $boost;
    }
    #cast-help, #cast-stop-hint {
        height: auto;
        margin: 0 0 1 0;
    }
    #url-info {
        height: auto;
        margin: 0 0 1 0;
    }
    #lib-cast-hint {
        height: auto;
    }
    #cast-srv-status {
        height: auto;
    }
    Horizontal Input {
        min-width: 8;
    }
    #search-q, #url-in {
        width: 1fr;
    }
    #cast-wait {
        width: 12;
    }
    #cast-name-filter {
        width: 1fr;
    }
    """

    BINDINGS = [Binding("q", "quit", "Quit", show=True)]

    def __init__(self) -> None:
        super().__init__()
        self._search_hits: list[dict] = []
        self._pl_hits: list[dict] = []
        self._cast_devices: list = []
        self._cast_port: int = 0
        self._file_to_cast: str | None = None
        self._lib_paths: list[str] = []

    def compose(self) -> ComposeResult:
        yield Header()
        with TabbedContent(id="main-tabs"):
            with TabPane("Search", id="tab-search"):
                yield Vertical(
                    Horizontal(
                        Input(placeholder="YouTube search…", id="search-q"),
                        Button("Search", id="btn-search", variant="primary"),
                    ),
                    Label("Results (tick rows, then download):", classes="pane-label"),
                    ScrollableContainer(id="search-results"),
                    Horizontal(
                        Select(
                            [(p[0], str(i)) for i, p in enumerate(FORMAT_PRESETS)],
                            value=str(0),
                            id="search-fmt",
                        ),
                        Button("Download selected", id="btn-dl-search", variant="success"),
                    ),
                    Label("Log:", classes="pane-label"),
                    RichLog(id="log-search", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
            with TabPane("URL / playlist", id="tab-url"):
                yield Vertical(
                    Horizontal(
                        Input(placeholder="https://youtube.com/…", id="url-in"),
                        Button("Inspect", id="btn-url-info", variant="primary"),
                    ),
                    Static("", id="url-info"),
                    Checkbox(label="Single video only (no playlist)", id="chk-nopl"),
                    Horizontal(
                        Select(
                            [(p[0], str(i)) for i, p in enumerate(FORMAT_PRESETS)],
                            value=str(0),
                            id="url-fmt",
                        ),
                        Button("Download URL", id="btn-dl-url", variant="success"),
                    ),
                    Label("Playlist / channel — tick entries, then download:", classes="pane-label"),
                    ScrollableContainer(id="pl-results"),
                    Button("Download selected from list", id="btn-dl-pl", variant="warning"),
                    Label("Log:", classes="pane-label"),
                    RichLog(id="log-url", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
            with TabPane("Library", id="tab-lib"):
                yield Vertical(
                    Horizontal(
                        Button("Refresh", id="btn-lib-refresh", variant="primary"),
                        Button("Open folder", id="btn-lib-open", variant="default"),
                    ),
                    Label("Files under downloads/: ", classes="pane-label"),
                    DataTable(cursor_type="row", id="lib-table"),
                    Horizontal(
                        Button("Rename", id="btn-lib-ren"),
                        Button("Delete", id="btn-lib-del", variant="error"),
                        Button("Prepare for Cast", id="btn-lib-cast", variant="success"),
                    ),
                    Static("", id="lib-cast-hint"),
                    Label("Log:", classes="pane-label"),
                    RichLog(id="log-lib", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
            with TabPane("Chromecast", id="tab-cast"):
                yield Vertical(
                    Static(
                        "Same flow as [bold]pure_cli[/]: HTTP → Discover → cast file from Library. "
                        "Optional name filter matches [bold]pure_cli cast --name[/]. "
                        "[bold]Stop last device[/] = pure_cli session memory (no rescan).",
                        id="cast-help",
                    ),
                    Static(
                        "[bold]Stop casting[/]: table row + button, or [bold]Stop last device[/] without selecting.",
                        id="cast-stop-hint",
                    ),
                    Horizontal(
                        Button("Start HTTP server", id="btn-cast-http", variant="primary"),
                        Button("Discover Chromecasts", id="btn-cast-disc", variant="primary"),
                    ),
                    Horizontal(
                        Input(placeholder="Discovery wait (seconds)", id="cast-wait", value="3"),
                        Input(
                            placeholder="Optional: device name contains… (like --name)",
                            id="cast-name-filter",
                        ),
                    ),
                    Static("", id="cast-srv-status"),
                    DataTable(cursor_type="row", id="cast-table"),
                    Horizontal(
                        Button("Stop last device", id="btn-cast-stop-last", variant="warning"),
                        Button("Stop casting (selected)", id="btn-cast-stop", variant="warning"),
                        Button("Start casting", id="btn-cast-play", variant="success"),
                    ),
                    Label("Log:", classes="pane-label"),
                    RichLog(id="log-cast", highlight=True, markup=True),
                    classes="tab-pane-body",
                )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#lib-table", DataTable).add_columns("File", "Size")
        self.query_one("#cast-table", DataTable).add_columns("Device", "Model", "Host:port")
        self._refresh_library_table()
        self.query_one("#lib-cast-hint", Static).update(
            "Select a row → “Prepare for Cast” → open the Chromecast tab."
        )

    def _log(self, wid: str, msg: str) -> None:
        self.query_one(wid, RichLog).write(msg)

    def _refresh_library_table(self) -> None:
        t = self.query_one("#lib-table", DataTable)
        t.clear()
        self._lib_paths = []
        if not DOWNLOADS_DIR.is_dir():
            return
        for job in sorted(DOWNLOADS_DIR.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
            if not job.is_dir():
                continue
            jid = job.name
            if ".." in jid or "/" in jid:
                continue
            try:
                for f in sorted(job.iterdir(), key=lambda x: x.name.lower()):
                    if not f.is_file():
                        continue
                    rel = f"{jid}/{f.name}"
                    self._lib_paths.append(rel)
                    t.add_row(rel, str(f.stat().st_size))
            except OSError:
                continue

    def _lib_selected_rel(self) -> str | None:
        table = self.query_one("#lib-table", DataTable)
        coord = table.cursor_coordinate
        if coord is None or coord.row < 0:
            return None
        if coord.row >= len(self._lib_paths):
            return None
        return self._lib_paths[coord.row]

    def _clear_container(self, cid: str) -> None:
        self.query_one(f"#{cid}").remove_children()

    def _mount_search_results(self) -> None:
        self._clear_container("search-results")
        box = self.query_one("#search-results", ScrollableContainer)
        for i, h in enumerate(self._search_hits):
            box.mount(Horizontal(Checkbox(id=f"sc_{i}"), Label(h.get("title", "")[:100])))

    def _mount_pl_results(self) -> None:
        self._clear_container("pl-results")
        box = self.query_one("#pl-results", ScrollableContainer)
        for i, h in enumerate(self._pl_hits):
            box.mount(Horizontal(Checkbox(id=f"pc_{i}"), Label(h.get("title", "")[:100])))

    @on(Button.Pressed, "#btn-search")
    def do_search(self) -> None:
        q = self.query_one("#search-q", Input).value.strip()
        if not q:
            self.notify("Enter a search query.")
            return
        self._log("#log-search", f"[yellow]Search:[/] {q}")
        self._search_hits = search_youtube(q, max_results=12)
        self._mount_search_results()
        self._log("#log-search", f"[green]{len(self._search_hits)} results[/]")

    @on(Button.Pressed, "#btn-url-info")
    def do_url_info(self) -> None:
        url = self.query_one("#url-in", Input).value.strip()
        if not url:
            self.notify("Enter a URL.")
            return
        info = extract_url_info(url)
        if not info:
            self.query_one("#url-info", Static).update("[red]URL unreachable.[/]")
            self._pl_hits = []
            self._clear_container("pl-results")
            return
        ctype, desc = detect_content_type(info)
        self.query_one("#url-info", Static).update(f"[green]{desc}[/] ({ctype})")
        self._pl_hits = []
        self._clear_container("pl-results")
        if ctype in ("playlist", "channel"):
            entries, err = fetch_playlist_entries(url, max_entries=200)
            if err:
                self._log("#log-url", f"[red]{err}[/]")
                return
            self._pl_hits = entries
            self._mount_pl_results()
            self._log("#log-url", f"[green]{len(entries)} entries[/] (same as [bold]pure_cli info[/]: tick to download)")
            for i, e in enumerate(entries[:30]):
                self._log(
                    "#log-url",
                    f"  [{i}] {(e.get('title') or '')[:70]}",
                )
            if len(entries) > 30:
                self._log("#log-url", f"  … {len(entries) - 30} more (table shows all loaded)")
        else:
            self._log("#log-url", "Single video — use “Download URL”. ([bold]pure_cli info[/] style)")

    def _selected_fmt(self, sel_id: str) -> int:
        v = self.query_one(sel_id, Select).value
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return 0

    @work(thread=True, exclusive=True)
    def _download_urls(self, urls: list[str], fmt_idx: int, no_pl: bool, log_id: str) -> None:
        preset = get_format_preset(fmt_idx)
        if not preset:
            self.call_from_thread(self._log, log_id, "[red]Invalid format[/]")
            return
        spec, extra = preset
        for u in urls:
            job = str(uuid.uuid4())
            out = DOWNLOADS_DIR / job
            out.mkdir(parents=True, exist_ok=True)
            self.call_from_thread(self._log, log_id, f"[cyan]↓[/] {u[:70]}…")
            ok, files, err = run_download(u, spec, extra, str(out), no_playlist=no_pl)
            if ok:
                self.call_from_thread(self._log, log_id, f"[green]OK:[/] {', '.join(files)}")
            else:
                self.call_from_thread(self._log, log_id, f"[red]{err or '?'}[/]")
        self.call_from_thread(self._refresh_library_table)

    @on(Button.Pressed, "#btn-dl-search")
    def dl_search(self) -> None:
        urls: list[str] = []
        for i, h in enumerate(self._search_hits):
            try:
                if self.query_one(f"#sc_{i}", Checkbox).value:
                    urls.append(h.get("url") or "")
            except Exception:
                continue
        urls = [u for u in urls if u]
        if not urls:
            self.notify("Tick one or more results.")
            return
        self._download_urls(urls, self._selected_fmt("#search-fmt"), True, "#log-search")

    @on(Button.Pressed, "#btn-dl-url")
    def dl_url_single(self) -> None:
        url = self.query_one("#url-in", Input).value.strip()
        if not url:
            return
        no_pl = self.query_one("#chk-nopl", Checkbox).value
        self._download_urls([url], self._selected_fmt("#url-fmt"), no_pl, "#log-url")

    @on(Button.Pressed, "#btn-dl-pl")
    def dl_pl_selected(self) -> None:
        urls: list[str] = []
        for i, h in enumerate(self._pl_hits):
            try:
                if self.query_one(f"#pc_{i}", Checkbox).value:
                    urls.append(h.get("url") or "")
            except Exception:
                continue
        urls = [u for u in urls if u]
        if not urls:
            self.notify("Tick one or more entries.")
            return
        self._download_urls(urls, self._selected_fmt("#url-fmt"), True, "#log-url")

    @on(Button.Pressed, "#btn-lib-refresh")
    def lib_refresh(self) -> None:
        self._refresh_library_table()
        self.notify("Refreshed.")

    @on(Button.Pressed, "#btn-lib-open")
    def lib_open(self) -> None:
        p = str(DOWNLOADS_DIR)
        os.makedirs(DOWNLOADS_DIR, exist_ok=True)
        try:
            if sys.platform == "darwin":
                os.system(f'open "{p}"')
            elif sys.platform == "win32":
                os.startfile(p)  # type: ignore[attr-defined]
            else:
                os.system(f'xdg-open "{p}"')
        except OSError as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-lib-ren")
    def lib_rename(self) -> None:
        rel = self._lib_selected_rel()
        if not rel:
            self.notify("Select a row.")
            return
        parts = rel.split("/", 1)
        if len(parts) != 2:
            return
        self.push_screen(RenameScreen(parts[0], parts[1]), self._after_rename)

    def _after_rename(self, result: tuple[str, str, str] | None) -> None:
        if not result:
            return
        job, old_n, new_n = result
        p = DOWNLOADS_DIR / job / old_n
        dest = DOWNLOADS_DIR / job / new_n
        if dest.exists():
            self.notify("That name already exists.")
            return
        try:
            p.rename(dest)
            self._refresh_library_table()
            self.notify("Renamed.")
        except OSError as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-lib-del")
    def lib_del(self) -> None:
        rel = self._lib_selected_rel()
        if not rel:
            self.notify("Select a row.")
            return
        p = DOWNLOADS_DIR / rel
        if not p.is_file():
            return
        try:
            p.unlink()
            jd = p.parent
            if jd.is_dir() and not any(jd.iterdir()):
                jd.rmdir()
            self._refresh_library_table()
            self.notify("Deleted.")
        except OSError as e:
            self.notify(str(e))

    @on(Button.Pressed, "#btn-lib-cast")
    def lib_prepare_cast(self) -> None:
        rel = self._lib_selected_rel()
        if not rel:
            self.notify("Select a file.")
            return
        self._file_to_cast = rel
        if rel.lower().endswith(".mkv"):
            self.notify("MKV may fail on the default receiver; MP4 is safer.")
        self.query_one("#lib-cast-hint", Static).update(f"Cast: [bold]{rel}[/] → Chromecast tab")
        self.notify("Ready for Cast.")

    @on(Button.Pressed, "#btn-cast-http")
    def cast_start_http(self) -> None:
        self._cast_port = start_cast_server(port=0)
        ip = get_lan_ip()
        self.query_one("#cast-srv-status", Static).update(
            f"[green]HTTP[/] http://{ip}:{self._cast_port}/media/…"
        )
        self._log("#log-cast", f"Port {self._cast_port}, IP LAN {ip}")

    @on(Button.Pressed, "#btn-cast-disc")
    def cast_discover(self) -> None:
        wait_s = 3.0
        try:
            w = self.query_one("#cast-wait", Input).value.strip()
            if w:
                wait_s = float(w)
                if wait_s < 0.5:
                    wait_s = 0.5
        except ValueError:
            self.notify("Invalid discovery wait; using 3s.")
            wait_s = 3.0
        self._log("#log-cast", f"Scanning for Chromecasts ([bold]pure_cli devices --wait {wait_s}[/])…")
        self._cast_devices = discover_chromecasts(wait_s=wait_s)
        ct = self.query_one("#cast-table", DataTable)
        ct.clear()
        for i, c in enumerate(self._cast_devices):
            info = c.cast_info
            host = getattr(info, "host", "?")
            port = getattr(info, "port", None) or 8009
            ct.add_row(
                info.friendly_name or "—",
                info.model_name or "—",
                f"{host}:{port}",
            )
            self._log(
                "#log-cast",
                f"  [{i}] {info.friendly_name}  [dim]host {host}:{port}  model {getattr(info, 'model_name', '?')}  uuid {getattr(info, 'uuid', '?')}[/]",
            )
        self._log("#log-cast", f"[green]{len(self._cast_devices)} device(s).[/] Use table row, or name filter + Start casting.")

    def _cast_selected_index(self) -> int | None:
        table = self.query_one("#cast-table", DataTable)
        coord = table.cursor_coordinate
        if coord is None or coord.row < 0:
            return None
        if coord.row >= len(self._cast_devices):
            return None
        return coord.row

    @on(Button.Pressed, "#btn-cast-play")
    def cast_play(self) -> None:
        if not self._file_to_cast:
            self.notify("Library → Prepare for Cast.")
            return
        if self._cast_port <= 0:
            self.notify("Start the HTTP server first.")
            return
        if not self._cast_devices:
            self.notify("Discover Chromecasts first.")
            return
        name_f = self.query_one("#cast-name-filter", Input).value.strip().lower()
        cast = None
        if name_f:
            for c in self._cast_devices:
                if name_f in (c.cast_info.friendly_name or "").lower():
                    cast = c
                    break
            if cast is None:
                self.notify("No device name matches (pure_cli cast --name).")
                return
        else:
            idx = self._cast_selected_index()
            if idx is None:
                self.notify("Select a row or set name filter (like pure_cli --index / --name).")
                return
            cast = self._cast_devices[idx]
        ip = get_lan_ip()
        url = media_url(self._file_to_cast, ip, self._cast_port)
        name = Path(self._file_to_cast).name
        mime = guess_mime_for_cast(name)
        self._log("#log-cast", f"{url}\n{mime}")
        try:
            play_url(cast, url, mime)
            self.notify("Casting started.")
        except Exception as e:
            self._log("#log-cast", f"[red]{e}[/]")
            self.notify(f"Error: {e}")

    @work(thread=True, exclusive=True)
    def _stop_last_cast_work(self) -> None:
        ok, msg = stop_last_cast()

        def show() -> None:
            self.notify(msg)
            self._log("#log-cast", f"[green]{msg}[/]" if ok else f"[yellow]{msg}[/]")

        self.call_from_thread(show)

    @on(Button.Pressed, "#btn-cast-stop-last")
    def cast_stop_last_btn(self) -> None:
        self._stop_last_cast_work()

    @on(Button.Pressed, "#btn-cast-stop")
    def cast_stop_btn(self) -> None:
        if not self._cast_devices:
            self.notify("Discover Chromecasts first, then select a row.")
            return
        idx = self._cast_selected_index()
        if idx is None:
            self.notify("Select a device row in the table, then Stop casting.")
            return
        try:
            stop_projection(self._cast_devices[idx])
            self.notify("Casting stopped.")
        except Exception as e:
            self.notify(str(e))

    def action_quit(self) -> None:
        self.exit()


def main() -> None:
    YtDesktopApp().run()


if __name__ == "__main__":
    main()
