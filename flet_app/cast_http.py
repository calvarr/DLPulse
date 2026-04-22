"""
Minimal threaded HTTP server for Chromecast: serves files from downloads/
with Range (206), same idea as /stream/ on the web app.

When no active /media/ connections remain for CAST_IDLE_STOP_SECONDS, the
registered Chromecast is stopped automatically (nothing left to stream).
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

from flask import Flask, abort, request, send_file
from werkzeug.exceptions import RequestedRangeNotSatisfiable
from werkzeug.utils import secure_filename

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from download_dir import get_downloads_dir

app = Flask(__name__)

_log = logging.getLogger(__name__)

# After playback ends, in-flight HTTP usually drops to zero; brief gaps between
# Range requests are short. This delay avoids stopping mid-buffer.
CAST_IDLE_STOP_SECONDS = 120.0

_idle_lock = threading.Lock()
_active_media_requests = 0
_zero_since: float | None = None
_watcher_started = False
_http_idle_timer: threading.Timer | None = None
# After last /media/ byte transfer ends, stop the Flask thread if no new transfers (seconds).
HTTP_SERVER_IDLE_STOP_SECONDS = 90.0
# Last device we sent a cast to (for main-menu “stop last” without rescanning).
_last_cast_host_tuple: tuple[str, int, object, str | None, str | None] | None = None

# Devices to stop when /media/ stays idle (multi-cast: all targets).
_idle_host_tuples: list[tuple[str, int, object, str | None, str | None]] = []


def register_cast_idle_targets(
    host_tuples: list[tuple[str, int, object, str | None, str | None]],
) -> None:
    """Remember Cast device(s) to stop when /media/ has no active transfers (simultaneous cast)."""
    global _idle_host_tuples, _last_cast_host_tuple
    with _idle_lock:
        _idle_host_tuples = list(host_tuples)
        _last_cast_host_tuple = host_tuples[-1] if host_tuples else None
    _ensure_idle_watcher()


def register_cast_idle_target(
    host_tuple: tuple[str, int, object, str | None, str | None],
) -> None:
    """Single-device cast (compat)."""
    register_cast_idle_targets([host_tuple])


def get_last_cast_host_tuple() -> tuple[str, int, object, str | None, str | None] | None:
    """Return the last device we cast to, for Stop casting without rediscovery."""
    with _idle_lock:
        return _last_cast_host_tuple


def clear_last_cast_host() -> None:
    global _last_cast_host_tuple
    with _idle_lock:
        _last_cast_host_tuple = None


def clear_cast_idle_target() -> None:
    """Clear idle targets (manual Stop casting or new session replacing the old one)."""
    global _idle_host_tuples, _zero_since
    with _idle_lock:
        _idle_host_tuples = []
        _zero_since = None


def _media_transfer_started() -> None:
    global _active_media_requests, _zero_since, _http_idle_timer
    with _idle_lock:
        _active_media_requests += 1
        _zero_since = None
        if _http_idle_timer is not None:
            try:
                _http_idle_timer.cancel()
            except Exception:
                pass
            _http_idle_timer = None


def _media_transfer_ended() -> None:
    global _active_media_requests, _zero_since
    with _idle_lock:
        _active_media_requests -= 1
        if _active_media_requests <= 0:
            _active_media_requests = 0
            _zero_since = time.time()
    _schedule_http_server_idle_stop()


def _schedule_http_server_idle_stop() -> None:
    """Stop the embedded HTTP server after idle if no active media Range transfers."""

    def _fire() -> None:
        global _http_idle_timer
        with _idle_lock:
            if _active_media_requests > 0:
                return
        try:
            stop_cast_server()
            _log.info("HTTP cast server stopped (idle, no active media transfers).")
        except Exception as ex:
            _log.debug("HTTP idle stop: %s", ex, exc_info=True)
        with _idle_lock:
            _http_idle_timer = None

    global _http_idle_timer
    with _idle_lock:
        if _http_idle_timer is not None:
            try:
                _http_idle_timer.cancel()
            except Exception:
                pass
        _http_idle_timer = threading.Timer(HTTP_SERVER_IDLE_STOP_SECONDS, _fire)
        _http_idle_timer.daemon = True
        _http_idle_timer.start()


def _ensure_idle_watcher() -> None:
    global _watcher_started
    with _idle_lock:
        if _watcher_started:
            return
        _watcher_started = True
    t = threading.Thread(target=_idle_watcher_loop, daemon=True, name="cast-idle-watch")
    t.start()


def _idle_watcher_loop() -> None:
    global _idle_host_tuples, _zero_since
    while True:
        time.sleep(5.0)
        to_stop: list[tuple] = []
        with _idle_lock:
            if not _idle_host_tuples:
                continue
            if _active_media_requests > 0:
                continue
            if _zero_since is None:
                continue
            if time.time() - _zero_since < CAST_IDLE_STOP_SECONDS:
                continue
            to_stop = list(_idle_host_tuples)
            _idle_host_tuples = []
            _zero_since = None
        if not to_stop:
            continue
        try:
            from chromecast_helper import stop_projection_from_host_tuple

            for tup in to_stop:
                try:
                    stop_projection_from_host_tuple(tup)
                except Exception as ex:
                    _log.debug("Idle stop one device: %s", ex, exc_info=True)
            clear_last_cast_host()
            _log.info(
                "Chromecast session(s) ended after HTTP idle (no active /media/ transfers), "
                "stopped %d device(s).",
                len(to_stop),
            )
        except Exception as e:
            _log.debug("Idle Chromecast stop failed: %s", e, exc_info=True)


def _safe_path(rel: str) -> Path | None:
    if not rel or ".." in rel:
        return None
    base = get_downloads_dir().resolve()
    p = (base / rel).resolve()
    try:
        p.relative_to(base)
    except ValueError:
        return None
    return p if p.is_file() else None


def _serve_media_file(rel_path: str):
    """Same file as under downloads/; used for ``/media/`` and ``/stream/``."""
    path = _safe_path(rel_path)
    if not path:
        abort(404)
    _media_transfer_started()
    try:
        import mimetypes

        mt, _ = mimetypes.guess_type(path.name)
        if not mt:
            mt = "application/octet-stream"
        size = path.stat().st_size
        dn = secure_filename(path.name) or path.name
        response = send_file(
            path,
            mimetype=mt,
            as_attachment=False,
            download_name=dn,
            max_age=0,
            conditional=False,
            etag=False,
        )
        response.headers.pop("Last-Modified", None)
        response.headers.pop("ETag", None)
        if mt.startswith("video/") or mt.startswith("audio/"):
            response.headers["Content-Disposition"] = "inline"
        try:
            response = response.make_conditional(
                request.environ, accept_ranges=True, complete_length=size
            )
        except RequestedRangeNotSatisfiable as e:
            resp = e.get_response(request.environ)
            resp.call_on_close(_media_transfer_ended)
            return resp
        if "Accept-Ranges" not in response.headers:
            response.headers["Accept-Ranges"] = "bytes"
        response.call_on_close(_media_transfer_ended)
        return response
    except Exception:
        _media_transfer_ended()
        raise


@app.route("/media/<path:rel_path>")
def serve_media(rel_path: str):
    return _serve_media_file(rel_path)


@app.route("/stream/<path:rel_path>")
def serve_stream(rel_path: str):
    """Alias of ``/media/`` — handy URL for players (VLC, mpv, browser) on other devices."""
    return _serve_media_file(rel_path)


_server_thread: threading.Thread | None = None
_server_port: int = 0
_server_instance = None  # werkzeug BaseWSGIServer, for shutdown


def is_cast_server_running() -> bool:
    """True dacă serverul HTTP este activ și ascultă pe un port."""
    return (
        _server_thread is not None
        and _server_thread.is_alive()
        and _server_port > 0
        and _server_instance is not None
    )


def start_cast_server(host: str = "0.0.0.0", port: int = 0) -> int:
    """Start Flask pe un port liber (0 = ales de OS). Returnează portul efectiv.

    Portul este legat *atomic* de werkzeug (fără fereastra TOCTOU), iar erorile
    din thread sunt propagate înapoi prin Queue. Nu returnează până serverul nu
    ascultă efectiv.
    """
    global _server_thread, _server_port, _server_instance
    if is_cast_server_running():
        return _server_port

    import queue as _queue
    from werkzeug.serving import make_server

    _server_instance = None
    startup_q: _queue.Queue = _queue.Queue()

    def run() -> None:
        global _server_instance, _server_port
        try:
            # port=0 → OS alege și leagă atomic (fără race condition).
            srv = make_server(host, port, app, threaded=True)
            actual_port = srv.server_address[1]
            _server_instance = srv
            startup_q.put(actual_port)   # succes
            srv.serve_forever()
        except Exception as ex:
            startup_q.put(ex)            # eroare → propagăm la caller

    _server_thread = threading.Thread(target=run, daemon=True, name="cast-http")
    _server_thread.start()

    try:
        result = startup_q.get(timeout=8.0)
    except _queue.Empty:
        _server_thread = None
        _server_instance = None
        _server_port = 0
        raise RuntimeError("Cast HTTP server nu a pornit în 8 s (timeout).")

    if isinstance(result, Exception):
        _server_thread = None
        _server_instance = None
        _server_port = 0
        raise RuntimeError(f"Cast HTTP server — eroare la pornire: {result}") from result

    _server_port = result
    return _server_port


def stop_cast_server() -> None:
    """Shut down the Flask HTTP server (safe to call if already stopped)."""
    global _server_instance, _server_thread, _server_port, _http_idle_timer
    with _idle_lock:
        if _http_idle_timer is not None:
            try:
                _http_idle_timer.cancel()
            except Exception:
                pass
            _http_idle_timer = None
    srv = _server_instance
    if srv is not None:
        try:
            srv.shutdown()
        except Exception:
            pass
        _server_instance = None
    _server_port = 0
    _server_thread = None


def media_url(relative_under_downloads: str, lan_ip: str, port: int) -> str:
    from urllib.parse import quote

    rel = relative_under_downloads.replace("\\", "/").lstrip("/")
    return f"http://{lan_ip}:{port}/media/{quote(rel, safe='/')}"


def stream_url(relative_under_downloads: str, lan_ip: str, port: int) -> str:
    """HTTP URL using ``/stream/…`` (same bytes as ``/media/…``)."""
    from urllib.parse import quote

    rel = relative_under_downloads.replace("\\", "/").lstrip("/")
    return f"http://{lan_ip}:{port}/stream/{quote(rel, safe='/')}"


def guess_mime_for_cast(name: str) -> str:
    n = (name or "").lower()
    if n.endswith(".mp4"):
        return "video/mp4"
    if n.endswith(".webm"):
        return "video/webm"
    if n.endswith(".mkv"):
        return "video/x-matroska"
    if n.endswith(".mp3"):
        return "audio/mpeg"
    if n.endswith(".m4a"):
        return "audio/mp4"
    if n.endswith(".opus") or n.endswith(".ogg"):
        return "audio/ogg"
    return "video/mp4"