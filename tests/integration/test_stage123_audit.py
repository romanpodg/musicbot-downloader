from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.config import Settings
from app.core.enums import (
    DeepLinkTargetType,
    MusicProviderName,
    OperationalAuditActorKind,
    OperationalAuditEventType,
    UserRole,
)
from app.providers.base import AlbumReference
from app.services.admin_management import AdministratorManagementService
from app.services.authorization import TelegramAuthorizationService
from app.services.deep_links import DeepLinkRegistryService
from app.services.operational_audit import OperationalAuditService
from app.services.queues import WorkerSettingsService
from app.services.runtime_worker_control import RuntimeWorkerControlService
from app.storage import Database


async def _user(database: Database, telegram_id: int, role: UserRole) -> int:
    async with database.transaction() as repositories:
        return (
            await repositories.users.create_user(
                telegram_id,
                username=None,
                first_name="User",
                telegram_language_code="en",
                preferred_locale="en",
                preferred_quality_profile=None,
                role=role,
            )
        ).id


class _AlbumProvider:
    async def classify_url(self, url: str) -> AlbumReference:
        return AlbumReference(MusicProviderName.SPOTIFY, "safe-album-id", url)


class _UnusedResolver:
    async def resolve(self, url: str, *, discover: bool = False) -> SimpleNamespace:
        raise AssertionError((url, discover))


async def test_role_audit_is_atomic_exact_and_idempotent(database: Database) -> None:
    owner = await _user(database, 101, UserRole.OWNER)
    target = await _user(database, 102, UserRole.USER)
    service = AdministratorManagementService(
        database, TelegramAuthorizationService(database, owner_id=101), owner_id=101
    )

    await service.promote_to_admin(owner, target)
    await service.promote_to_admin(owner, target)
    await service.demote_admin(owner, target)
    await service.demote_admin(owner, target)

    events = await OperationalAuditService(database).list_recent(limit=10)
    assert [event.event_type for event in events] == [
        OperationalAuditEventType.ADMIN_DEMOTED,
        OperationalAuditEventType.ADMIN_PROMOTED,
    ]
    assert all(event.actor_kind is OperationalAuditActorKind.TELEGRAM_USER for event in events)
    assert all(event.actor_user_id == owner and event.target_id == str(target) for event in events)

    with patch.object(
        service._audit,
        "append_admin_role_change",
        new_callable=AsyncMock,
        side_effect=RuntimeError("controlled audit failure"),
    ):
        with pytest.raises(RuntimeError, match="controlled"):
            await service.promote_to_admin(owner, target)
    async with database.transaction() as repositories:
        stored = await repositories.users.get(target)
        assert stored is not None and stored.role is UserRole.USER


async def test_worker_audit_is_atomic_and_noops_are_not_recorded(database: Database) -> None:
    admin = await _user(database, 201, UserRole.ADMIN)
    settings = Settings(
        _env_file=None,
        download_workers_default=2,
        download_workers_max=3,
        upload_workers_default=2,
        upload_workers_max=3,
    )
    worker_settings = WorkerSettingsService(database, settings)
    runtime = SimpleNamespace(
        snapshot=AsyncMock(
            return_value=SimpleNamespace(download=SimpleNamespace(), upload=SimpleNamespace())
        )
    )
    control = RuntimeWorkerControlService(
        TelegramAuthorizationService(database, owner_id=None), worker_settings, runtime
    )

    await control.adjust_download_workers(admin, 1)
    await control.adjust_download_workers(admin, 1)
    events = await OperationalAuditService(database).list_recent(limit=10)
    assert len(events) == 1
    assert events[0].event_type is OperationalAuditEventType.WORKER_DESIRED_CHANGED
    assert events[0].details == {"new_desired": 3, "old_desired": 2, "pool": "download"}

    with patch.object(
        worker_settings._audit,
        "append_worker_desired_change",
        new_callable=AsyncMock,
        side_effect=RuntimeError("controlled audit failure"),
    ):
        with pytest.raises(RuntimeError, match="controlled"):
            await control.adjust_download_workers(admin, -1)
    assert (await worker_settings.get_values()).download.current == 3

    await worker_settings.set_upload_workers(3, local_operator=True)
    latest = (await OperationalAuditService(database).list_recent(limit=1))[0]
    assert latest.actor_kind is OperationalAuditActorKind.LOCAL_OPERATOR
    assert latest.actor_user_id is None


async def test_deep_link_audit_is_atomic_concurrent_and_token_free(database: Database) -> None:
    registry = DeepLinkRegistryService(
        database,
        _AlbumProvider(),  # type: ignore[arg-type]
        _UnusedResolver(),  # type: ignore[arg-type]
        telegram_bot_id=301,
    )
    results = await asyncio.gather(
        *(
            registry.register_from_url("https://safe.invalid/album", idempotency_key="same")
            for _ in range(100)
        )
    )
    assert sum(result.created for result in results) == 1
    token = results[0].entry.token
    await registry.revoke(token)
    await registry.revoke(token)

    events = await OperationalAuditService(database).list_recent(limit=10)
    assert [event.event_type for event in events] == [
        OperationalAuditEventType.DEEP_LINK_REVOKED,
        OperationalAuditEventType.DEEP_LINK_REGISTERED,
    ]
    assert all(event.actor_kind is OperationalAuditActorKind.INTERNAL_API for event in events)
    assert all(event.actor_user_id is None for event in events)
    assert all(event.details == {"target_type": DeepLinkTargetType.ALBUM.value} for event in events)
    assert token not in "".join(str(event) for event in events)

    failing = DeepLinkRegistryService(
        database,
        _AlbumProvider(),  # type: ignore[arg-type]
        _UnusedResolver(),  # type: ignore[arg-type]
        telegram_bot_id=302,
    )
    with patch.object(
        failing._audit,
        "append_deep_link_change",
        new_callable=AsyncMock,
        side_effect=RuntimeError("controlled audit failure"),
    ):
        with pytest.raises(RuntimeError, match="controlled"):
            await failing.register_from_url("https://safe.invalid/other")
    from sqlalchemy import func, select

    from app.storage.models import DeepLinkRegistryEntry

    async with database.engine.connect() as connection:
        count = await connection.scalar(
            select(func.count(DeepLinkRegistryEntry.id)).where(
                DeepLinkRegistryEntry.telegram_bot_id == 302
            )
        )
        assert count == 0


async def test_audit_listing_bounds_order_and_secret_metadata_protection(
    database: Database,
) -> None:
    audit = OperationalAuditService(database)
    for index in range(3):
        await audit.append_backup(
            destination=Path(f"backup-{index}.db"),
            size_bytes=index,
            schema_revision="20260820_0011",
        )
    rows = await audit.list_recent(limit=2)
    assert len(rows) == 2 and rows[0].id > rows[1].id
    with pytest.raises(ValueError):
        await audit.list_recent(limit=201)

    await audit.append_backup(
        destination=Path("access_token=super-secret.db"),
        size_bytes=1,
        schema_revision="20260820_0011",
    )
    latest = (await audit.list_recent(limit=1))[0]
    assert latest.details["filename"] == "[REDACTED]"
    assert "super-secret" not in str(latest)
