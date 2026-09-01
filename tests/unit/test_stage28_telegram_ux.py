from datetime import UTC, datetime

from app.core.delivery_targets import PrivateUserTarget
from app.core.download import DownloadDeliveryTarget, DownloadRequest
from app.core.enums import (
    DownloadFailureCode,
    DownloadJobStatus,
    DownloadPhase,
    MusicProviderName,
)
from app.core.search import Artist, Track
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.services.download_activity import DownloadActivityService
from app.services.download_lifecycle import DownloadLifecycleService
from app.telegram.ux_presentation import (
    DownloadStatusPresenter,
    TelegramStatusUpdatePolicy,
    UserDownloadState,
)


def test_stage28_statuses_are_small_safe_and_retry_aware() -> None:
    presenter = DownloadStatusPresenter()
    assert (
        presenter.present(DownloadJobStatus.RUNNING, DownloadPhase.DOWNLOADING).state
        is UserDownloadState.DOWNLOADING
    )
    assert (
        presenter.present(DownloadJobStatus.RUNNING, DownloadPhase.PROCESSING).state
        is UserDownloadState.PROCESSING
    )
    failure = presenter.present(DownloadJobStatus.FAILED, failure_code=DownloadFailureCode.NETWORK)
    assert failure.state is UserDownloadState.FAILED
    assert failure.retryable is True
    assert "NETWORK" not in failure.label


def test_stage28_status_policy_coalesces_but_never_hides_terminal() -> None:
    now = datetime(2026, 9, 2, tzinfo=UTC)
    policy = TelegramStatusUpdatePolicy(clock=lambda: now)
    presenter = DownloadStatusPresenter()
    preparing = presenter.present(DownloadJobStatus.PENDING)
    downloading = presenter.present(DownloadJobStatus.RUNNING, DownloadPhase.DOWNLOADING)
    delivered = presenter.present(DownloadJobStatus.SUCCEEDED)
    assert policy.should_emit(5, 8, preparing)
    assert not policy.should_emit(5, 8, downloading)
    assert policy.should_emit(5, 8, delivered)


async def test_stage28_downloads_projection_is_bounded_and_owner_scoped(database) -> None:  # type: ignore[no-untyped-def]
    async with database.transaction() as repositories:
        owner = await repositories.users.create_user(28001)
        await repositories.users.create_user(28002)
        track = await repositories.tracks.create_track(title="Private activity", artist="Artist")
    lifecycle = DownloadLifecycleService(database)
    await lifecycle.admit(
        confirmation_id="stage28-owner-activity",
        request=DownloadRequest(
            28001,
            Track(
                "stage28",
                "Private activity",
                (Artist("Artist"),),
                MusicProviderName.SPOTIFY,
                "stage28",
            ),
            confirmation_id="stage28-owner-activity",
        ),
        canonical_track_id=track.id,
        target=DownloadDeliveryTarget(
            28001,
            TelegramContext(28001, 28001, TelegramChatType.PRIVATE),
            PrivateUserTarget(28001),
            1,
        ),
    )
    activity = DownloadActivityService(database)
    mine = await activity.list_for_telegram_user(owner.telegram_id)
    assert [item.title for item in mine] == ["Private activity"]
    assert await activity.detail_for_telegram_user(28002, mine[0].request_id) is None
