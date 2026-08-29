"""Pure Stage 21 state-machine validation."""

from app.core.enums import DownloadJobStatus, DownloadPhase


class InvalidDownloadTransition(ValueError):
    pass


class DownloadLifecycle:
    _allowed: dict[DownloadJobStatus, frozenset[DownloadJobStatus]] = {
        DownloadJobStatus.PENDING: frozenset(
            {DownloadJobStatus.QUEUED, DownloadJobStatus.CANCELLED}
        ),
        DownloadJobStatus.QUEUED: frozenset(
            {DownloadJobStatus.RUNNING, DownloadJobStatus.CANCELLED}
        ),
        DownloadJobStatus.RUNNING: frozenset(
            {
                DownloadJobStatus.DELIVERING,
                DownloadJobStatus.RETRY_WAIT,
                DownloadJobStatus.FAILED,
                DownloadJobStatus.CANCELLED,
            }
        ),
        DownloadJobStatus.RETRY_WAIT: frozenset(
            {DownloadJobStatus.QUEUED, DownloadJobStatus.FAILED, DownloadJobStatus.CANCELLED}
        ),
        DownloadJobStatus.DELIVERING: frozenset(
            {
                DownloadJobStatus.SUCCEEDED,
                DownloadJobStatus.RETRY_WAIT,
                DownloadJobStatus.FAILED,
                DownloadJobStatus.CANCELLED,
            }
        ),
        DownloadJobStatus.SUCCEEDED: frozenset(),
        DownloadJobStatus.FAILED: frozenset(),
        DownloadJobStatus.CANCELLED: frozenset(),
    }

    @classmethod
    def validate(cls, current: DownloadJobStatus, target: DownloadJobStatus) -> None:
        if target not in cls._allowed[current]:
            raise InvalidDownloadTransition(f"{current.value} -> {target.value} is not allowed")

    @classmethod
    def validate_phase(cls, status: DownloadJobStatus, phase: DownloadPhase) -> None:
        if status is not DownloadJobStatus.RUNNING:
            raise InvalidDownloadTransition("phases are only valid while RUNNING")
