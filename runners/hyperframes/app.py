"""HyperFrames runner: a Live Runner app that renders HTML to MP4 on the operator's host.

The orchestrator forwards each paid request to `POST /<anything>` here. The runner renders
with the `hyperframes` CLI, writes the MP4 under OUTPUT_DIR, which the Kit's Caddy serves at
PUBLIC_BASE_URL, and answers with the same `{"data": {...}}` envelope as the fleet's tool apps.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import signal
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from composition import RequestError, build_composition, parse_request

log = logging.getLogger("hyperframes-runner")

MAX_BACKGROUND_BYTES = 200 * 1024 * 1024
RENDER_TIMEOUT_S = 300.0


@dataclass(frozen=True)
class Config:
    orch_url: str
    orch_secret: str
    runner_url: str
    app: str
    price_usd: float
    capacity: int
    output_dir: Path
    public_base_url: str
    output_ttl_s: int
    listen_port: int
    render_cmd: tuple[str, ...]

    @classmethod
    def from_env(cls) -> "Config":
        def need(key: str) -> str:
            value = os.environ.get(key, "").strip()
            if not value:
                raise SystemExit(f"{key} is required")
            return value

        return cls(
            orch_url=need("ORCH_URL"),
            orch_secret=need("ORCH_SECRET"),
            runner_url=need("RUNNER_URL"),
            app=os.environ.get("APP_NAME", "community/hyperframes"),
            price_usd=float(os.environ.get("PRICE_USD", "0.008")),
            capacity=int(os.environ.get("CAPACITY", "2")),
            output_dir=Path(os.environ.get("OUTPUT_DIR", "/outputs")),
            public_base_url=need("PUBLIC_BASE_URL").rstrip("/"),
            output_ttl_s=int(os.environ.get("OUTPUT_TTL_S", str(24 * 3600))),
            listen_port=int(os.environ.get("PORT", "8993")),
            render_cmd=tuple(os.environ.get("RENDER_CMD", "hyperframes render").split()),
        )


class Runner:
    def __init__(self, config: Config, session: ClientSession | None = None) -> None:
        self.config = config
        self._session = session
        self._slots = asyncio.Semaphore(config.capacity)
        self.app = web.Application(client_max_size=1024 * 1024)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_post("/{tail:.*}", self.render)

    async def health(self, _request: web.Request) -> web.Response:
        return web.json_response({"status": "ok", "app": self.config.app})

    async def render(self, request: web.Request) -> web.Response:
        t0 = time.monotonic()
        try:
            req = parse_request(await request.json())
        except RequestError as exc:
            return web.json_response({"error": exc.code, "message": str(exc)}, status=400)
        except ValueError:
            return web.json_response({"error": "invalid_request", "message": "The body is not valid JSON."}, status=400)

        if self._slots.locked():
            return web.json_response({"error": "at_capacity", "message": "Every render slot is busy."}, status=503)
        async with self._slots:
            job_id = uuid.uuid4().hex
            work = Path(tempfile.mkdtemp(prefix=f"hf-{job_id}-"))
            try:
                background = None
                if req.background_video_url:
                    background = "background.mp4"
                    await self._download(req.background_video_url, work / background)
                (work / "index.html").write_text(build_composition(req, background))
                out = work / "out.mp4"
                await self._run_render(work, out)
                self.config.output_dir.mkdir(parents=True, exist_ok=True)
                final = self.config.output_dir / f"{job_id}.mp4"
                shutil.move(out, final)
            except RequestError as exc:
                return web.json_response({"error": exc.code, "message": str(exc)}, status=400)
            except RenderFailed as exc:
                log.warning("render %s failed: %s", job_id, exc.detail)
                return web.json_response({"error": "render_failed", "message": "The renderer could not produce a video."}, status=502)
            finally:
                shutil.rmtree(work, ignore_errors=True)

        elapsed_ms = int((time.monotonic() - t0) * 1000)
        return web.json_response({"data": {
            "url": f"{self.config.public_base_url}/{job_id}.mp4",
            "capability": self.config.app,
            "kind": "tool",
            "content_type": "video/mp4",
            "elapsed_ms": elapsed_ms,
            "metadata": {"width": req.width, "height": req.height, "duration_s": req.duration_s, "format": "mp4"},
            "units_metered": 1,
            "unit_kind": "call",
        }})

    async def _download(self, url: str, dest: Path) -> None:
        session = self._session or ClientSession()
        try:
            async with session.get(url, timeout=ClientTimeout(total=60), allow_redirects=False) as resp:
                if resp.status != 200:
                    raise RequestError("background_fetch_failed", f"background_video_url answered {resp.status}.")
                size = 0
                with dest.open("wb") as fh:
                    async for chunk in resp.content.iter_chunked(1 << 16):
                        size += len(chunk)
                        if size > MAX_BACKGROUND_BYTES:
                            raise RequestError("background_too_large", "background_video_url is larger than 200 MiB.")
                        fh.write(chunk)
        finally:
            if self._session is None:
                await session.close()

    async def _run_render(self, work: Path, out: Path) -> None:
        proc = await asyncio.create_subprocess_exec(
            *self.config.render_cmd, "-c", str(work / "index.html"), "-o", str(out),
            cwd=work, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), RENDER_TIMEOUT_S)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise RenderFailed("timed out") from None
        if proc.returncode != 0 or not out.exists() or out.stat().st_size == 0:
            raise RenderFailed(f"exit {proc.returncode}: {stdout.decode(errors='replace')[-2000:]}")


class RenderFailed(Exception):
    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail


async def sweep_outputs(config: Config) -> None:
    """Delete rendered files older than OUTPUT_TTL_S, once a minute."""
    while True:
        cutoff = time.time() - config.output_ttl_s
        for f in config.output_dir.glob("*.mp4"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass
        await asyncio.sleep(60)


async def main() -> None:
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    from livepeer_gateway.live_runner import register_runner

    config = Config.from_env()
    runner = Runner(config)
    site_runner = web.AppRunner(runner.app)
    await site_runner.setup()
    await web.TCPSite(site_runner, "0.0.0.0", config.listen_port).start()

    registration = None
    for attempt in range(1, 61):
        try:
            registration = await register_runner(
                config.orch_url, secret=config.orch_secret, runner_url=config.runner_url, app=config.app,
                mode="single-shot", price=config.price_usd, currency="USD", unit="fixed",
                capacity=config.capacity, auto_detect_gpu=False,
            )
            break
        except Exception as exc:  # noqa: BLE001 - the orchestrator may still be starting
            log.warning("register attempt %d failed: %s", attempt, exc)
            await asyncio.sleep(5)
    if registration is None:
        raise SystemExit("could not register with the orchestrator")
    log.info("registered app=%s price=$%s/call capacity=%d", config.app, config.price_usd, config.capacity)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    sweeper = asyncio.create_task(sweep_outputs(config))
    await stop.wait()
    sweeper.cancel()
    await registration.close()
    await site_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
