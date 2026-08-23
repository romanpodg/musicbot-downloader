"""Sanitized provider-account domain contracts.

These models deliberately contain no upstream account objects or credential fields.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from app.core.enums import MusicProviderName


class ProviderAccountState(StrEnum):
    READY = "READY"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    AUTH_REQUIRED = "AUTH_REQUIRED"
    AUTHORIZING = "AUTHORIZING"
    ERROR = "ERROR"
    UNSUPPORTED = "UNSUPPORTED"


class ProviderAccountErrorCode(StrEnum):
    AUTH_NOT_CONFIGURED = "AUTH_NOT_CONFIGURED"
    SESSION_UNAVAILABLE = "SESSION_UNAVAILABLE"
    SESSION_UNVERIFIED = "SESSION_UNVERIFIED"
    SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
    RUNTIME_UNAVAILABLE = "RUNTIME_UNAVAILABLE"
    REFRESH_FAILED = "REFRESH_FAILED"
    STATUS_CHECK_FAILED = "STATUS_CHECK_FAILED"
    INVALID_BACKEND_RESPONSE = "INVALID_BACKEND_RESPONSE"
    PROVIDER_UNSUPPORTED = "PROVIDER_UNSUPPORTED"
    AUTHORIZATION_UNSUPPORTED = "AUTHORIZATION_UNSUPPORTED"
    AUTHORIZATION_FAILED = "AUTHORIZATION_FAILED"
    AUTHORIZATION_STALE_FLOW = "AUTHORIZATION_STALE_FLOW"
    TIDAL_AUTH_START_FAILED = "TIDAL_AUTH_START_FAILED"
    TIDAL_AUTH_PENDING = "TIDAL_AUTH_PENDING"
    TIDAL_AUTH_SLOW_DOWN = "TIDAL_AUTH_SLOW_DOWN"
    TIDAL_AUTH_DENIED = "TIDAL_AUTH_DENIED"
    TIDAL_AUTH_EXPIRED = "TIDAL_AUTH_EXPIRED"
    TIDAL_AUTH_NETWORK_ERROR = "TIDAL_AUTH_NETWORK_ERROR"
    TIDAL_AUTH_INVALID_RESPONSE = "TIDAL_AUTH_INVALID_RESPONSE"
    TIDAL_AUTH_PERSIST_FAILED = "TIDAL_AUTH_PERSIST_FAILED"
    TIDAL_AUTH_RELOAD_FAILED = "TIDAL_AUTH_RELOAD_FAILED"
    DEEZER_ARL_INVALID_FORMAT = "DEEZER_ARL_INVALID_FORMAT"
    DEEZER_ARL_INVALID = "DEEZER_ARL_INVALID"
    DEEZER_AUTH_NETWORK_ERROR = "DEEZER_AUTH_NETWORK_ERROR"
    DEEZER_AUTH_TIMEOUT = "DEEZER_AUTH_TIMEOUT"
    DEEZER_AUTH_INVALID_RESPONSE = "DEEZER_AUTH_INVALID_RESPONSE"
    DEEZER_AUTH_UPSTREAM_ERROR = "DEEZER_AUTH_UPSTREAM_ERROR"
    DEEZER_AUTH_PERSIST_FAILED = "DEEZER_AUTH_PERSIST_FAILED"
    DEEZER_AUTH_RELOAD_FAILED = "DEEZER_AUTH_RELOAD_FAILED"
    DEEZER_AUTH_MESSAGE_DELETE_FAILED = "DEEZER_AUTH_MESSAGE_DELETE_FAILED"
    DISCONNECT_UNSUPPORTED = "DISCONNECT_UNSUPPORTED"
    DISCONNECT_FAILED = "DISCONNECT_FAILED"


class ProviderAuthorizationMethod(StrEnum):
    BROWSER_DEVICE_LINK = "BROWSER_DEVICE_LINK"
    SENSITIVE_SECRET = "SENSITIVE_SECRET"
    COMPOUND_CREDENTIALS = "COMPOUND_CREDENTIALS"


class ProviderAuthorizationOutcomeStatus(StrEnum):
    UNSUPPORTED = "UNSUPPORTED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    CANCELLED = "CANCELLED"
    READY = "READY"
    FAILED = "FAILED"


class ProviderAuthorizationStartStatus(StrEnum):
    STARTED = "STARTED"
    UNSUPPORTED = "UNSUPPORTED"
    ALREADY_ACTIVE = "ALREADY_ACTIVE"
    ALREADY_READY = "ALREADY_READY"
    FAILED = "FAILED"


class ProviderDisconnectOutcomeStatus(StrEnum):
    UNSUPPORTED = "UNSUPPORTED"
    DISCONNECTED = "DISCONNECTED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ProviderAccountStatus:
    """Provider-neutral state safe for presentation, logs, and repr output."""

    provider: MusicProviderName
    state: ProviderAccountState
    checked_at: datetime
    authorization_methods: tuple[ProviderAuthorizationMethod, ...] = ()
    error_code: ProviderAccountErrorCode | None = None
    disconnect_supported: bool = False

    @property
    def authorization_supported(self) -> bool:
        return bool(self.authorization_methods)


@dataclass(frozen=True, slots=True)
class ProviderAccountOverview:
    checked_at: datetime
    accounts: tuple[ProviderAccountStatus, ...]


@dataclass(frozen=True, slots=True)
class ProviderAuthorizationRequest:
    provider: MusicProviderName
    method: ProviderAuthorizationMethod


@dataclass(frozen=True, slots=True)
class ProviderAuthorizationOutcome:
    provider: MusicProviderName
    status: ProviderAuthorizationOutcomeStatus
    error_code: ProviderAccountErrorCode | None = None


@dataclass(frozen=True, slots=True)
class ProviderAuthorizationChallenge:
    """Sanitized browser challenge; never contains the device code or credentials."""

    provider: MusicProviderName
    flow_id: str
    verification_url: str = field(repr=False)
    expires_at: datetime
    polling_interval_seconds: float


@dataclass(frozen=True, slots=True)
class ProviderSensitiveInputChallenge:
    """Opaque generation for an in-process, non-durable secret-entry flow."""

    provider: MusicProviderName
    flow_id: str


@dataclass(frozen=True, slots=True)
class ProviderAuthorizationStartOutcome:
    provider: MusicProviderName
    status: ProviderAuthorizationStartStatus
    challenge: ProviderAuthorizationChallenge | ProviderSensitiveInputChallenge | None = None
    error_code: ProviderAccountErrorCode | None = None


@dataclass(frozen=True, slots=True)
class ProviderDisconnectOutcome:
    provider: MusicProviderName
    status: ProviderDisconnectOutcomeStatus
    error_code: ProviderAccountErrorCode | None = None


class SensitiveValue:
    """Opaque credential input whose repr and str are always redacted."""

    __slots__ = ("__value",)

    def __init__(self, value: str) -> None:
        if not value:
            raise ValueError("sensitive value must not be empty")
        self.__value = value

    def reveal_to_provider_backend(self) -> str:
        """Reveal only at the provider backend boundary."""

        return self.__value

    def __repr__(self) -> str:
        return "SensitiveValue('[REDACTED]')"

    def __str__(self) -> str:
        return "[REDACTED]"


@dataclass(frozen=True, slots=True)
class ProviderSecretInput:
    provider: MusicProviderName
    secret: SensitiveValue


@dataclass(frozen=True, slots=True)
class ProviderCompoundCredentialInput:
    """Separate opaque playback and API credentials for a future driver."""

    provider: MusicProviderName
    playback_credential: SensitiveValue
    api_credential: SensitiveValue
