"""Async gRPC server factory with health checking and optional reflection."""

from __future__ import annotations

import logging

import grpc
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from grpc_reflection.v1alpha import reflection

from tts_service.config import Settings
from tts_service.services import SubsyncService, TTSService
from tts_service.transport.grpc.gen.tts.v1 import tts_pb2, tts_pb2_grpc

from .interceptors import BearerAuthInterceptor
from .servicer import TTSGrpcServicer

logger = logging.getLogger(__name__)


def create_grpc_server(
    settings: Settings,
    tts_service: TTSService,
    subsync_service: SubsyncService,
) -> tuple[grpc.aio.Server, health.aio.HealthServicer]:
    """Create and configure the grpc.aio.Server (not yet started)."""
    options = [
        ("grpc.max_send_message_length", settings.grpc_max_message_bytes),
        ("grpc.max_receive_message_length", settings.grpc_max_message_bytes),
        ("grpc.keepalive_time_ms", 30_000),
        ("grpc.keepalive_timeout_ms", 10_000),
        ("grpc.keepalive_permit_without_calls", True),
    ]

    server = grpc.aio.server(
        interceptors=[BearerAuthInterceptor(settings.api_token)],
        options=options,
    )

    tts_pb2_grpc.add_TTSServiceServicer_to_server(
        TTSGrpcServicer(tts_service=tts_service, subsync_service=subsync_service),
        server,
    )

    health_servicer = health.aio.HealthServicer()
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    if settings.grpc_reflection:
        service_names = (
            tts_pb2.DESCRIPTOR.services_by_name["TTSService"].full_name,
            health.SERVICE_NAME,
            reflection.SERVICE_NAME,
        )
        reflection.enable_server_reflection(service_names, server)

    return server, health_servicer


async def set_grpc_health_status(
    health_servicer: health.aio.HealthServicer,
    serving: bool,
) -> None:
    """Update gRPC health status for both overall server and TTSService."""
    status = (
        health_pb2.HealthCheckResponse.SERVING
        if serving
        else health_pb2.HealthCheckResponse.NOT_SERVING
    )
    tts_service_name = tts_pb2.DESCRIPTOR.services_by_name["TTSService"].full_name
    await health_servicer.set("", status)
    await health_servicer.set(tts_service_name, status)
