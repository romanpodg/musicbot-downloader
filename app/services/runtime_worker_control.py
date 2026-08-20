"""Authorized runtime controls over the existing Stage 7 worker settings."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from app.core.models import QueueRuntimeSnapshot, WorkerPoolSnapshot
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
    TelegramAuthorizationService,
)
from app.services.queues import WorkerSettingMutation, WorkerSettingsService

logger = logging.getLogger(__name__)


class WorkerPoolType(StrEnum):
    DOWNLOAD = "download"
    UPLOAD = "upload"


class WorkerMutationStatus(StrEnum):
    UPDATED = "UPDATED"
    UNCHANGED = "UNCHANGED"
    MINIMUM_REACHED = "MINIMUM_REACHED"
    MAXIMUM_REACHED = "MAXIMUM_REACHED"


@dataclass(frozen=True, slots=True)
class WorkerControlSnapshot:
    download: WorkerPoolSnapshot
    upload: WorkerPoolSnapshot


@dataclass(frozen=True, slots=True)
class WorkerMutationResult:
    pool: WorkerPoolType
    status: WorkerMutationStatus
    previous_desired: int
    desired: int
    snapshot: WorkerControlSnapshot


class QueueRuntimeSnapshotReader(Protocol):
    async def snapshot(self) -> QueueRuntimeSnapshot: ...


class RuntimeWorkerControlService:
    """Change durable desired state; Stage 7 remains the only pool reconciler."""

    def __init__(
        self,
        authorization: TelegramAuthorizationService,
        worker_settings: WorkerSettingsService,
        runtime: QueueRuntimeSnapshotReader,
    ) -> None:
        self._authorization = authorization
        self._worker_settings = worker_settings
        self._runtime = runtime

    async def authorize(self, actor_user_id: int) -> AdminAccessContext:
        return await self._authorization.require_permission(
            actor_user_id, AdminPermission.WORKERS_MANAGE
        )

    async def get_snapshot(self, actor_user_id: int) -> WorkerControlSnapshot:
        await self.authorize(actor_user_id)
        return await self._snapshot()

    async def set_download_workers(self, actor_user_id: int, value: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        mutation = await self._worker_settings.update_download_workers(
            value, actor_user_id=actor_user_id
        )
        return await self._result(actor_user_id, WorkerPoolType.DOWNLOAD, mutation, action="set")

    async def set_upload_workers(self, actor_user_id: int, value: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        mutation = await self._worker_settings.update_upload_workers(
            value, actor_user_id=actor_user_id
        )
        return await self._result(actor_user_id, WorkerPoolType.UPLOAD, mutation, action="set")

    async def adjust_download_workers(self, actor_user_id: int, delta: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        mutation = await self._worker_settings.adjust_download_workers(
            delta, actor_user_id=actor_user_id
        )
        return await self._adjustment_result(
            actor_user_id, WorkerPoolType.DOWNLOAD, delta, mutation
        )

    async def adjust_upload_workers(self, actor_user_id: int, delta: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        mutation = await self._worker_settings.adjust_upload_workers(
            delta, actor_user_id=actor_user_id
        )
        return await self._adjustment_result(actor_user_id, WorkerPoolType.UPLOAD, delta, mutation)

    async def reset_download_workers(self, actor_user_id: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        current = await self._worker_settings.get_values()
        mutation = await self._worker_settings.update_download_workers(
            current.download.default, actor_user_id=actor_user_id
        )
        return await self._result(actor_user_id, WorkerPoolType.DOWNLOAD, mutation, action="reset")

    async def reset_upload_workers(self, actor_user_id: int) -> WorkerMutationResult:
        await self.authorize(actor_user_id)
        current = await self._worker_settings.get_values()
        mutation = await self._worker_settings.update_upload_workers(
            current.upload.default, actor_user_id=actor_user_id
        )
        return await self._result(actor_user_id, WorkerPoolType.UPLOAD, mutation, action="reset")

    async def _adjustment_result(
        self,
        actor_user_id: int,
        pool: WorkerPoolType,
        delta: int,
        mutation: WorkerSettingMutation,
    ) -> WorkerMutationResult:
        if mutation.current != mutation.previous:
            status = WorkerMutationStatus.UPDATED
        elif delta < 0:
            status = WorkerMutationStatus.MINIMUM_REACHED
        else:
            status = WorkerMutationStatus.MAXIMUM_REACHED
        action = "increase" if delta > 0 else "decrease"
        return await self._result(actor_user_id, pool, mutation, action=action, status=status)

    async def _result(
        self,
        actor_user_id: int,
        pool: WorkerPoolType,
        mutation: WorkerSettingMutation,
        *,
        action: str,
        status: WorkerMutationStatus | None = None,
    ) -> WorkerMutationResult:
        selected_status = status or (
            WorkerMutationStatus.UPDATED
            if mutation.current != mutation.previous
            else WorkerMutationStatus.UNCHANGED
        )
        if selected_status is WorkerMutationStatus.UPDATED:
            logger.info(
                "Runtime worker desired count updated",
                extra={
                    "event": "worker_desired_updated",
                    "user_id": actor_user_id,
                    "pool": pool.value,
                    "old_desired": mutation.previous,
                    "new_desired": mutation.current,
                    "action": action,
                },
            )
        return WorkerMutationResult(
            pool=pool,
            status=selected_status,
            previous_desired=mutation.previous,
            desired=mutation.current,
            snapshot=await self._snapshot(),
        )

    async def _snapshot(self) -> WorkerControlSnapshot:
        runtime = await self._runtime.snapshot()
        return WorkerControlSnapshot(runtime.download, runtime.upload)
