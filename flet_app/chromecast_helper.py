from __future__ import annotations

import socket
import time
from typing import Any

import pychromecast
from pychromecast import Chromecast
from pychromecast.const import MESSAGE_TYPE
from pychromecast.controllers.media import TYPE_QUEUE_UPDATE
from pychromecast.response_handler import WaitResponse

_CAST_READY_TIMEOUT_S = 30.0
_MEDIA_SESSION_WAIT_S = 25.0


def fresh_cast(cast: Chromecast) -> Chromecast:
    info = cast.cast_info
    port = info.port if info.port else 8009
    host_tuple = (info.host, port, info.uuid, info.model_name, info.friendly_name)
    return pychromecast.get_chromecast_from_host(host_tuple)


def get_lan_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


def discover_chromecasts(wait_s: float = 2.5) -> list[Chromecast]:
    chromecasts: list[Any]
    browser: Any
    chromecasts, browser = pychromecast.get_chromecasts()
    try:
        time.sleep(wait_s)
    finally:
        try:
            pychromecast.discovery.stop_discovery(browser)
        except Exception:
            pass
    return list(chromecasts)


def _sync_media_session(mc: Any, *, wait_s: float = _MEDIA_SESSION_WAIT_S) -> None:
    mc.update_status()
    mc.block_until_active(timeout=wait_s)


def host_tuple_from_cast(cast: Chromecast) -> tuple[str, int, Any, str | None, str | None]:
    info = cast.cast_info
    port = info.port if info.port else 8009
    return (info.host, port, info.uuid, info.model_name, info.friendly_name)


def play_url(
    cast: Chromecast,
    url: str,
    content_type: str,
    *,
    register_idle: bool = True,
) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    mc.play_media(url, content_type)
    mc.block_until_active(timeout=30)
    if register_idle:
        from cast_http import register_cast_idle_target

        register_cast_idle_target(host_tuple_from_cast(c))


def play_url_to_casts(casts: list[Chromecast], url: str, content_type: str) -> None:
    if not casts:
        raise ValueError("No Cast devices.")
    from cast_http import register_cast_idle_targets

    tups: list[tuple[str, int, Any, str | None, str | None]] = []
    for cast in casts:
        play_url(cast, url, content_type, register_idle=False)
        c = fresh_cast(cast)
        tups.append(host_tuple_from_cast(c))
    register_cast_idle_targets(tups)


def pause(cast: Chromecast) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    mc.pause()


def play(cast: Chromecast) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    mc.play()


def stop(cast: Chromecast) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    mc.stop()


def _stop_projection_connected(c: Chromecast) -> None:
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    try:
        _sync_media_session(mc, wait_s=8.0)
        mc.stop(timeout=10.0)
    except Exception:
        pass
    try:
        c.quit_app()
    except Exception:
        pass


def stop_projection(cast: Chromecast) -> None:
    from cast_http import clear_cast_idle_target, clear_last_cast_host

    clear_cast_idle_target()
    clear_last_cast_host()
    _stop_projection_connected(fresh_cast(cast))


def stop_projection_from_host_tuple(
    host_tuple: tuple[str, int, Any, str | None, str | None],
) -> None:
    c = pychromecast.get_chromecast_from_host(host_tuple)
    _stop_projection_connected(c)


def set_receiver_volume(cast: Chromecast, level: float) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    level = min(max(float(level), 0.0), 1.0)
    c.set_volume(level)


def seek_media(cast: Chromecast, position_sec: float) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    mc.seek(position_sec)


def queue_set_repeat_mode(cast: Chromecast, repeat_mode: str) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    response_handler = WaitResponse(10.0, "queue repeat")
    mc._send_command(
        {MESSAGE_TYPE: TYPE_QUEUE_UPDATE, "repeatMode": repeat_mode},
        response_handler.callback,
    )
    response_handler.wait_response()


def queue_set_shuffle(cast: Chromecast, shuffle: bool) -> None:
    c = fresh_cast(cast)
    c.wait(timeout=_CAST_READY_TIMEOUT_S)
    mc = c.media_controller
    _sync_media_session(mc)
    response_handler = WaitResponse(10.0, "queue shuffle")
    mc._send_command(
        {MESSAGE_TYPE: TYPE_QUEUE_UPDATE, "shuffle": shuffle},
        response_handler.callback,
    )
    response_handler.wait_response()


def media_progress(cast: Chromecast) -> tuple[float, float | None, str] | None:
    try:
        c = fresh_cast(cast)
        c.wait(timeout=_CAST_READY_TIMEOUT_S)
        mc = c.media_controller
        mc.update_status()
        time.sleep(0.15)
        st = mc.status
        if st is None:
            return None
        cur = float(st.adjusted_current_time or st.current_time or 0.0)
        dur = float(st.duration) if st.duration is not None else None
        state = str(st.player_state or "")
        return (cur, dur, state)
    except Exception:
        return None


def stop_last_cast() -> tuple[bool, str]:
    from cast_http import clear_cast_idle_target, clear_last_cast_host, get_last_cast_host_tuple

    tup = get_last_cast_host_tuple()
    if not tup:
        return False, "No cast device yet this session. Cast a file first."
    clear_cast_idle_target()
    try:
        stop_projection_from_host_tuple(tup)
        clear_last_cast_host()
        return True, "Casting stopped (last device)."
    except Exception as e:
        return False, str(e)
