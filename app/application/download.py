"""Stage 18 confirmation and download-admission application flow."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.download import (
    DownloadDeliveryTarget,
    DownloadOptions,
    DownloadRequest,
    DownloadSubmission,
)
from app.core.recognition import RecognitionDecision, RecognitionResult
from app.core.search import Track


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
    user_id: int
    selected_track: Track
    alternatives: tuple[Track, ...]
    options: DownloadOptions


class DownloadService:
    """Transport-neutral orchestration around confirmation and durable download admission."""

    def __init__(
        self,
        use_case: DownloadTrackUseCase,
        *,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._use_case = use_case
        self._token_factory = token_factory or (lambda: secrets.token_hex(12))
        self._confirmations: dict[str, DownloadConfirmation] = {}

    def create_confirmation(
        self,
        *,
        user_id: int,
        result: RecognitionResult,
        options: DownloadOptions | None = None,
    ) -> DownloadConfirmation | None:
        """Store only a pending user choice; rejected recognition never becomes a request."""

        if user_id <= 0:
            raise ValueError("download user ID must be positive")
        if result.candidate is None or result.decision is RecognitionDecision.REJECT:
            return None
        token = self._new_token()
        confirmation = DownloadConfirmation(
            token=token,
            user_id=user_id,
            selected_track=result.candidate.track,
            alternatives=tuple(item.candidate.track for item in result.alternatives),
            options=options or DownloadOptions(),
        )
        self._confirmations[token] = confirmation
        return confirmation

    def select_alternative(
        self, *, user_id: int, token: str, alternative_index: int
    ) -> DownloadConfirmation | None:
        confirmation = self._owned_confirmation(user_id, token)
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
            confirmation.user_id,
            selected,
            alternatives,
            confirmation.options,
        )
        self._confirmations[token] = updated
        return updated

    async def confirm(
        self, *, user_id: int, token: str, target: DownloadDeliveryTarget
    ) -> DownloadSubmission | None:
        confirmation = self._owned_confirmation(user_id, token)
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

    def cancel(self, *, user_id: int, token: str) -> bool:
        confirmation = self._owned_confirmation(user_id, token)
        if confirmation is None:
            return False
        self._confirmations.pop(token, None)
        return True

    def _owned_confirmation(self, user_id: int, token: str) -> DownloadConfirmation | None:
        confirmation = self._confirmations.get(token)
        if confirmation is None or confirmation.user_id != user_id:
            return None
        return confirmation

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
