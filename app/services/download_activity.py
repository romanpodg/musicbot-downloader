"""Owner-scoped, read-only Stage 28 activity projection over the lifecycle."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import DownloadFailureCode
from app.storage import Database
from app.storage.models.download_lifecycle import DownloadLifecycleJob, DownloadRequestRecord
from app.telegram.ux_presentation import DownloadStatusPresenter, DownloadStatusView


@dataclass(frozen=True, slots=True)
class DownloadActivity:
    request_id: int
    job_id: int
    title: str
    artist: str | None
    view: DownloadStatusView


class DownloadActivityService:
    """Never exposes provider attempts, filesystem details, or another user's work."""

    def __init__(
        self, database: Database, *, presenter: DownloadStatusPresenter | None = None
    ) -> None:
        self._database = database
        self._presenter = presenter or DownloadStatusPresenter()

    async def list_for_telegram_user(
        self, telegram_user_id: int, *, limit: int = 12
    ) -> list[DownloadActivity]:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            if user is None:
                return []
            rows = await repositories.download_lifecycle.list_activity(user.id, limit=limit)
        return [self._activity(request, job) for request, job, _ in rows]

    async def detail_for_telegram_user(
        self, telegram_user_id: int, request_id: int
    ) -> DownloadActivity | None:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_user_id)
            request = await repositories.download_lifecycle.get_request(request_id)
            if user is None or request is None or request.requester_user_id != user.id:
                return None
            job = await repositories.download_lifecycle.get_job_for_request(request.id)
            if job is None:
                return None
        return self._activity(request, job)

    def _activity(self, request: object, job: object) -> DownloadActivity:
        if not isinstance(request, DownloadRequestRecord) or not isinstance(
            job, DownloadLifecycleJob
        ):
            raise TypeError("invalid lifecycle activity")
        failure = _failure_code(job.error_code)
        return DownloadActivity(
            request_id=request.id,
            job_id=job.id,
            title=request.media_title or "Track",
            artist=request.media_artist,
            view=self._presenter.present(job.status, job.phase, failure),
        )


def _failure_code(value: str | None) -> DownloadFailureCode | None:
    if value is None:
        return None
    try:
        return DownloadFailureCode(value)
    except ValueError:
        return None
