"""Bearer token authentication for HTTP endpoints."""

from __future__ import annotations

import hmac

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def verify_bearer_token(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer_scheme),
) -> str:
    """Validate Authorization: Bearer <API_TOKEN> using constant-time comparison."""
    expected_token: str = request.app.state.settings.api_token
    if credentials is None or not hmac.compare_digest(
        credentials.credentials, expected_token
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or missing API token",
        )
    return credentials.credentials
