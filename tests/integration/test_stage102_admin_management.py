from __future__ import annotations

import asyncio
from typing import cast
from unittest.mock import AsyncMock, patch

import pytest

from app.core.enums import QualityProfile, UserRole
from app.i18n import LocalizationService
from app.services.admin_management import (
    ADMIN_MANAGEMENT_PAGE_SIZE,
    AdministratorManagementService,
    AdminManagementError,
    AdminManagementErrorCode,
    AdminMutationStatus,
)
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
    AuthorizationError,
    AuthorizationFailureCode,
    TelegramAuthorizationService,
)
from app.services.provider_resolution import ProviderResolver
from app.services.telegram_users import TelegramUserService
from app.storage import Database


async def _create_user(
    database: Database,
    telegram_id: int,
    role: UserRole = UserRole.USER,
    *,
    username: str | None = None,
) -> int:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            telegram_id,
            username=username,
            first_name=f"user-{telegram_id}",
            telegram_language_code="ru",
            preferred_locale="en",
            preferred_quality_profile=QualityProfile.AAC_256,
            role=role,
        )
        return user.id


async def _role(database: Database, user_id: int) -> UserRole:
    async with database.transaction() as repositories:
        user = await repositories.users.get(user_id)
        assert user is not None
        return user.role


async def test_owner_promotes_and_demotes_immediately_without_changing_other_fields(
    database: Database,
) -> None:
    owner_id = await _create_user(database, 1001, UserRole.OWNER)
    target_id = await _create_user(database, 1002, username="candidate")
    authorization = TelegramAuthorizationService(database, owner_id=1001)
    service = AdministratorManagementService(database, authorization, owner_id=1001)

    async with database.transaction() as repositories:
        before = await repositories.users.get(target_id)
        assert before is not None
        preserved = (
            before.telegram_id,
            before.username,
            before.first_name,
            before.telegram_language_code,
            before.preferred_locale,
            before.preferred_quality_profile,
            before.is_banned,
            before.created_at,
            before.last_seen_at,
        )

    promoted = await service.promote_to_admin(owner_id, target_id)
    assert promoted.status is AdminMutationStatus.PROMOTED
    assert await _role(database, target_id) is UserRole.ADMIN
    assert (await authorization.get_access_context(target_id)).can_view_admin

    already = await service.promote_to_admin(owner_id, target_id)
    assert already.status is AdminMutationStatus.ALREADY_ADMIN

    demoted = await service.demote_admin(owner_id, target_id)
    assert demoted.status is AdminMutationStatus.DEMOTED
    assert await _role(database, target_id) is UserRole.USER
    assert not (await authorization.get_access_context(target_id)).can_view_admin

    not_admin = await service.demote_admin(owner_id, target_id)
    assert not_admin.status is AdminMutationStatus.NOT_ADMIN
    async with database.transaction() as repositories:
        after = await repositories.users.get(target_id)
        assert after is not None
        assert (
            after.telegram_id,
            after.username,
            after.first_name,
            after.telegram_language_code,
            after.preferred_locale,
            after.preferred_quality_profile,
            after.is_banned,
            after.created_at,
            after.last_seen_at,
        ) == preserved

    users = TelegramUserService(database, LocalizationService(("en", "ru"), "en"), owner_id=1001)
    assert (await users.set_locale(1002, "ru")).role is UserRole.USER
    assert (await users.set_quality(1002, QualityProfile.MP3_320)).role is UserRole.USER


@pytest.mark.parametrize("actor_role", [UserRole.ADMIN, UserRole.USER, UserRole.OWNER])
async def test_non_authoritative_actors_cannot_read_or_mutate_admin_management(
    database: Database, actor_role: UserRole
) -> None:
    await _create_user(database, 1101, UserRole.OWNER)
    actor_telegram_id = 1102 if actor_role is not UserRole.OWNER else 1103
    actor_id = await _create_user(database, actor_telegram_id, actor_role)
    candidate_id = await _create_user(database, 1104)
    admin_id = await _create_user(database, 1105, UserRole.ADMIN)
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1101),
        owner_id=1101,
    )

    for operation in (
        service.list_administrators(actor_id),
        service.list_promotion_candidates(actor_id),
        service.promote_to_admin(actor_id, candidate_id),
        service.demote_admin(actor_id, admin_id),
    ):
        with pytest.raises(AuthorizationError) as denial:
            await operation
        expected = (
            AuthorizationFailureCode.OWNER_IDENTITY_MISMATCH
            if actor_role is UserRole.OWNER
            else AuthorizationFailureCode.OWNER_REQUIRED
        )
        assert denial.value.code is expected
    assert await _role(database, candidate_id) is UserRole.USER
    assert await _role(database, admin_id) is UserRole.ADMIN


async def test_missing_and_owner_targets_are_typed_and_never_mutated(database: Database) -> None:
    owner_id = await _create_user(database, 1201, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 1202, UserRole.OWNER)
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1201),
        owner_id=1201,
    )

    with pytest.raises(AdminManagementError) as missing:
        await service.promote_to_admin(owner_id, 999_999)
    assert missing.value.code is AdminManagementErrorCode.TARGET_NOT_FOUND

    for operation in (
        service.promote_to_admin(owner_id, owner_id),
        service.demote_admin(owner_id, owner_id),
        service.promote_to_admin(owner_id, stale_owner_id),
        service.demote_admin(owner_id, stale_owner_id),
    ):
        with pytest.raises(AdminManagementError) as protected:
            await operation
        assert protected.value.code is AdminManagementErrorCode.TARGET_IS_OWNER
    assert await _role(database, owner_id) is UserRole.OWNER
    assert await _role(database, stale_owner_id) is UserRole.OWNER


async def test_configured_owner_identity_is_defensively_rejected_as_target(
    database: Database,
) -> None:
    actor_id = await _create_user(database, 1301, UserRole.OWNER)
    target_id = await _create_user(database, 1302, UserRole.USER)

    class AuthorizationStub:
        async def require_authoritative_owner(self, user_id: int) -> AdminAccessContext:
            assert user_id == actor_id
            return AdminAccessContext(
                user_id=actor_id,
                telegram_id=1302,
                effective_role=UserRole.OWNER,
                permissions=frozenset(
                    (AdminPermission.ADMIN_PANEL_VIEW, AdminPermission.OWNER_ONLY)
                ),
                is_authoritative_owner=True,
            )

    service = AdministratorManagementService(
        database,
        cast(TelegramAuthorizationService, AuthorizationStub()),
        owner_id=1302,
    )
    with pytest.raises(AdminManagementError) as promotion:
        await service.promote_to_admin(actor_id, target_id)
    assert promotion.value.code is AdminManagementErrorCode.TARGET_IS_OWNER

    async with database.transaction() as repositories:
        target = await repositories.users.get(target_id)
        assert target is not None
        await repositories.users.set_role(target, UserRole.ADMIN)
    with pytest.raises(AdminManagementError) as demotion:
        await service.demote_admin(actor_id, target_id)
    assert demotion.value.code is AdminManagementErrorCode.TARGET_IS_OWNER
    assert await _role(database, target_id) is UserRole.ADMIN


async def test_lists_filter_and_paginate_existing_rows_deterministically(
    database: Database,
) -> None:
    owner_id = await _create_user(database, 1401, UserRole.OWNER)
    stale_owner_id = await _create_user(database, 1402, UserRole.OWNER)
    admin_ids = [
        await _create_user(database, 1500 + index, UserRole.ADMIN)
        for index in range(ADMIN_MANAGEMENT_PAGE_SIZE + 3)
    ]
    candidate_ids = [
        await _create_user(database, 1600 + index)
        for index in range(ADMIN_MANAGEMENT_PAGE_SIZE + 4)
    ]
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1401),
        owner_id=1401,
    )

    first_admins = await service.list_administrators(owner_id, page=-99)
    second_admins = await service.list_administrators(owner_id, page=1)
    clamped_admins = await service.list_administrators(owner_id, page=10**100)
    assert [user.id for user in first_admins.administrators] == admin_ids[:8]
    assert [user.id for user in second_admins.administrators] == admin_ids[8:]
    assert clamped_admins.page == 1
    assert owner_id not in [user.id for user in first_admins.administrators]
    assert stale_owner_id not in [user.id for user in first_admins.administrators]

    first_candidates = await service.list_promotion_candidates(owner_id, page=0)
    second_candidates = await service.list_promotion_candidates(owner_id, page=1)
    assert [user.id for user in first_candidates.candidates] == candidate_ids[:8]
    assert [user.id for user in second_candidates.candidates] == candidate_ids[8:]
    combined = first_candidates.candidates + second_candidates.candidates
    assert len({user.id for user in combined}) == len(candidate_ids)
    assert not set(admin_ids) & {user.id for user in combined}
    assert owner_id not in {user.id for user in combined}
    assert stale_owner_id not in {user.id for user in combined}


async def test_duplicate_concurrent_role_changes_have_one_winner(database: Database) -> None:
    owner_id = await _create_user(database, 1701, UserRole.OWNER)
    target_id = await _create_user(database, 1702)
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1701),
        owner_id=1701,
    )

    promotions = await asyncio.gather(
        service.promote_to_admin(owner_id, target_id),
        service.promote_to_admin(owner_id, target_id),
    )
    assert [result.status for result in promotions].count(AdminMutationStatus.PROMOTED) == 1
    assert {result.status for result in promotions} <= {
        AdminMutationStatus.PROMOTED,
        AdminMutationStatus.ALREADY_ADMIN,
        AdminMutationStatus.TARGET_STATE_CHANGED,
    }
    assert await _role(database, target_id) is UserRole.ADMIN

    demotions = await asyncio.gather(
        service.demote_admin(owner_id, target_id),
        service.demote_admin(owner_id, target_id),
    )
    assert [result.status for result in demotions].count(AdminMutationStatus.DEMOTED) == 1
    assert {result.status for result in demotions} <= {
        AdminMutationStatus.DEMOTED,
        AdminMutationStatus.NOT_ADMIN,
        AdminMutationStatus.TARGET_STATE_CHANGED,
    }
    assert await _role(database, target_id) is UserRole.USER


async def test_confirmation_reauthorizes_actor_after_management_screen_was_opened(
    database: Database,
) -> None:
    owner_id = await _create_user(database, 1801, UserRole.OWNER)
    target_id = await _create_user(database, 1802)
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1801),
        owner_id=1801,
    )

    assert (await service.get_promotion_candidate(owner_id, target_id)).id == target_id
    async with database.transaction() as repositories:
        owner = await repositories.users.get(owner_id)
        assert owner is not None
        await repositories.users.set_role(owner, UserRole.USER)

    with pytest.raises(AuthorizationError) as denial:
        await service.promote_to_admin(owner_id, target_id)
    assert denial.value.code is AuthorizationFailureCode.OWNER_REQUIRED
    assert await _role(database, target_id) is UserRole.USER


async def test_management_performs_no_provider_or_media_work(database: Database) -> None:
    owner_id = await _create_user(database, 1901, UserRole.OWNER)
    target_id = await _create_user(database, 1902)
    service = AdministratorManagementService(
        database,
        TelegramAuthorizationService(database, owner_id=1901),
        owner_id=1901,
    )

    with patch.object(ProviderResolver, "resolve", new_callable=AsyncMock) as provider_resolution:
        await service.list_administrators(owner_id)
        await service.list_promotion_candidates(owner_id)
        await service.get_promotion_candidate(owner_id, target_id)
        await service.promote_to_admin(owner_id, target_id)
        provider_resolution.assert_not_awaited()
