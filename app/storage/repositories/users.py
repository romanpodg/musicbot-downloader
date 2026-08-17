"""User persistence operations."""

from __future__ import annotations

from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.enums import QualityProfile, UserRole
from app.storage.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return cast(
            User | None,
            await self._session.scalar(select(User).where(User.telegram_id == telegram_id)),
        )

    async def create_user(
        self,
        telegram_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        telegram_language_code: str | None = None,
        preferred_locale: str | None = None,
        default_quality: QualityProfile | None = None,
        role: UserRole = UserRole.USER,
    ) -> User:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            telegram_language_code=telegram_language_code,
            preferred_locale=preferred_locale,
            default_quality=default_quality,
            role=role,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def set_role(self, user: User, role: UserRole) -> User:
        user.role = role
        await self._session.flush()
        return user
