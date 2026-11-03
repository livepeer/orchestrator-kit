"""Turn a render request into a HyperFrames composition.

A request carries `html` (a fragment or a full document), an optional background video,
an aspect ratio and a duration. A full document that already declares a composition
(`data-composition-id`) is rendered as given; anything else is wrapped in one root
composition so the renderer knows the canvas size and the duration.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from html import escape
from urllib.parse import urlparse

CANVAS = {"16:9": (1920, 1080), "9:16": (1080, 1920), "1:1": (1080, 1080)}
DEFAULT_ASPECT = "9:16"
DEFAULT_DURATION_S = 5.0
MAX_DURATION_S = 30.0
MAX_HTML_BYTES = 256 * 1024


class RequestError(ValueError):
    """A request the runner refuses. `code` is stable; the message is plain English."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class RenderRequest:
    html: str
    width: int
    height: int
    duration_s: float
    background_video_url: str | None


def parse_request(body: object) -> RenderRequest:
    if not isinstance(body, dict):
        raise RequestError("invalid_request", "The request body must be a JSON object.")
    params = body.get("params") or body.get("inputs") or body
    if not isinstance(params, dict):
        raise RequestError("invalid_request", "params must be a JSON object.")

    html = params.get("html")
    if not isinstance(html, str) or not html.strip():
        raise RequestError("html_required", "html is required.")
    if len(html.encode()) > MAX_HTML_BYTES:
        raise RequestError("html_too_large", f"html is larger than {MAX_HTML_BYTES // 1024} KiB.")

    aspect = params.get("aspect_ratio") or DEFAULT_ASPECT
    if aspect not in CANVAS:
        raise RequestError("invalid_aspect_ratio", f"aspect_ratio must be one of {', '.join(CANVAS)}.")

    raw_duration = params.get("duration", DEFAULT_DURATION_S)
    if isinstance(raw_duration, bool) or not isinstance(raw_duration, (int, float)):
        raise RequestError("invalid_duration", "duration must be a number of seconds.")
    duration = float(raw_duration)
    if not 0 < duration <= MAX_DURATION_S:
        raise RequestError("invalid_duration", f"duration must be between 0 and {MAX_DURATION_S:g} seconds.")

    bg = params.get("background_video_url")
    if bg is not None:
        if not isinstance(bg, str):
            raise RequestError("invalid_background_video_url", "background_video_url must be a URL.")
        check_public_url(bg)

    width, height = CANVAS[aspect]
    return RenderRequest(html=html, width=width, height=height, duration_s=duration, background_video_url=bg)


def check_public_url(url: str, resolve=socket.getaddrinfo) -> None:
    """Refuse anything but https to a public address, so a request cannot reach the operator's network."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or not parsed.hostname:
        raise RequestError("invalid_background_video_url", "background_video_url must be an https URL.")
    try:
        infos = resolve(parsed.hostname, parsed.port or 443, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise RequestError("invalid_background_video_url", "background_video_url host does not resolve.") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if not ip.is_global:
            raise RequestError("invalid_background_video_url", "background_video_url must point to a public address.")


def build_composition(req: RenderRequest, background_src: str | None) -> str:
    if "data-composition-id" in req.html:
        return req.html
    d = f"{req.duration_s:g}"
    video = ""
    if background_src:
        video = (
            f'<video src="{escape(background_src)}" data-start="0" data-duration="{d}" data-track-index="0" '
            'muted playsinline style="position:absolute;inset:0;width:100%;height:100%;object-fit:cover"></video>'
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\">"
        "<style>html,body{margin:0;background:#000;overflow:hidden}</style></head><body>"
        f'<div id="root" data-composition-id="root" data-width="{req.width}" data-height="{req.height}" data-duration="{d}" '
        f'style="position:relative;width:{req.width}px;height:{req.height}px">'
        f"{video}"
        f'<div data-start="0" data-duration="{d}" data-track-index="1" style="position:absolute;inset:0">{req.html}</div>'
        "</div></body></html>"
    )
