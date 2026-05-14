"""
Minimal threaded HTTP server for Chromecast: serves files from downloads/
with Range (206), same idea as /stream/ on the web app.

When no active /media/ connections remain for CAST_IDLE_STOP_SECONDS, the
registered Chromecast is stopped automatically (nothing left to stream).
"""
from __future__ import annotations

import logging
import hashlib
import os
import secrets
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from flask import Flask, Response, abort, request, send_file, stream_with_context
from werkzeug.exceptions import RequestedRangeNotSatisfiable
from werkzeug.utils import secure_filename

_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

from download_dir import get_downloads_dir
from ffmpeg_tools import apply_bundled_tool_path, find_ffmpeg

app = Flask(__name__)

_log = logging.getLogger(__name__)

apply_bundled_tool_path()

# Short-lived mapping for ``/upstream_t/<token>`` — lets ffmpeg read ``http://127.0.0.1/…``
# instead of HTTPS to Google CDN (libav TLS on Windows often hits -10054 resets).
_upstream_lock = threading.Lock()
_upstream_tokens: dict[str, tuple[float, str]] = {}
_UPSTREAM_TTL_S = 7200.0
_UPSTREAM_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

# Serialize yt-dlp extraction for /remote_stream (VLC often probes the next playlist item while the
# first is still streaming; concurrent YoutubeDL runs can break the second open on Windows).
_remote_stream_extract_lock = threading.Lock()

# After playback ends, in-flight HTTP usually drops to zero; brief gaps between
# Range requests are short. This delay avoids stopping mid-buffer.
CAST_IDLE_STOP_SECONDS = 120.0

_idle_lock = threading.Lock()
_active_media_requests = 0
_zero_since: float | None = None
_watcher_started = False
_http_idle_timer: threading.Timer | None = None
# After last /media/ byte transfer ends, stop the Flask thread if no new transfers (seconds).
HTTP_SERVER_IDLE_STOP_SECONDS = 600.0
# Last device we sent a cast to (for main-menu “stop last” without rescanning).
_last_cast_host_tuple: tuple[str, int, object, str | None, str | None] | None = None

# Devices to stop when /media/ stays idle (multi-cast: all targets).
_idle_host_tuples: list[tuple[str, int, object, str | None, str | None]] = []

# Extra absolute files selected through Library Browse / Player. Keys are served
# through the same /media/ and /stream/ routes as downloads.
_extra_media_paths: dict[str, Path] = {}


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


def register_media_path(path: Path) -> str:
    p = path.expanduser().resolve()
    if not p.is_file():
        raise FileNotFoundError(str(p))
    digest = hashlib.sha256(str(p).encode("utf-8")).hexdigest()[:16]
    rel = f"external/{digest}/{secure_filename(p.name) or p.name}"
    with _idle_lock:
        _extra_media_paths[rel] = p
    return rel


def _safe_path(rel: str) -> Path | None:
    if not rel or ".." in rel:
        return None
    rel_norm = rel.replace("\\", "/").lstrip("/")
    with _idle_lock:
        extra = _extra_media_paths.get(rel_norm)
    if extra is not None and extra.is_file():
        return extra
    base = get_downloads_dir().resolve()
    p = (base / rel_norm).resolve()
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


def _allowed_remote_page_url(u: str) -> bool:
    """Restrict ``/remote_stream`` to known media page hosts — not an open proxy."""
    try:
        p = urlparse((u or "").strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if host in ("youtu.be", "www.youtu.be", "m.youtu.be"):
        return True
    if host in ("music.youtube.com", "www.youtube.com", "m.youtube.com", "youtube.com"):
        return True
    if host.endswith(".youtube.com"):
        return True
    # SoundCloud (search results use https://soundcloud.com/… track pages)
    if host in ("soundcloud.com", "www.soundcloud.com", "m.soundcloud.com", "on.soundcloud.com"):
        return True
    if host.endswith(".soundcloud.com"):
        return True
    return False


def _allowed_stream_cdn_url(u: str) -> bool:
    """Hosts yt-dlp returns for raw media bytes — not arbitrary Internet URLs."""
    try:
        p = urlparse((u or "").strip())
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.netloc or "").lower().split("@")[-1].split(":")[0]
    if host.endswith(".googlevideo.com") or host == "googlevideo.com":
        return True
    if host.endswith(".googleusercontent.com"):
        return True
    if host.endswith(".sndcdn.com") or host == "sndcdn.com":
        return True
    return False


def _prune_upstream_tokens_unlocked() -> None:
    now = time.time()
    for k, (exp, _) in list(_upstream_tokens.items()):
        if exp < now:
            _upstream_tokens.pop(k, None)


def _register_upstream_token(remote_url: str) -> str | None:
    """Return opaque token for ``/upstream_t/<token>``, or None to use raw URL with ffmpeg."""
    if not _allowed_stream_cdn_url(remote_url):
        return None
    tok = secrets.token_urlsafe(32)
    with _upstream_lock:
        _prune_upstream_tokens_unlocked()
        _upstream_tokens[tok] = (time.time() + _UPSTREAM_TTL_S, remote_url)
    return tok


def _dash_inputs_via_local_tunnel(urls: list[str]) -> list[str] | None:
    """Rewrite CDN HTTPS URLs to ``http://127.0.0.1:<port>/upstream_t/…`` for ffmpeg inputs."""
    port = get_cast_server_port()
    if port <= 0:
        return None
    base = f"http://127.0.0.1:{port}"
    out: list[str] = []
    for u in urls:
        tok = _register_upstream_token(u)
        if tok is not None:
            out.append(f"{base}/upstream_t/{tok}")
        else:
            out.append(u)
    return out


def _proxy_upstream_bytes(media_url: str):
    """Pull from CDN with a browser-like UA (ffmpeg's default UA can get throttled)."""
    headers = {
        "User-Agent": _UPSTREAM_BROWSER_UA,
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
    }
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header
    upstream = urlopen(Request(media_url, headers=headers), timeout=60)
    _media_transfer_started()

    def _gen():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                upstream.close()
            finally:
                _media_transfer_ended()

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    status = getattr(upstream, "status", None) or (206 if range_header else 200)
    resp = Response(stream_with_context(_gen()), status=status, mimetype=content_type)
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Accept-Ranges"] = upstream.headers.get("Accept-Ranges") or "bytes"
    content_length = upstream.headers.get("Content-Length")
    content_range = upstream.headers.get("Content-Range")
    if content_length:
        resp.headers["Content-Length"] = content_length
    if content_range:
        resp.headers["Content-Range"] = content_range
    return resp


@app.route("/upstream_t/<token>", methods=["GET", "HEAD"])
def upstream_via_token(token: str):
    """Local HTTP relay of a CDN segment (see ``_dash_inputs_via_local_tunnel``)."""
    if not token or len(token) > 96:
        abort(400)
    with _upstream_lock:
        _prune_upstream_tokens_unlocked()
        entry = _upstream_tokens.get(token)
    if not entry:
        abort(404)
    exp, media_url = entry
    if time.time() > exp:
        with _upstream_lock:
            _upstream_tokens.pop(token, None)
        abort(404)
    if request.method == "HEAD":
        return Response(
            status=200,
            mimetype="application/octet-stream",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )
    return _proxy_upstream_bytes(media_url)


def _watch_html(video_src: str) -> str:
    """Minimal HTML5 player page (used by Settings → browser playback mode)."""
    from html import escape

    v = escape(video_src, quote=True)
    return (
        "<!DOCTYPE html><html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>DLPulse</title>"
        "<style>html,body{height:100%;margin:0;background:#0b0f14;color:#e2e8f0;"
        "font-family:system-ui,-apple-system,sans-serif}"
        "video{width:100%;max-height:96vh;display:block;margin:12px auto;background:#000;border-radius:8px}"
        "</style></head><body>"
        f"<video controls autoplay playsinline src=\"{v}\">"
        "Browser does not support HTML5 video.</video>"
        "<p style=\"text-align:center;font-size:12px;opacity:.55;margin-top:8px\">"
        "DLPulse · browser playback</p></body></html>"
    )


@app.route("/watch")
def watch_page():
    """
    Same-origin HTML5 ``<video>`` for browser playback mode (Windows/Linux).

    Query (one of):
      - ``p=<relative path>`` — file under downloads or ``register_media_path`` (served as ``/stream/…``).
      - ``relay=1`` + ``u=<page URL>`` — same allowlist as ``/remote_stream``.
      - ``dv=<video URL>`` and optional ``da=<audio URL>`` — passed to ``/direct_stream`` (yt-dlp resolved).
    """
    from urllib.parse import quote

    root = (request.host_url or "").rstrip("/")
    if not root:
        abort(500)

    dv = (request.args.get("dv") or "").strip()
    if dv:
        da = (request.args.get("da") or "").strip()
        if len(dv) > 9000 or (da and len(da) > 9000):
            abort(400)
        q = f"v={quote(dv, safe='')}"
        if da:
            q += f"&a={quote(da, safe='')}"
        video_src = f"{root}/direct_stream?{q}"
        return Response(_watch_html(video_src), mimetype="text/html; charset=utf-8")

    relay = (request.args.get("relay") or "").strip().lower() in ("1", "true", "yes")
    page_u = (request.args.get("u") or "").strip()
    if relay and page_u:
        if not _allowed_remote_page_url(page_u):
            abort(400)
        if len(page_u) > 4000:
            abort(400)
        video_src = f"{root}/remote_stream?u={quote(page_u, safe='')}"
        return Response(_watch_html(video_src), mimetype="text/html; charset=utf-8")

    p = (request.args.get("p") or "").strip().replace("\\", "/").lstrip("/")
    if not p or ".." in p:
        abort(400)
    path = _safe_path(p)
    if not path:
        abort(404)
    rel_q = quote(p, safe="/")
    video_src = f"{root}/stream/{rel_q}"
    return Response(_watch_html(video_src), mimetype="text/html; charset=utf-8")


def _proxy_remote_media(url: str):
    headers = {
        "User-Agent": request.headers.get(
            "User-Agent",
            "Mozilla/5.0 (X11; Linux x86_64) DLPulse/1.0",
        ),
        "Accept": request.headers.get("Accept", "*/*"),
    }
    range_header = request.headers.get("Range")
    if range_header:
        headers["Range"] = range_header

    upstream = urlopen(Request(url, headers=headers), timeout=30)
    _media_transfer_started()

    def _gen():
        try:
            while True:
                chunk = upstream.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                if upstream is not None:
                    upstream.close()
            finally:
                _media_transfer_ended()

    content_type = upstream.headers.get("Content-Type") or "application/octet-stream"
    status = getattr(upstream, "status", None) or (206 if range_header else 200)
    resp = Response(stream_with_context(_gen()), status=status, mimetype=content_type)
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Accept-Ranges"] = upstream.headers.get("Accept-Ranges") or "bytes"
    content_length = upstream.headers.get("Content-Length")
    content_range = upstream.headers.get("Content-Range")
    if content_length:
        resp.headers["Content-Length"] = content_length
    if content_range:
        resp.headers["Content-Range"] = content_range
    return resp


def _ffmpeg_mux_remote_media(*urls: str):
    ffmpeg = find_ffmpeg()
    clean = [u for u in urls if u]
    if not ffmpeg or not clean:
        return None
    tunneled = _dash_inputs_via_local_tunnel(clean)
    if tunneled is None:
        tunneled = list(clean)

    def _gen():
        proc: subprocess.Popen | None = None
        _media_transfer_started()
        try:
            # Remux without re-encoding. Inputs use ``http://127.0.0.1/.../upstream_t/…`` when
            # possible so ffmpeg uses HTTP to this process instead of libav TLS to Google (fewer
            # Windows -10054 resets); Python urllib pulls the CDN with a browser-like User-Agent.
            cmd = [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel",
                "warning",
                "-reconnect",
                "1",
                "-reconnect_streamed",
                "1",
                "-reconnect_delay_max",
                "5",
                "-i",
                tunneled[0],
            ]
            if len(tunneled) > 1:
                cmd.extend(
                    [
                        "-reconnect",
                        "1",
                        "-reconnect_streamed",
                        "1",
                        "-reconnect_delay_max",
                        "5",
                        "-i",
                        tunneled[1],
                        "-map",
                        "0:v:0",
                        "-map",
                        "1:a:0",
                    ]
                )
            else:
                cmd.extend(["-map", "0:v:0?", "-map", "0:a:0?"])
            cmd.extend(
                [
                    "-c",
                    "copy",
                    "-movflags",
                    "frag_keyframe+empty_moov+default_base_moof",
                    "-f",
                    "mp4",
                    "pipe:1",
                ]
            )
            _log.warning("remote_stream ffmpeg: %s", " ".join(cmd[:8] + ["..."]))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "AV_LOG_FORCE_NOCOLOR": "1"},
            )
            if proc.stdout is None:
                return
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                try:
                    err = proc.stderr.read(8192).decode("utf-8", "replace") if proc.stderr else ""
                except Exception:
                    err = ""
                if err.strip():
                    _log.warning("remote_stream ffmpeg stderr: %s", err.strip()[-2000:])
            _media_transfer_ended()

    resp = Response(stream_with_context(_gen()), mimetype="video/mp4")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Accept-Ranges"] = "none"
    return resp


@app.route("/remote_stream", methods=["GET", "HEAD"])
def remote_stream():
    """
    Stream in the player without downloading: resolve media via yt-dlp.
    - **DASH** (separate video + audio URLs): if ``ffmpeg`` is available, remux to one fragmented MP4
      with **stream copy** (no re-encode). CDN inputs are tunneled through ``/upstream_t/…`` so ffmpeg
      reads ``http://127.0.0.1`` (Python urllib → CDN) instead of libav TLS to Google (fewer Windows
      ``-10054`` disconnects); otherwise proxy the video track only.
    - **Single progressive HTTPS URL**: **HTTP proxy only** (no ffmpeg) — enough for external players.

    **HEAD** is answered without yt-dlp so VLC playlist probes do not race a running extraction.
    """
    page = (request.args.get("u") or "").strip()
    if not page or not _allowed_remote_page_url(page):
        abort(400)
    if request.method == "HEAD":
        return Response(
            status=200,
            mimetype="video/mp4",
            headers={"Accept-Ranges": "bytes", "Cache-Control": "no-store"},
        )
    from yt_core import extract_single_http_stream_url, extract_split_video_audio_stream_urls

    with _remote_stream_extract_lock:
        pair = extract_split_video_audio_stream_urls(page)
        ffmpeg = find_ffmpeg()
        muxed = None
        if pair and ffmpeg:
            muxed = _ffmpeg_mux_remote_media(pair[0], pair[1])
        if muxed is not None:
            return muxed
        direct = extract_single_http_stream_url(page)
    if direct:
        # Single progressive URL: HTTP proxy only (no ffmpeg re-encode). FFmpeg is for DASH mux above.
        return _proxy_remote_media(direct)
    if pair:
        return _proxy_remote_media(pair[0])
    abort(502)


def prewarm_remote_stream_extractions(pages: list[str]) -> None:
    """
    Run yt-dlp resolution for each page URL while holding the same lock as ``/remote_stream`` GET.

    VLC often opens the next playlist entry while the first stream is active; warming the rest
    reduces contention and failed second-item opens on Windows.
    """
    from yt_core import extract_single_http_stream_url, extract_split_video_audio_stream_urls

    for raw in pages:
        page = (raw or "").strip()
        if not page:
            continue
        with _remote_stream_extract_lock:
            try:
                pair = extract_split_video_audio_stream_urls(page)
                if not pair:
                    extract_single_http_stream_url(page)
            except Exception as ex:
                _log.debug("prewarm remote_stream: %s", ex)


@app.route("/direct_stream")
def direct_stream():
    """
    Mux pre-extracted video/audio URLs with ffmpeg immediately — no yt-dlp delay.

    Query params:
      v=<video_url>   required
      a=<audio_url>   optional (omit for single-stream sources)

    This endpoint is used by the embedded app player when stream URLs have
    already been resolved by yt-dlp in the Python layer.  ffmpeg starts
    instantly (no extraction wait), so libmpv never times out.

    Uses ``-c copy`` to remux without re-encoding (fast start, low CPU).
    Output format: fragmented MP4, which flet_video/media_kit handles more
    reliably than Matroska for embedded playback.
    """
    v = (request.args.get("v") or "").strip()
    a = (request.args.get("a") or "").strip()
    if not v:
        abort(400)

    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        abort(503)

    raw_inputs = [v]
    if a:
        raw_inputs.append(a)
    tunneled_ds = _dash_inputs_via_local_tunnel(raw_inputs)
    if tunneled_ds is None:
        tunneled_ds = raw_inputs
    v_in = tunneled_ds[0]
    a_in = tunneled_ds[1] if a and len(tunneled_ds) > 1 else None

    def _gen():
        proc: subprocess.Popen | None = None
        _media_transfer_started()
        try:
            cmd = [
                ffmpeg,
                "-nostdin",
                "-hide_banner",
                "-loglevel", "warning",
                "-reconnect", "1",
                "-reconnect_streamed", "1",
                "-reconnect_delay_max", "5",
                "-i", v_in,
            ]
            if a_in:
                cmd += [
                    "-reconnect", "1",
                    "-reconnect_streamed", "1",
                    "-reconnect_delay_max", "5",
                    "-i", a_in,
                    "-map", "0:v:0",
                    "-map", "1:a:0",
                ]
            else:
                cmd += ["-map", "0:v:0?", "-map", "0:a:0?"]
            cmd += [
                "-c",
                "copy",
                "-movflags",
                "frag_keyframe+empty_moov+default_base_moof",
                "-f",
                "mp4",
                "pipe:1",
            ]
            _log.info("direct_stream ffmpeg: %s", " ".join(cmd[:10] + ["..."]))
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env={**os.environ, "AV_LOG_FORCE_NOCOLOR": "1"},
            )
            if proc.stdout is None:
                return
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                yield chunk
        finally:
            if proc is not None:
                if proc.poll() is None:
                    proc.kill()
                try:
                    err = (
                        proc.stderr.read(8192).decode("utf-8", "replace")
                        if proc.stderr
                        else ""
                    )
                except Exception:
                    err = ""
                if err.strip():
                    _log.warning("direct_stream ffmpeg stderr: %s", err.strip()[-2000:])
            _media_transfer_ended()

    from flask import stream_with_context
    resp = Response(stream_with_context(_gen()), mimetype="video/mp4")
    resp.headers["Content-Disposition"] = "inline"
    resp.headers["Accept-Ranges"] = "none"
    return resp


_server_thread: threading.Thread | None = None
_server_port: int = 0
_server_instance = None  # werkzeug BaseWSGIServer, for shutdown


def is_cast_server_running() -> bool:
    """True if the HTTP server is running and bound to a port."""
    return (
        _server_thread is not None
        and _server_thread.is_alive()
        and _server_port > 0
        and _server_instance is not None
    )


def get_cast_server_port() -> int:
    """Bound Flask server port (0 if not running)."""
    return int(_server_port or 0)


def start_cast_server(host: str = "0.0.0.0", port: int = 0) -> int:
    """Start Flask on a free port (0 = OS-assigned). Returns the actual port.

    Binding is atomic via werkzeug (no TOCTOU window); errors from the worker thread
    are propagated through a Queue. Does not return until the server is listening.
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
            # port=0 → OS picks and binds atomically (no race).
            srv = make_server(host, port, app, threaded=True)
            actual_port = srv.server_address[1]
            _server_instance = srv
            startup_q.put(actual_port)   # success
            srv.serve_forever()
        except Exception as ex:
            startup_q.put(ex)            # error → propagate to caller

    _server_thread = threading.Thread(target=run, daemon=True, name="cast-http")
    _server_thread.start()

    try:
        result = startup_q.get(timeout=8.0)
    except _queue.Empty:
        _server_thread = None
        _server_instance = None
        _server_port = 0
        raise RuntimeError("Cast HTTP server did not start within 8 s (timeout).")

    if isinstance(result, Exception):
        _server_thread = None
        _server_instance = None
        _server_port = 0
        raise RuntimeError(f"Cast HTTP server failed to start: {result}") from result

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