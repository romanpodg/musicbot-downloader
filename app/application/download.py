"""Stage 18 confirmation and download-admission application flow."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from app.core.download import (
    DownloadDeliveryTarget,
    DownloadOptions,
    DownloadRequest,
    DownloadSubmission,
)
from app.core.recognition import RecognitionDecision, RecognitionResult
from app.core.search import Track
from app.core.telegram_context import TelegramContext

DEFAULT_CONFIRMATION_TTL = timedelta(minutes=15)
MAX_CONFIRMATION_ALTERNATIVES = 3


class RecognizedTrackResolver(Protocol):
    """Resolve a selected catalog identity through the existing canonical-track boundary."""

    async def resolve_track_id(self, track: Track) -> int: ...


class DownloadSubmissionPort(Protocol):
    """Admit a canonical track through the existing durable delivery/queue path."""

    async def submit(
        self,
        request: DownloadRequest,
        *,
        canonical_track_id: int,
        target: DownloadDeliveryTarget,
    ) -> DownloadSubmission: ...


class DownloadTrackUseCase:
    """Turns confirmed catalog intent into an existing durable delivery/queue admission."""

    def __init__(
        self, resolver: RecognizedTrackResolver, submissions: DownloadSubmissionPort
    ) -> None:
        self._resolver = resolver
        self._submissions = submissions

    async def execute(
        self, request: DownloadRequest, *, target: DownloadDeliveryTarget
    ) -> DownloadSubmission:
        if request.user_id != target.user_id:
            raise ValueError("download request and delivery target users differ")
        canonical_track_id = await self._resolver.resolve_track_id(request.recognized_track)
        if canonical_track_id <= 0:
            raise ValueError("recognized track resolver returned an invalid track ID")
        return await self._submissions.submit(
            request, canonical_track_id=canonical_track_id, target=target
        )


@dataclass(frozen=True, slots=True)
class DownloadConfirmation:
    """Short-lived, user-owned confirmation context; it is never a persistent job state."""

    token: str
    context: TelegramContext
    selected_track: Track
    alternatives: tuple[Track, ...]
    options: DownloadOptions
    expires_at: datetime
    show_alternatives: bool = True

    @property
    def user_id(self) -> int:
        return self.context.user_id

    @property
    def presentation_alternatives(self) -> tuple[Track, ...]:
        return self.alternatives if self.show_alternatives else ()


class DownloadService:
    """Transport-neutral orchestration around confirmation and durable download admission."""

    def __init__(
        self,
        use_case: DownloadTrackUseCase,
        *,
        token_factory: Callable[[], str] | None = None,
        clock: Callable[[], datetime] | None = None,
        confirmation_ttl: timedelta = DEFAULT_CONFIRMATION_TTL,
    ) -> None:
        if confirmation_ttl <= timedelta():
            raise ValueError("download confirmation TTL must be positive")
        self._use_case = use_case
        self._token_factory = token_factory or (lambda: secrets.token_hex(12))
        self._clock = clock or (lambda: datetime.now(UTC))
        self._confirmation_ttl = confirmation_ttl
        self._confirmations: dict[str, DownloadConfirmation] = {}

    def create_confirmation(
        self,
        *,
        context: TelegramContext,
        result: RecognitionResult,
        options: DownloadOptions | None = None,
    ) -> DownloadConfirmation | None:
        """Store only a pending user choice; rejected recognition never becomes a request."""

        if result.candidate is None or result.decision is RecognitionDecision.REJECT:
            return None
        self._prune_expired()
        token = self._new_token()
        alternatives = tuple(
            item.candidate.track for item in result.alternatives[:MAX_CONFIRMATION_ALTERNATIVES]
        )
        confirmation = DownloadConfirmation(
            token=token,
            context=context,
            selected_track=result.candidate.track,
            alternatives=alternatives,
            options=options or DownloadOptions(),
            expires_at=self._clock() + self._confirmation_ttl,
            show_alternatives=result.decision is RecognitionDecision.ASK_USER,
        )
        self._confirmations[token] = confirmation
        return confirmation

    def select_alternative(
        self, *, context: TelegramContext, token: str, alternative_index: int
    ) -> DownloadConfirmation | None:
        confirmation = self._owned_confirmation(context, token)
        if confirmation is None or not 0 <= alternative_index < len(confirmation.alternatives):
            return None
        selected = confirmation.alternatives[alternative_index]
        alternatives = (
            confirmation.selected_track,
            *(
                track
                for index, track in enumerate(confirmation.alternatives)
                if index != alternative_index
            ),
        )
        updated = DownloadConfirmation(
            confirmation.token,
            confirmation.context,
            selected,
            alternatives,
            confirmation.options,
            confirmation.expires_at,
            confirmation.show_alternatives,
        )
        self._confirmations[token] = updated
        return updated

    async def confirm(
        self, *, context: TelegramContext, token: str, target: DownloadDeliveryTarget
    ) -> DownloadSubmission | None:
        confirmation = self._owned_confirmation(context, token)
        if confirmation is None:
            return None
        request = DownloadRequest(
            user_id=confirmation.user_id,
            recognized_track=confirmation.selected_track,
            options=confirmation.options,
        )
        submission = await self._use_case.execute(request, target=target)
        self._confirmations.pop(token, None)
        return submission

    def cancel(self, *, context: TelegramContext, token: str) -> bool:
        confirmation = self._owned_confirmation(context, token)
        if confirmation is None:
            return False
        self._confirmations.pop(token, None)
        return True

    def _owned_confirmation(
        self, context: TelegramContext, token: str
    ) -> DownloadConfirmation | None:
        now = self._clock()
        confirmation = self._confirmations.get(token)
        if (
            confirmation is None
            or confirmation.context != context
            or confirmation.expires_at <= now
        ):
            if confirmation is not None and confirmation.expires_at <= now:
                self._confirmations.pop(token, None)
            return None
        return confirmation

    def _prune_expired(self) -> None:
        now = self._clock()
        expired = [token for token, item in self._confirmations.items() if item.expires_at <= now]
        for token in expired:
            self._confirmations.pop(token, None)

    def _new_token(self) -> str:
        for _ in range(8):
            token = self._token_factory()
            if (
                len(token) == 24
                and token.isascii()
                and token.islower()
                and token.isalnum()
                and token not in self._confirmations
            ):
                return token
        raise RuntimeError("could not create an opaque download confirmation token")
