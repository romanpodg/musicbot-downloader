"""FastAPI transport adapter for the Stage 11 application service."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.exceptions import (
    AlbumTooLarge,
    DeepLinkNotFound,
    IdempotencyKeyConflict,
    InvalidTrackUrl,
    MetadataUnavailable,
    ProviderAuthenticationError,
    ProviderUnavailable,
    UnsupportedMediaType,
    UnsupportedProvider,
)
from app.internal_api.auth import InternalApiAuth
from app.internal_api.errors import InternalApiError
from app.internal_api.schemas import (
    DeepLinkResponse,
    DeepLinkTargetResponse,
    HealthResponse,
    RegistrationRequest,
)
from app.services.deep_links import DeepLinkRegistryService, deep_link_url
from app.storage.models import DeepLinkRegistryEntry

logger = logging.getLogger(__name__)
MAX_REQUEST_BODY_BYTES = 4096


def create_internal_api_app(
    *,
    api_token: str,
    registry: DeepLinkRegistryService | None,
    bot_username: str | None,
) -> FastAPI:
    """Build the private ASGI app; passing no registry is reserved for ``--check``."""

    app = FastAPI(
        title="Musicbot Internal API",
        version="1",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    authenticate = InternalApiAuth(api_token)

    @app.middleware("http")
    async def bound_request_body(request: Request, call_next):  # type: ignore[no-untyped-def]
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return _error_response(413, "INVALID_REQUEST", "Request body is too large.")
            except ValueError:
                return _error_response(400, "INVALID_REQUEST", "Invalid request.")
        return await call_next(request)

    @app.exception_handler(InternalApiError)
    async def internal_api_error(_: Request, exc: InternalApiError) -> JSONResponse:
        return _error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return _error_response(400, "INVALID_REQUEST", "The request is invalid.")

    @app.exception_handler(Exception)
    async def unexpected_error(_: Request, __: Exception) -> JSONResponse:
        logger.error("Internal API request failed")
        return _error_response(500, "INTERNAL_ERROR", "An internal error occurred.")

    @app.get("/internal/v1/health", response_model=HealthResponse)
    async def health() -> HealthResponse:
        return HealthResponse(status="ok")

    @app.post(
        "/internal/v1/deep-links",
        response_model=DeepLinkResponse,
        dependencies=[Depends(authenticate)],
    )
    async def register(
        body: RegistrationRequest,
        idempotency_key: Annotated[str | None, Header(max_length=128)] = None,
    ) -> DeepLinkResponse:
        service, username = _runtime(registry, bot_username)
        try:
            result = await service.register_from_url(body.url, idempotency_key=idempotency_key)
        except IdempotencyKeyConflict:
            raise InternalApiError(
                409,
                "IDEMPOTENCY_KEY_CONFLICT",
                "The Idempotency-Key was already used for another request.",
            ) from None
        except UnsupportedMediaType:
            raise InternalApiError(
                422, "UNSUPPORTED_MEDIA_TYPE", "The media type is not supported."
            ) from None
        except (InvalidTrackUrl, UnsupportedProvider):
            raise InternalApiError(422, "INVALID_MEDIA_URL", "The media URL is invalid.") from None
        except (MetadataUnavailable, ProviderAuthenticationError, ProviderUnavailable):
            raise InternalApiError(
                503, "TRACK_RESOLUTION_FAILED", "The Track could not be resolved."
            ) from None
        except AlbumTooLarge:
            raise InternalApiError(
                422, "ALBUM_TARGET_INVALID", "The Album target is invalid."
            ) from None
        except ValueError:
            raise InternalApiError(400, "INVALID_REQUEST", "The request is invalid.") from None
        entry = result.entry
        logger.info(
            "Deep link registration completed",
            extra={
                "deep_link_registry_id": entry.id,
                "target_type": entry.target_type.value,
                "created": result.created,
            },
        )
        return DeepLinkResponse(
            target_type=entry.target_type.value.lower(),
            status=entry.status.value.lower(),
            start_parameter=entry.token,
            deep_link_url=deep_link_url(username, entry.token),
            created=result.created,
            created_at=entry.created_at,
            revoked_at=entry.revoked_at,
        )

    @app.get(
        "/internal/v1/deep-links/{token}",
        response_model=DeepLinkTargetResponse,
        dependencies=[Depends(authenticate)],
    )
    async def get_deep_link(token: str) -> DeepLinkTargetResponse:
        service, _ = _runtime(registry, bot_username)
        try:
            entry = await service.get_by_token(token)
        except DeepLinkNotFound:
            raise InternalApiError(
                404, "DEEP_LINK_NOT_FOUND", "The deep link was not found."
            ) from None
        return _target_response(entry)

    @app.post(
        "/internal/v1/deep-links/{token}/revoke",
        response_model=DeepLinkTargetResponse,
        dependencies=[Depends(authenticate)],
    )
    async def revoke(token: str) -> DeepLinkTargetResponse:
        service, _ = _runtime(registry, bot_username)
        try:
            entry = await service.revoke(token)
        except DeepLinkNotFound:
            raise InternalApiError(
                404, "DEEP_LINK_NOT_FOUND", "The deep link was not found."
            ) from None
        logger.info("Deep link revoked", extra={"deep_link_registry_id": entry.id})
        return _target_response(entry)

    return app


def _runtime(
    registry: DeepLinkRegistryService | None, bot_username: str | None
) -> tuple[DeepLinkRegistryService, str]:
    if registry is None or bot_username is None:
        raise InternalApiError(503, "INTERNAL_ERROR", "The API runtime is unavailable.")
    return registry, bot_username


def _target_response(entry: DeepLinkRegistryEntry) -> DeepLinkTargetResponse:
    return DeepLinkTargetResponse(
        target_type=entry.target_type.value.lower(),
        status=entry.status.value.lower(),
        start_parameter=entry.token,
        track_id=entry.track_id,
        album_provider=entry.album_provider.value if entry.album_provider is not None else None,
        album_provider_id=entry.album_provider_id,
        created_at=entry.created_at,
        revoked_at=entry.revoked_at,
    )


def _error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}},
        headers=headers,
    )
