"""gRPC server interceptors (Bearer token authentication)."""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from typing import Any

import grpc

_PUBLIC_METHOD_PREFIXES = (
    "/grpc.health.v1.Health/",
    "/grpc.reflection.v1.ServerReflection/",
    "/grpc.reflection.v1alpha.ServerReflection/",
    "/tts.v1.TTSService/GetInfo",
    "/tts.v1.TTSService/ListVoices",
)


def _extract_bearer_token(invocation_metadata: tuple[tuple[str, str], ...] | None) -> str | None:
    if not invocation_metadata:
        return None
    for key, value in invocation_metadata:
        if key.lower() == "authorization":
            if value.lower().startswith("bearer "):
                return value[7:].strip()
            return None
    return None


class BearerAuthInterceptor(grpc.aio.ServerInterceptor):
    """Rejects unauthenticated gRPC calls except health and reflection endpoints."""

    def __init__(self, expected_token: str) -> None:
        self._expected_token = expected_token

    async def intercept_service(
        self,
        continuation: Callable[[grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        method = handler_call_details.method or ""
        if method.startswith(_PUBLIC_METHOD_PREFIXES):
            return await continuation(handler_call_details)

        token = _extract_bearer_token(handler_call_details.invocation_metadata)
        if token is None or not hmac.compare_digest(token, self._expected_token):
            return _unauthenticated_handler()

        return await continuation(handler_call_details)


def _unauthenticated_handler() -> grpc.RpcMethodHandler:
    async def _abort(_request: Any, context: grpc.aio.ServicerContext) -> None:
        await context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid or missing API token")

    return grpc.unary_unary_rpc_method_handler(_abort)
