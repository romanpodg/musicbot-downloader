"""Validated, bounded builders for high-value operational audit history."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from app.core.enums import (
    DeepLinkTargetType,
    OperationalAuditActorKind,
    OperationalAuditEventType,
    OperationalAuditTargetKind,
    UserRole,
)
from app.storage import Database
from app.storage.models.base import utc_now
from app.storage.repositories.operational_audit import (
    MAX_AUDIT_LIST_LIMIT,
    OperationalAuditRepository,
)

MAX_AUDIT_DETAILS_BYTES = 4096
DEFAULT_AUDIT_LIST_LIMIT = 50
_SECRET_LIKE_METADATA = re.compile(
    r"(?i)(bot_token|internal_api_token|authorization|bearer\s+|access_token|"
    r"refresh_token|cookie|\barl\b|\b\d{5,}:[A-Za-z0-9_-]{8,})"
)


@dataclass(frozen=True, slots=True)
class RecoveryAuditDetails:
    download_jobs_recovered: int = 0
    upload_jobs_recovered: int = 0
    upload_artifacts_failed: int = 0
    uploads_recovered_from_cache: int = 0
    flights_reconciled: int = 0
    deliveries_recovered: int = 0
    album_items_recovered: int = 0
    album_requests_reconciled: int = 0


@dataclass(frozen=True, slots=True)
class ArtifactCleanupAuditDetails:
    scanned: int = 0
    preserved_active: int = 0
    preserved_owned: int = 0
    preserved_young: int = 0
    removed_stale: int = 0
    stale_candidates: int = 0
    unknown: int = 0
    errors: int = 0


@dataclass(frozen=True, slots=True)
class AuditEventView:
    id: int
    occurred_at: datetime
    event_type: OperationalAuditEventType
    actor_kind: OperationalAuditActorKind
    actor_user_id: int | None
    target_kind: OperationalAuditTargetKind | None
    target_id: str | None
    request_id: str | None
    details: dict[str, str | int | bool]


class OperationalAuditService:
    """The only application service that constructs persisted audit metadata."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def list_recent(
        self,
        *,
        limit: int = DEFAULT_AUDIT_LIST_LIMIT,
        before_id: int | None = None,
        event_type: OperationalAuditEventType | None = None,
        actor_user_id: int | None = None,
    ) -> tuple[AuditEventView, ...]:
        if limit < 1 or limit > MAX_AUDIT_LIST_LIMIT:
            raise ValueError(f"limit must be between 1 and {MAX_AUDIT_LIST_LIMIT}")
        async with self._database.transaction() as repositories:
            rows = await repositories.audit.list_recent(
                limit=limit,
                before_id=before_id,
                event_type=event_type,
                actor_user_id=actor_user_id,
            )
        return tuple(
            AuditEventView(
                id=row.id,
                occurred_at=row.occurred_at,
                event_type=row.event_type,
                actor_kind=row.actor_kind,
                actor_user_id=row.actor_user_id,
                target_kind=row.target_kind,
                target_id=row.target_id,
                request_id=row.request_id,
                details=json.loads(row.details_json) if row.details_json else {},
            )
            for row in rows
        )

    async def append_admin_role_change(
        self,
        repository: OperationalAuditRepository,
        *,
        promoted: bool,
        actor_user_id: int,
        target_user_id: int,
    ) -> None:
        old_role = UserRole.USER if promoted else UserRole.ADMIN
        new_role = UserRole.ADMIN if promoted else UserRole.USER
        await self._append(
            repository,
            event_type=(
                OperationalAuditEventType.ADMIN_PROMOTED
                if promoted
                else OperationalAuditEventType.ADMIN_DEMOTED
            ),
            actor_kind=OperationalAuditActorKind.TELEGRAM_USER,
            actor_user_id=actor_user_id,
            target_kind=OperationalAuditTargetKind.USER,
            target_id=str(target_user_id),
            details={"new_role": new_role.value, "old_role": old_role.value},
        )

    async def append_worker_desired_change(
        self,
        repository: OperationalAuditRepository,
        *,
        actor_user_id: int | None,
        local_operator: bool = False,
        pool: str,
        old_desired: int,
        new_desired: int,
    ) -> None:
        if pool not in {"download", "upload"}:
            raise ValueError("unsupported worker pool")
        if local_operator == (actor_user_id is not None):
            raise ValueError("worker audit requires exactly one actor identity")
        await self._append(
            repository,
            event_type=OperationalAuditEventType.WORKER_DESIRED_CHANGED,
            actor_kind=(
                OperationalAuditActorKind.LOCAL_OPERATOR
                if local_operator
                else OperationalAuditActorKind.TELEGRAM_USER
            ),
            actor_user_id=actor_user_id,
            target_kind=OperationalAuditTargetKind.WORKER_POOL,
            target_id=pool,
            details={"new_desired": new_desired, "old_desired": old_desired, "pool": pool},
        )

    async def append_deep_link_change(
        self,
        repository: OperationalAuditRepository,
        *,
        registered: bool,
        registry_id: int,
        target_type: DeepLinkTargetType,
    ) -> None:
        await self._append(
            repository,
            event_type=(
                OperationalAuditEventType.DEEP_LINK_REGISTERED
                if registered
                else OperationalAuditEventType.DEEP_LINK_REVOKED
            ),
            actor_kind=OperationalAuditActorKind.INTERNAL_API,
            actor_user_id=None,
            target_kind=OperationalAuditTargetKind.DEEP_LINK,
            target_id=str(registry_id),
            details={"target_type": target_type.value},
        )

    async def append_recovery(self, details: RecoveryAuditDetails, *, manual: bool) -> None:
        async with self._database.transaction() as repositories:
            await self._append(
                repositories.audit,
                event_type=(
                    OperationalAuditEventType.MANUAL_RECOVERY_EXECUTED
                    if manual
                    else OperationalAuditEventType.CRASH_RECOVERY_COMPLETED
                ),
                actor_kind=(
                    OperationalAuditActorKind.LOCAL_OPERATOR
                    if manual
                    else OperationalAuditActorKind.SYSTEM
                ),
                actor_user_id=None,
                target_kind=OperationalAuditTargetKind.RECOVERY,
                target_id="startup" if not manual else "manual",
                details=asdict(details),
            )

    async def append_artifact_cleanup(self, details: ArtifactCleanupAuditDetails) -> None:
        async with self._database.transaction() as repositories:
            await self._append(
                repositories.audit,
                event_type=OperationalAuditEventType.MANUAL_ARTIFACT_CLEANUP_EXECUTED,
                actor_kind=OperationalAuditActorKind.LOCAL_OPERATOR,
                actor_user_id=None,
                target_kind=OperationalAuditTargetKind.ARTIFACT_CLEANUP,
                target_id="configured_temp_dir",
                details=asdict(details),
            )

    async def append_backup(
        self, *, destination: Path, size_bytes: int, schema_revision: str
    ) -> None:
        filename = destination.name
        if not filename or len(filename) > 255:
            raise ValueError("invalid backup filename")
        if _SECRET_LIKE_METADATA.search(filename):
            filename = "[REDACTED]"
        async with self._database.transaction() as repositories:
            await self._append(
                repositories.audit,
                event_type=OperationalAuditEventType.SQLITE_BACKUP_CREATED,
                actor_kind=OperationalAuditActorKind.LOCAL_OPERATOR,
                actor_user_id=None,
                target_kind=OperationalAuditTargetKind.DATABASE_BACKUP,
                target_id="sqlite",
                details={
                    "filename": filename,
                    "schema_revision": schema_revision,
                    "size_bytes": size_bytes,
                },
            )

    @staticmethod
    async def _append(
        repository: OperationalAuditRepository,
        *,
        event_type: OperationalAuditEventType,
        actor_kind: OperationalAuditActorKind,
        actor_user_id: int | None,
        target_kind: OperationalAuditTargetKind,
        target_id: str,
        details: dict[str, str | int | bool],
    ) -> None:
        if len(target_id) > 64:
            raise ValueError("audit target ID is too long")
        encoded = json.dumps(details, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        if _SECRET_LIKE_METADATA.search(encoded):
            raise ValueError("secret-like audit metadata is rejected")
        if len(encoded.encode("utf-8")) > MAX_AUDIT_DETAILS_BYTES:
            raise ValueError("audit details exceed the size limit")
        await repository.append_event(
            occurred_at=utc_now(),
            event_type=event_type,
            actor_kind=actor_kind,
            actor_user_id=actor_user_id,
            target_kind=target_kind,
            target_id=target_id,
            request_id=None,
            details_json=encoded,
        )
