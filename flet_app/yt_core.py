"""
Shared download / YouTube logic for DLPulse (CLI, TUI, and Flet app).
"""
import os
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

import yt_dlp

# Swallows yt-dlp stderr-style messages during format retries (real failure still returned as exception).
class _YtdlpQuietLogger:
    def debug(self, msg: str) -> None:
        pass

    def info(self, msg: str) -> None:
        pass

    def warning(self, msg: str) -> None:
        pass

    def error(self, msg: str) -> None:
        pass


# Cookies: YT_COOKIES_FILE or YT_COOKIES_PATH (env), else cookies.txt next to this module.
def _cookiefile_path() -> str | None:
    path = os.environ.get("YT_COOKIES_FILE") or os.environ.get("YT_COOKIES_PATH")
    if path and os.path.isfile(path):
        return path
    default = Path(__file__).resolve().parent / "cookies.txt"
    return str(default) if default.is_file() else None


def _youtube_opts_extra() -> dict[str, Any]:
    """Align with Android YtdlpJson: ``player_client=android,web`` — web-only often returns None for some clips."""
    return {
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
        "sleep_interval": random.randint(1, 3),
        "sleep_interval_requests": 1,
        "ratelimit": 5_000_000,  # 5 MB/s max
    }


def normalize_youtube_radio_mix_url(url: str) -> str:
    """
    - Mix / Radio (list=RD...): keep only the current video (v= / youtu.be).
    - Watch + real playlist (list=PL..., OL..., etc., not RD): use canonical playlist URL,
      otherwise yt-dlp may mishandle watch?v=…&list=… (e.g. failure on a private item in the list).
    """
    u = (url or "").strip()
    if not u:
        return u
    if "music.youtube.com" in u:
        u = u.replace("music.youtube.com", "www.youtube.com", 1)
    parsed = urlparse(u)
    host = (parsed.netloc or "").lower()
    if host in ("youtu.be", "www.youtu.be", "m.youtu.be"):
        vid = (parsed.path or "").strip("/").split("/")[0]
        if len(vid) != 11:
            return u
        qs = parse_qs(parsed.query)
        lst = (qs.get("list") or [""])[0]
        if lst.startswith("RD"):
            return f"https://www.youtube.com/watch?v={vid}"
        if lst and not lst.startswith("RD"):
            q = urlencode({"list": lst})
            return urlunparse((parsed.scheme or "https", "www.youtube.com", "/playlist", "", q, ""))
        return u
    if "youtube.com" not in host:
        return u
    path = (parsed.path or "").rstrip("/") or "/"
    if path != "/watch":
        return u
    qs = parse_qs(parsed.query)
    v = (qs.get("v") or [""])[0]
    if not v:
        return u
    lst = (qs.get("list") or [""])[0]
    if lst.startswith("RD"):
        q = urlencode({"v": v})
        return urlunparse((parsed.scheme or "https", "www.youtube.com", "/watch", "", q, ""))
    if lst and not lst.startswith("RD"):
        q = urlencode({"list": lst})
        return urlunparse((parsed.scheme or "https", "www.youtube.com", "/playlist", "", q, ""))
    return u


def youtube_url_for_single_video_download(url: str) -> str:
    """
    For single-video download: collapse to ``https://www.youtube.com/watch?v=VIDEO_ID`` (no ``list=``).
    Flat playlist rows often have ``watch?v=…&list=PL…``; if we normalize to ``playlist?list=…``,
    yt-dlp would download the whole playlist again instead of the ticked clip.
    """
    u = (url or "").strip()
    if not u:
        return u
    if "music.youtube.com" in u:
        u = u.replace("music.youtube.com", "www.youtube.com", 1)
    parsed = urlparse(u)
    host = (parsed.netloc or "").lower()
    if host in ("youtu.be", "www.youtu.be", "m.youtu.be"):
        vid = (parsed.path or "").strip("/").split("/")[0]
        if len(vid) == 11:
            return f"https://www.youtube.com/watch?v={vid}"
        return u
    if "youtube.com" not in host:
        return u
    path = (parsed.path or "").rstrip("/") or "/"
    if path == "/watch":
        qs = parse_qs(parsed.query)
        v = (qs.get("v") or [""])[0]
        if v:
            return f"https://www.youtube.com/watch?v={v}"
    return u


# Programmatic fallbacks: try each format in order (avoids "format not available").
# besteffort = most permissive, accepts whatever is available.
FORMATS_VIDEO_TO_TRY = [
    "bestvideo[ext=mp4]+bestaudio[ext=m4a]",
    "bestvideo[ext=mp4]+bestaudio",
    "bestvideo[ext=webm]+bestaudio[ext=webm]",
    "bestvideo+bestaudio",
    "best[ext=mp4]",
    "best[ext=webm]",
    "best",
    "besteffort",
    "worst",
]
# YouTube often omits separate audio streams for the web client; include merge-then-extract fallbacks.
FORMATS_AUDIO_TO_TRY = [
    "bestaudio/best",
    "bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best",
    "ba/b",
    "bestaudio*",
    "bestaudio",
    "best[ext=mp4]/best[ext=webm]/best",
    "best",
    "bv*+ba/b",  # merge video+audio then FFmpeg postprocessor extracts audio
    "besteffort",
    "worst",
]

FORMAT_PRESETS = [
    ("Video — best quality (video+audio)", "bestvideo+bestaudio", None),
    ("Video 1080p", "bestvideo[height<=1080]+bestaudio", None),
    ("Video 720p", "bestvideo[height<=720]+bestaudio", None),
    ("Video 480p", "bestvideo[height<=480]+bestaudio", None),
    ("Video 360p", "bestvideo[height<=360]+bestaudio", None),
    ("Audio only — MP3 320 kbps", "bestaudio/best", [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "0"}]),
    ("Audio only — MP3 192 kbps", "bestaudio/best", [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "2"}]),
    ("Audio only — MP3 128 kbps", "bestaudio/best", [{"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "5"}]),
    ("Audio only — M4A (AAC)", "bestaudio/best", [{"key": "FFmpegExtractAudio", "preferredcodec": "m4a"}]),
    ("Audio only — OPUS", "bestaudio/best", None),
]


def extract_url_info(url: str, extract_flat: bool = False, *, normalize_url: bool = True) -> dict | None:
    """Extract metadata for a URL without downloading."""
    import sys

    if normalize_url:
        url = normalize_youtube_radio_mix_url(url)
    opts_base: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        # Playlists may include private/deleted clips; without this, extract_info aborts entirely.
        "ignoreerrors": True,
        "logger": _YtdlpQuietLogger(),
    }
    cookiefile = _cookiefile_path()
    if cookiefile:
        opts_base["cookiefile"] = cookiefile
    if extract_flat:
        opts_base["extract_flat"] = True

    last_err: Exception | None = None
    for extra in (_youtube_opts_extra(), {}):
        opts = {**opts_base, **extra}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            # yt-dlp may return None without raising (e.g. web client); must retry with fallback opts.
            if info is not None:
                return info
        except Exception as e:
            last_err = e
            continue
    if last_err:
        print("yt-dlp extract_info error:", str(last_err), file=sys.stderr)
    return None


def get_playlist_count(info: dict) -> int:
    """Return the number of entries in a playlist or channel tab."""
    if not info:
        return 0
    entries = info.get("entries") or []
    if entries is None:
        return 0
    if isinstance(entries, list):
        return len(entries)
    return sum(1 for _ in entries)


def detect_content_type(info: dict) -> tuple[str, str]:
    """Detect type: 'video', 'playlist', or 'channel'. Returns (type, description)."""
    if not info:
        return "unknown", "Could not determine type"
    kind = (info.get("_type") or "video").lower()
    title = info.get("title") or "Untitled"

    if kind == "playlist":
        count = get_playlist_count(info)
        if "channel" in (info.get("extractor") or "").lower() or (
            info.get("id") and "UC" in str(info.get("id", ""))
        ):
            return "channel", f"Channel: {title} ({count} videos)"
        return "playlist", f"Playlist: {title} ({count} videos)"
    return "video", f"Video: {title}"


def get_format_preset(index: int) -> tuple[str, dict] | None:
    """Return (format_spec, opts_extra) for the given preset index (0-based)."""
    if index < 0 or index >= len(FORMAT_PRESETS):
        return None
    _, format_spec, postprocessors = FORMAT_PRESETS[index]
    opts_extra: dict[str, Any] = {}
    if postprocessors:
        opts_extra["postprocessors"] = postprocessors
    # Video: force mp4 after merge (bestvideo+bestaudio)
    if index <= 4:
        opts_extra["merge_output_format"] = "mp4"
    # MP4 video (0–4) and MP3 (5–7): embed thumbnail + metadata for playback
    if index <= 4 or index in (5, 6, 7):
        opts_extra["writethumbnail"] = True
        opts_extra["embedthumbnail"] = True
        opts_extra["addmetadata"] = True
    return format_spec, opts_extra


def _is_format_not_available(err: Exception) -> bool:
    msg = (str(err) or "").lower()
    return "format is not available" in msg or "requested format" in msg


_POSTPROCESSOR_LABELS: dict[str, str] = {
    "FFmpegExtractAudio": "Extracting audio (MP3/M4A/…)",
    "FFmpegMerger": "Merging video + audio",
    "FFmpegVideoRemuxer": "Remuxing",
    "FFmpegVideoConvertor": "Converting video",
    "FFmpegSubtitlesConvertor": "Converting subtitles",
    "MoveFiles": "Finalizing file",
    "EmbedSubtitle": "Embedding subtitles",
    "XAttrMetadata": "Writing metadata",
    "MetadataFromField": "Metadata",
    "SponsorBlock": "SponsorBlock",
    "ModifyChapters": "Chapters",
    "FFmpegMetadata": "FFmpeg metadata",
    "EmbedThumbnail": "Embedding thumbnail",
}


def _format_speed(bps: float | None) -> str:
    if bps is None or bps <= 0:
        return ""
    if bps < 1024:
        return f"{bps:.0f} B/s"
    if bps < 1024 * 1024:
        return f"{bps / 1024:.1f} KiB/s"
    return f"{bps / 1024 / 1024:.1f} MiB/s"


def run_download(
    url: str,
    format_spec: str,
    opts_extra: dict,
    output_dir: str,
    no_playlist: bool = False,
    progress_callback: Callable[[dict[str, Any]], None] | None = None,
) -> tuple[bool, list[str], str | None]:
    """
    Run yt-dlp with programmatic fallbacks: try format_spec, then the format list until one succeeds.
    Returns (success, list of new filenames, error message or None).
    If no_playlist=True, download only the first video (same as --no-playlist).

    progress_callback: invoked from the yt-dlp thread with dicts
    {message, fraction, filename?, title?} — fraction 0..1 or None; filename is basename when known.
    """
    import sys

    def _basename_from_hook(d: dict[str, Any]) -> str:
        fn = d.get("filename") or d.get("tmpfilename")
        if fn:
            return os.path.basename(str(fn))
        info = d.get("info_dict") or {}
        fp = info.get("filepath") or info.get("_filename")
        if fp:
            return os.path.basename(str(fp))
        return ""

    def _title_from_hook(d: dict[str, Any]) -> str:
        info = d.get("info_dict") or {}
        t = info.get("title")
        return str(t).strip() if t else ""

    def hook_download(d: dict[str, Any]) -> None:
        if not progress_callback:
            return
        st = d.get("status")
        base = _basename_from_hook(d)
        title = _title_from_hook(d)
        if st == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            downloaded = d.get("downloaded_bytes") or 0
            frac: float | None = None
            if total and total > 0:
                frac = min(1.0, max(0.0, downloaded / float(total)))
            else:
                fi = d.get("fragment_index")
                fn = d.get("fragment_count")
                if fn and fn > 0 and fi is not None:
                    frac = min(1.0, max(0.0, float(fi) / float(fn)))
            parts: list[str] = ["Downloading"]
            if frac is not None:
                parts.append(f"{int(frac * 100)}%")
            sp = _format_speed(d.get("speed"))
            if sp:
                parts.append(sp)
            eta = d.get("eta")
            if eta is not None and eta > 0:
                parts.append(f"ETA {int(eta)}s")
            progress_callback(
                {
                    "message": " · ".join(parts),
                    "fraction": frac,
                    "filename": base,
                    "title": title,
                }
            )
        elif st == "finished":
            progress_callback(
                {
                    "message": "Download finished — processing…",
                    "fraction": 1.0,
                    "filename": base,
                    "title": title,
                }
            )
        elif st == "error":
            progress_callback({"message": "Download error", "fraction": None, "filename": base, "title": title})

    def hook_postprocess(d: dict[str, Any]) -> None:
        if not progress_callback:
            return
        st = d.get("status")
        pp = d.get("postprocessor") or ""
        label = _POSTPROCESSOR_LABELS.get(pp, pp.replace("_", " ") or "Processing")
        base = _basename_from_hook(d)
        title = _title_from_hook(d)
        if st == "started":
            progress_callback({"message": f"{label}…", "fraction": None, "filename": base, "title": title})
        elif st == "processing":
            progress_callback({"message": f"{label}…", "fraction": None, "filename": base, "title": title})

    def attach_hooks(opts: dict[str, Any]) -> None:
        if not progress_callback:
            return
        opts["progress_hooks"] = [hook_download]
        opts["postprocessor_hooks"] = [hook_postprocess]

    out_dir = (output_dir or os.getcwd()).strip()
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    before = set(Path(out_dir).iterdir()) if Path(out_dir).exists() else set()

    url = youtube_url_for_single_video_download(url)

    is_video_preset = "merge_output_format" in opts_extra
    formats_to_try = [format_spec] + (
        FORMATS_VIDEO_TO_TRY if is_video_preset else FORMATS_AUDIO_TO_TRY
    )
    # De-duplicate while preserving order
    seen = set()
    formats_to_try = [f for f in formats_to_try if f not in seen and not seen.add(f)]

    if progress_callback:
        progress_callback({"message": "Starting…", "fraction": None})

    # Must be False: with True, yt-dlp can finish without raising when the requested
    # format is unavailable (e.g. YouTube PO token / client quirks), so we would skip
    # format fallbacks and return success with an empty folder.
    base_opts: dict[str, Any] = {
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "quiet": True,
        "no_warnings": True,
        "ignoreerrors": False,
        "logger": _YtdlpQuietLogger(),
    }
    if no_playlist:
        base_opts["noplaylist"] = True
    cookiefile = _cookiefile_path()
    if cookiefile:
        base_opts["cookiefile"] = cookiefile
    base_opts.update(_youtube_opts_extra())
    base_opts.update(opts_extra)

    last_err: str | None = None
    for i, fmt in enumerate(formats_to_try):
        if progress_callback and i > 0:
            progress_callback(
                {
                    "message": f"Trying alternate format ({i + 1}/{len(formats_to_try)})…",
                    "fraction": None,
                }
            )
        opts = {**base_opts, "format": fmt}
        # Audio preset: merge video+audio so FFmpeg can extract MP3/M4A when pure bestaudio* is missing.
        if fmt == "bv*+ba/b" and not is_video_preset:
            opts["merge_output_format"] = "mp4"
        # On fallbacks (not the first = preset) drop format_sort — it can exclude formats on some videos
        if i > 0 and "format_sort" in opts:
            opts = {k: v for k, v in opts.items() if k != "format_sort"}
        if is_video_preset and i == 0:
            opts.setdefault("format_sort", ["res:1080", "ext:mp4:m4a", "tbr", "filesize"])
        # best/besteffort/worst = single stream, not merged
        if fmt in ("best", "besteffort", "worst") and "merge_output_format" in opts:
            opts = {k: v for k, v in opts.items() if k != "merge_output_format"}
        # besteffort: drop extractor_args (player_client=web) — some videos only work with default client
        if fmt == "besteffort":
            for key in ("extractor_args", "sleep_interval", "sleep_interval_requests", "ratelimit"):
                opts.pop(key, None)
        # Last resort (worst): minimal options (no extractor_args), still with cookies if present
        if fmt == "worst":
            opts = {
                "format": "worst",
                "outtmpl": base_opts["outtmpl"],
                "quiet": True,
                "no_warnings": True,
                "logger": _YtdlpQuietLogger(),
            }
            if no_playlist:
                opts["noplaylist"] = True
            cookiefile = _cookiefile_path()
            if cookiefile:
                opts["cookiefile"] = cookiefile
            pp = base_opts.get("postprocessors")
            if pp:
                opts["postprocessors"] = pp
        attach_hooks(opts)
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
            last_err = None
            break
        except Exception as e:
            last_err = str(e).strip() or "Unknown error"
            if _is_format_not_available(e):
                print("yt-dlp: format %r not available, trying next…" % (fmt,), file=sys.stderr)
                continue
            # Other error (geo, cookies, etc.) — do not try more formats
            print("yt-dlp download error:", last_err, file=sys.stderr)
            return False, [], last_err

    if last_err:
        print("yt-dlp download error (all format fallbacks failed):", last_err, file=sys.stderr)
        return False, [], last_err

    after = set(Path(out_dir).iterdir())
    new_files = [f.name for f in (after - before) if f.is_file()]
    return True, new_files, None


def fetch_playlist_entries(
    url: str, max_entries: int = 500, *, normalize_url: bool = True
) -> tuple[list[dict], str | None]:
    """
    List entries from a playlist or channel tab (flat), without downloading.
    Returns ([{id, title, url, thumbnail}, ...], error message or None).
    For YouTube Mix (``list=RD…``), use ``normalize_url=False`` and the original URL.
    """
    info = extract_url_info(url, extract_flat=True, normalize_url=normalize_url)
    if not info:
        return [], "Could not access the URL."
    if info.get("_type") != "playlist":
        return [], None
    entries = info.get("entries") or []
    if not isinstance(entries, list):
        entries = list(entries)
    result: list[dict] = []
    for e in entries[:max_entries]:
        if not e:
            continue
        vid = e.get("id") or ""
        if not vid:
            u = (e.get("url") or "").strip()
            vid = u.split("watch?v=")[-1].split("&")[0].strip()
        if not vid:
            continue
        title = e.get("title") or "Untitled"
        # Avoid &list=… on row URLs — otherwise download may be treated as a playlist.
        if isinstance(vid, str) and len(vid) == 11 and not vid.startswith("UC"):
            page_url = f"https://www.youtube.com/watch?v={vid}"
        else:
            page_url = (e.get("url") or "").strip() or f"https://www.youtube.com/watch?v={vid}"
        is_video_id = len(vid) == 11 and not vid.startswith("UC")
        thumb = e.get("thumbnail") or ""
        if not thumb and is_video_id:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        result.append({"id": vid, "title": title, "url": page_url, "thumbnail": thumb or ""})
    return result, None


def search_youtube(query: str, max_results: int = 10) -> list[dict]:
    """Search YouTube and return [{id, title, url, thumbnail}, ...]."""
    search_url = f"ytsearch{max_results}:{query}"
    info = extract_url_info(search_url, extract_flat=True)
    if not info or info.get("_type") != "playlist":
        return []
    entries = info.get("entries") or []
    if not isinstance(entries, list):
        entries = list(entries)
    result = []
    for e in entries:
        if not e:
            continue
        vid = e.get("id") or (e.get("url") or "").split("watch?v=")[-1].split("&")[0]
        if not vid:
            continue
        title = e.get("title") or "Untitled"
        url = e.get("url") or f"https://www.youtube.com/watch?v={vid}"
        # Only for 11-char video IDs; channel/playlist IDs differ → 404 on i.ytimg.com
        is_video_id = len(vid) == 11 and not vid.startswith("UC")
        thumb = e.get("thumbnail")
        if not thumb and is_video_id:
            thumb = f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
        result.append({"id": vid, "title": title, "url": url, "thumbnail": thumb or ""})
    return result


def _best_progressive_url_from_formats(formats: list | tuple) -> str | None:
    """Pick a single progressive URL (video+audio), preferring mp4 / reasonable height."""
    best: tuple[int, str] | None = None  # (height, url)
    for f in formats or []:
        if not isinstance(f, dict):
            continue
        u = f.get("url")
        if not isinstance(u, str) or not u.startswith("http"):
            continue
        ac, vc = f.get("acodec"), f.get("vcodec")
        if not ac or ac == "none" or not vc or vc == "none":
            continue
        h = f.get("height")
        try:
            hi = int(h) if h is not None else 0
        except (TypeError, ValueError):
            hi = 0
        if best is None or hi > best[0]:
            best = (hi, u)
    return best[1] if best else None


def extract_single_http_stream_url(page_url: str) -> str | None:
    """
    A single direct HTTPS URL (e.g. progressive YouTube), or None if only separate DASH
    or extraction failed.
    """
    url = youtube_url_for_single_video_download(page_url)
    if not url:
        return None
    cookiefile = _cookiefile_path()
    base_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": False,
        "logger": _YtdlpQuietLogger(),
        "noplaylist": True,
    }
    if cookiefile:
        base_opts["cookiefile"] = cookiefile

    format_try = [
        "best[vcodec!=none][acodec!=none][protocol^=http][protocol!=http_dash_segments][protocol!=m3u8_native]/best[vcodec!=none][acodec!=none]/18/22/best",
        "best[vcodec!=none][acodec!=none]/best",
        "best",
    ]
    for extra in (_youtube_opts_extra(), {}):
        for fmt in format_try:
            opts = {**base_opts, **extra, "format": fmt}
            if fmt == "best":
                opts.pop("merge_output_format", None)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(url, download=False)
            except Exception:
                continue
            if not info:
                continue
            u = info.get("url")
            if isinstance(u, str) and u.startswith("http"):
                return u
            prog = _best_progressive_url_from_formats(info.get("formats") or [])
            if prog:
                return prog
    return None


def extract_split_video_audio_stream_urls(page_url: str) -> tuple[str, str] | None:
    """For DASH: (video_url, audio_url). None if two streams are not available."""
    url = youtube_url_for_single_video_download(page_url)
    if not url:
        return None
    cookiefile = _cookiefile_path()
    base_opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignoreerrors": False,
        "logger": _YtdlpQuietLogger(),
        "noplaylist": True,
        "format": "bestvideo+bestaudio/bestvideo+ba/best",
    }
    if cookiefile:
        base_opts["cookiefile"] = cookiefile
    for extra in (_youtube_opts_extra(), {}):
        opts = {**base_opts, **extra}
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
        except Exception:
            continue
        if not info:
            continue
        rf = info.get("requested_formats")
        if not isinstance(rf, list) or len(rf) < 2:
            continue
        vurl: str | None = None
        aurl: str | None = None
        for x in rf:
            if not isinstance(x, dict):
                continue
            u = x.get("url")
            if not isinstance(u, str) or not u.startswith("http"):
                continue
            vc = (x.get("vcodec") or "none") != "none"
            ac = (x.get("acodec") or "none") != "none"
            if vc and not ac:
                vurl = u
            elif ac and not vc:
                aurl = u
        if vurl and aurl:
            return (vurl, aurl)
        urls: list[str] = []
        for x in rf:
            if isinstance(x, dict):
                u = x.get("url")
                if isinstance(u, str) and u.startswith("http"):
                    urls.append(u)
        if len(urls) >= 2:
            return (urls[0], urls[1])
    return None
