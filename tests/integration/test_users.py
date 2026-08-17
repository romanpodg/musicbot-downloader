from __future__ import annotations

import pytest

from app.core.enums import QualityProfile, UserRole
from app.core.exceptions import DatabaseError
from app.services.owner_bootstrap import OwnerBootstrapResult, OwnerBootstrapService
from app.storage import Database


@pytest.mark.asyncio
async def test_user_defaults_and_locale_fields(database: Database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(
            100,
            telegram_language_code="ru",
            preferred_locale=None,
        )
        assert user.role is UserRole.USER
        assert user.default_quality is None
        assert user.telegram_language_code == "ru"
        assert user.preferred_locale is None
        assert user.is_banned is False
        assert user.created_at.utcoffset() is not None
        assert user.updated_at.utcoffset() is not None
        assert user.last_seen_at.utcoffset() is not None


@pytest.mark.asyncio
async def test_user_can_have_nullable_or_selected_default_quality(database: Database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(101, default_quality=QualityProfile.AAC_256)
        assert user.default_quality is QualityProfile.AAC_256


@pytest.mark.asyncio
async def test_telegram_id_is_unique(database: Database) -> None:
    async with database.transaction() as repositories:
        await repositories.users.create_user(200)
    with pytest.raises(DatabaseError):
        async with database.transaction() as repositories:
            await repositories.users.create_user(200)


@pytest.mark.asyncio
async def test_owner_bootstrap_does_not_create_fake_user(database: Database) -> None:
    result = await OwnerBootstrapService(database, 300).run()
    assert result is OwnerBootstrapResult.AWAITING_USER
    async with database.transaction() as repositories:
        assert await repositories.users.get_by_telegram_id(300) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("initial_role", [UserRole.USER, UserRole.ADMIN])
async def test_owner_bootstrap_promotes_existing_user(
    database: Database, initial_role: UserRole
) -> None:
    async with database.transaction() as repositories:
        await repositories.users.create_user(400, role=initial_role)

    result = await OwnerBootstrapService(database, 400).run()
    assert result is OwnerBootstrapResult.PROMOTED
    async with database.transaction() as repositories:
        owner = await repositories.users.get_by_telegram_id(400)
        assert owner is not None
        assert owner.role is UserRole.OWNER
