from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from app.application.download import DownloadService, DownloadTrackUseCase
from app.application.ux.services.errors import UxErrorMessage, UxErrorService
from app.application.ux.services.progress import DownloadProgressService
from app.application.ux.services.state import UserUxStateService, UxState
from app.core.delivery_targets import PrivateUserTarget
from app.core.download import (
    CancelDownloadRequest,
    DownloadDeliveryTarget,
    DownloadOptions,
    DownloadRequest,
    DownloadSubmission,
    DownloadSubmissionState,
)
from app.core.enums import MusicProviderName, QualityProfile, QueueJobStatus, TelegramDeliveryStatus
from app.core.exceptions import QueueFullError, UploadTerminalError
from app.core.models import UploadRequest
from app.core.recognition import (
    RankedTrackCandidate,
    RecognitionDecision,
    RecognitionResult,
    SimilarityScores,
    TrackCandidate,
)
from app.core.search import Artist, Track
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.i18n import LocalizationService
from app.services.metadata import MetadataProcessor
from app.services.telegram_upload import DeliveryService
from app.telegram.download_callbacks import (
    DownloadCallbackAction,
    encode_download_callback,
    parse_download_callback,
)
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessage, UxMessageService


def _track(*, identifier: str = "one-more-time", title: str = "One More Time") -> Track:
    return Track(
        id=f"search:spotify:{identifier}",
        title=title,
        artists=(Artist("Daft Punk"),),
        provider=MusicProviderName.SPOTIFY,
        provider_track_id=identifier,
    )


def _recognition(*, alternatives: tuple[Track, ...] = ()) -> RecognitionResult:
    candidate = TrackCandidate(_track(), "spotify")
    return RecognitionResult(
        candidate,
        0.91,
        RecognitionDecision.ACCEPT,
        tuple(
            # Stage 18 receives Stage 17-ranked alternatives; score details stay Stage 17-owned.
            RankedTrackCandidate(
                TrackCandidate(track, "spotify"),
                SimilarityScores(0.8, 0.8, 0.5, 0.5),
                0.8,
            )
            for track in alternatives
        ),
    )


def test_download_request_and_cancellation_are_pure_validated_intents() -> None:
    request = DownloadRequest(42, _track(), DownloadOptions())

    assert request.user_id == 42
    assert request.recognized_track.title == "One More Time"
    assert CancelDownloadRequest(42, download_job_id=7).download_job_id == 7
    with pytest.raises(ValueError, match="positive"):
        DownloadRequest(0, _track())
    with pytest.raises(ValueError, match="requires"):
        CancelDownloadRequest(42)


@dataclass
class _Resolver:
    track_id: int = 17
    tracks: list[Track] | None = None

    async def resolve_track_id(self, track: Track) -> int:
        if self.tracks is None:
            self.tracks = []
        self.tracks.append(track)
        return self.track_id


@dataclass
class _SubmissionPort:
    received: tuple[DownloadRequest, int, DownloadDeliveryTarget] | None = None

    async def submit(
        self,
        request: DownloadRequest,
        *,
        canonical_track_id: int,
        target: DownloadDeliveryTarget,
    ) -> DownloadSubmission:
        self.received = (request, canonical_track_id, target)
        return DownloadSubmission(
            request,
            canonical_track_id,
            delivery_request_id=8,
            state=DownloadSubmissionState.QUEUED,
            download_job_id=9,
        )


async def test_download_track_use_case_resolves_then_submits_through_existing_port() -> None:
    resolver = _Resolver()
    submissions = _SubmissionPort()
    use_case = DownloadTrackUseCase(resolver, submissions)
    request = DownloadRequest(42, _track())
    context = TelegramContext(42, 42, TelegramChatType.PRIVATE)
    target = DownloadDeliveryTarget(42, context, PrivateUserTarget(42), 111)

    result = await use_case.execute(request, target=target)

    assert result.state is DownloadSubmissionState.QUEUED
    assert result.download_job_id == 9
    assert resolver.tracks == [_track()]
    assert submissions.received == (request, 17, target)


async def test_download_service_requires_owned_confirmation_and_allows_alternative_selection() -> (
    None
):
    resolver = _Resolver()
    submissions = _SubmissionPort()
    service = DownloadService(
        DownloadTrackUseCase(resolver, submissions), token_factory=lambda: "a" * 24
    )
    confirmation = service.create_confirmation(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
        result=_recognition(alternatives=(_track(identifier="around", title="Around the World"),)),
    )
    assert confirmation is not None
    assert (
        service.select_alternative(
            context=TelegramContext(99, 99, TelegramChatType.PRIVATE),
            token=confirmation.token,
            alternative_index=0,
        )
        is None
    )

    selected = service.select_alternative(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
        token=confirmation.token,
        alternative_index=0,
    )
    assert selected is not None
    assert selected.selected_track.title == "Around the World"

    result = await service.confirm(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
        token=confirmation.token,
        target=DownloadDeliveryTarget(
            42,
            TelegramContext(42, 42, TelegramChatType.PRIVATE),
            PrivateUserTarget(42),
            111,
        ),
    )
    assert result is not None
    assert submissions.received is not None
    assert submissions.received[0].recognized_track.title == "Around the World"
    assert (
        await service.confirm(
            context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
            token=confirmation.token,
            target=DownloadDeliveryTarget(
                42,
                TelegramContext(42, 42, TelegramChatType.PRIVATE),
                PrivateUserTarget(42),
                111,
            ),
        )
        is None
    )


def test_stage18_progress_and_errors_are_translated_from_backend_state() -> None:
    progress = DownloadProgressService()

    assert (
        progress.state_for(delivery_status=TelegramDeliveryStatus.QUEUED) is UxState.DOWNLOAD_QUEUED
    )
    assert (
        progress.state_for(
            delivery_status=TelegramDeliveryStatus.WAITING,
            download_status=QueueJobStatus.RUNNING,
        )
        is UxState.DOWNLOAD_PROCESSING
    )
    assert (
        progress.state_for(delivery_status=TelegramDeliveryStatus.DELIVERED)
        is UxState.DOWNLOAD_COMPLETED
    )
    assert UxErrorService().message_name(QueueFullError()) is UxErrorMessage.DOWNLOAD_FAILED
    assert (
        UxErrorService().download_message_name(QueueFullError()) is UxErrorMessage.DOWNLOAD_FAILED
    )


def test_stage18_confirmation_callbacks_and_states_remain_opaque_and_validated() -> None:
    token = "c" * 24
    callback = encode_download_callback(DownloadCallbackAction.CONFIRM, token)
    assert callback == f"dl18:confirm:{token}"
    assert parse_download_callback(callback) is not None
    assert parse_download_callback("dl18:confirm:provider-secret") is None

    service = DownloadService(
        DownloadTrackUseCase(_Resolver(), _SubmissionPort()), token_factory=lambda: token
    )
    confirmation = service.create_confirmation(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE), result=_recognition()
    )
    assert confirmation is not None
    keyboard = UxKeyboardFactory(LocalizationService(("en", "ru"), "en")).download_confirmation(
        "en", confirmation
    )
    assert [button.callback_data for row in keyboard.inline_keyboard for button in row] == [
        f"dl18:confirm:{token}",
        f"dl18:cancel:{token}",
    ]

    states = UserUxStateService()
    states.transition(42, UxState.SEARCH_INPUT)
    states.transition(42, UxState.SEARCHING)
    assert states.transition(42, UxState.DOWNLOAD_CONFIRMATION) is UxState.DOWNLOAD_CONFIRMATION
    assert states.transition(42, UxState.DOWNLOAD_QUEUED) is UxState.DOWNLOAD_QUEUED
    assert states.transition(42, UxState.DOWNLOAD_PROCESSING) is UxState.DOWNLOAD_PROCESSING
    assert states.transition(42, UxState.DOWNLOAD_COMPLETED) is UxState.DOWNLOAD_COMPLETED
    messages = UxMessageService(LocalizationService(("en", "ru"), "en"))
    assert messages.get(UxMessage.DOWNLOAD_FAILED, "en") == "❌ Download failed. Please try again."


class _PassthroughMetadataProcessor:
    async def process(self, artifact: UploadRequest) -> UploadRequest:
        return artifact


def test_metadata_processor_is_an_unwired_extension_point() -> None:
    processor: MetadataProcessor = _PassthroughMetadataProcessor()
    assert processor is not None


def test_stage20_confirmation_presentation_hides_accept_alternatives_and_bounds_ask_user() -> None:
    service = DownloadService(
        DownloadTrackUseCase(_Resolver(), _SubmissionPort()), token_factory=lambda: "e" * 24
    )
    selected = _track()
    alternatives = tuple(
        RankedTrackCandidate(
            TrackCandidate(
                _track(identifier=f"alt-{index}", title=f"Alternative {index}"), "spotify"
            ),
            SimilarityScores(0.7, 0.7, 0.5, 0.5),
            0.7,
        )
        for index in range(5)
    )
    accepted = service.create_confirmation(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
        result=RecognitionResult(
            TrackCandidate(selected, "spotify"), 0.95, RecognitionDecision.ACCEPT, alternatives
        ),
    )
    assert accepted is not None
    assert accepted.presentation_alternatives == ()

    asking_service = DownloadService(
        DownloadTrackUseCase(_Resolver(), _SubmissionPort()), token_factory=lambda: "f" * 24
    )
    asking = asking_service.create_confirmation(
        context=TelegramContext(42, 42, TelegramChatType.PRIVATE),
        result=RecognitionResult(
            TrackCandidate(selected, "spotify"), 0.7, RecognitionDecision.ASK_USER, alternatives
        ),
    )
    assert asking is not None
    assert len(asking.presentation_alternatives) == 3


async def test_delivery_service_rejects_artifacts_without_validated_metadata() -> None:
    service = DeliveryService(None, None, None, cache_chat_id=-100)  # type: ignore[arg-type]
    request = UploadRequest(1, 1, 1, QualityProfile.MP3_320, "artifact", Path("missing.mp3"), None)

    with pytest.raises(UploadTerminalError):
        await service.deliver(request)
