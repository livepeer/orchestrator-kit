import asyncio
import socket
import stat
import sys
from pathlib import Path

import pytest
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).parent))

from app import Config, Runner  # noqa: E402
from composition import RequestError, build_composition, check_public_url, parse_request  # noqa: E402


def public(host, port, proto=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", port))]


def private(host, port, proto=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", port))]


def test_parse_defaults_and_params_envelope():
    req = parse_request({"params": {"html": "<b>hi</b>"}})
    assert (req.width, req.height, req.duration_s, req.background_video_url) == (1080, 1920, 5.0, None)
    assert parse_request({"html": "x", "aspect_ratio": "16:9", "duration": 3}).width == 1920


@pytest.mark.parametrize("body,code", [
    ([], "invalid_request"),
    ({"params": {}}, "html_required"),
    ({"html": "x", "aspect_ratio": "4:3"}, "invalid_aspect_ratio"),
    ({"html": "x", "duration": 0}, "invalid_duration"),
    ({"html": "x", "duration": 31}, "invalid_duration"),
    ({"html": "x", "duration": True}, "invalid_duration"),
    ({"html": "x" * (256 * 1024 + 1)}, "html_too_large"),
    ({"html": "x", "background_video_url": "http://example.com/a.mp4"}, "invalid_background_video_url"),
])
def test_parse_refusals(body, code):
    with pytest.raises(RequestError) as err:
        parse_request(body)
    assert err.value.code == code


def test_background_url_must_be_public():
    check_public_url("https://cdn.example.com/a.mp4", resolve=public)
    for url in ("https://metadata.google.internal/x", "https://cdn.example.com/a.mp4"):
        with pytest.raises(RequestError):
            check_public_url(url, resolve=private)


def test_composition_wraps_fragments_and_passes_documents_through():
    req = parse_request({"html": "<h1>Hello</h1>", "aspect_ratio": "1:1", "duration": 2.5})
    doc = build_composition(req, "background.mp4")
    assert 'data-composition-id="root" data-width="1080" data-height="1080" data-duration="2.5"' in doc
    assert '<video src="background.mp4"' in doc and "<h1>Hello</h1>" in doc
    full = '<html><body><div data-composition-id="mine" data-width="10" data-height="10"></div></body></html>'
    assert build_composition(parse_request({"html": full}), None) == full


def fake_renderer(tmp_path: Path, exit_code: int = 0) -> tuple[str, ...]:
    script = tmp_path / "fake-render.sh"
    script.write_text(
        "#!/bin/sh\n"
        "while [ $# -gt 0 ]; do case $1 in -o) out=$2; shift;; esac; shift; done\n"
        f"[ {exit_code} -eq 0 ] && printf 'mp4' > \"$out\"\nexit {exit_code}\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return (str(script),)


def config(tmp_path: Path, render_cmd, capacity=2) -> Config:
    return Config(orch_url="http://orch", orch_secret="s", runner_url="http://r", app="community/hyperframes",
                  price_usd=0.008, capacity=capacity, output_dir=tmp_path / "out",
                  public_base_url="https://gpu1.example.org/outputs", output_ttl_s=60, listen_port=0,
                  render_cmd=render_cmd)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_render_returns_the_tool_envelope_and_a_served_file(tmp_path):
    async def go():
        runner = Runner(config(tmp_path, fake_renderer(tmp_path)))
        async with TestClient(TestServer(runner.app)) as client:
            resp = await client.post("/community/hyperframes", json={"params": {"html": "<b>x</b>"}})
            assert resp.status == 200
            data = (await resp.json())["data"]
            assert data["url"].startswith("https://gpu1.example.org/outputs/") and data["url"].endswith(".mp4")
            assert data["capability"] == "community/hyperframes" and data["content_type"] == "video/mp4"
            name = data["url"].rsplit("/", 1)[1]
            assert (tmp_path / "out" / name).read_text() == "mp4"
    run(go())


def test_render_failure_and_bad_input_are_plain_refusals(tmp_path):
    async def go():
        runner = Runner(config(tmp_path, fake_renderer(tmp_path, exit_code=1)))
        async with TestClient(TestServer(runner.app)) as client:
            failed = await client.post("/x", json={"html": "<b>x</b>"})
            assert failed.status == 502 and (await failed.json())["error"] == "render_failed"
            bad = await client.post("/x", data="not json")
            assert bad.status == 400 and (await bad.json())["error"] == "invalid_request"
    run(go())
