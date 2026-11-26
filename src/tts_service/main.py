"""Unified entrypoint running HTTP (FastAPI) and gRPC servers in one process."""

from __future__ import annotations

import asyncio
import logging
import signal

import uvicorn

from tts_service.config import Settings
from tts_service.logging_config import configure_logging
from tts_service.services import SubsyncService, TTSService
from tts_service.transport.grpc.server import create_grpc_server, set_grpc_health_status
from tts_service.transport.http import create_app

logger = logging.getLogger(__name__)


async def serve(settings: Settings | None = None) -> None:
    """Load ML engines once and run enabled HTTP and gRPC servers concurrently."""
    cfg = settings or Settings()
    configure_logging(cfg.log_level)

    if cfg.api_token == "my_super_secret_token_123":
        logger.warning(
            "Using default API_TOKEN; set API_TOKEN in production environments"
        )

    tts_service = TTSService(cfg)
    subsync_service = SubsyncService(cfg)

    await asyncio.to_thread(tts_service.load)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def _request_shutdown() -> None:
        logger.info("Shutdown signal received, draining servers...")
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _request_shutdown)
        except NotImplementedError:  # pragma: no cover (Windows)
            pass

    grpc_server = None
    health_servicer = None
    if cfg.enable_grpc:
        grpc_server, health_servicer = create_grpc_server(cfg, tts_service, subsync_service)
        bind_addr = f"{cfg.grpc_host}:{cfg.grpc_port}"
        grpc_server.add_insecure_port(bind_addr)
        await grpc_server.start()
        await set_grpc_health_status(health_servicer, serving=True)
        logger.info("gRPC server listening on %s", bind_addr)

    uvicorn_server: uvicorn.Server | None = None
    http_task: asyncio.Task[None] | None = None
    if cfg.enable_http:
        fastapi_app = create_app(
            settings=cfg,
            tts_service=tts_service,
            subsync_service=subsync_service,
            manage_lifespan=False,
        )
        uv_config = uvicorn.Config(
            app=fastapi_app,
            host=cfg.http_host,
            port=cfg.http_port,
            log_level=cfg.log_level.lower(),
            # Signal handling is coordinated by serve() so both HTTP & gRPC drain cleanly.
        )
        uvicorn_server = uvicorn.Server(uv_config)
        uvicorn_server.install_signal_handlers = lambda: None  # type: ignore[method-assign]
        http_task = asyncio.create_task(uvicorn_server.serve())
        logger.info("HTTP server listening on http://%s:%s", cfg.http_host, cfg.http_port)

    if not cfg.enable_http and not cfg.enable_grpc:
        raise RuntimeError("At least one of ENABLE_HTTP or ENABLE_GRPC must be true")

    if http_task is not None:
        stop_waiter = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            {http_task, stop_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        for task in pending:
            task.cancel()
        if uvicorn_server is not None and not http_task.done():
            uvicorn_server.should_exit = True
            await http_task
    else:
        await stop_event.wait()

    if grpc_server is not None:
        if health_servicer is not None:
            await set_grpc_health_status(health_servicer, serving=False)
        await grpc_server.stop(grace=5.0)
        logger.info("gRPC server stopped")


def main() -> None:
    """CLI entrypoint."""
    asyncio.run(serve())


if __name__ == "__main__":
    main()
