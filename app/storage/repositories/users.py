"""User persistence operations."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import func, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from app.core.enums import QualityProfile, UserRole
from app.storage.models import User
from app.storage.models.base import utc_now


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        return cast(
            User | None,
            await self._session.scalar(select(User).where(User.telegram_id == telegram_id)),
        )

    async def get(self, user_id: int) -> User | None:
        return await self._session.get(User, user_id)

    async def count_admin_promotion_candidates(self, *, owner_telegram_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == UserRole.USER,
                    User.telegram_id != owner_telegram_id,
                )
            )
            or 0
        )

    async def list_admin_promotion_candidates(
        self, *, owner_telegram_id: int, limit: int, offset: int
    ) -> tuple[User, ...]:
        rows = await self._session.scalars(
            select(User)
            .where(
                User.role == UserRole.USER,
                User.telegram_id != owner_telegram_id,
            )
            .order_by(User.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(rows)

    async def count_administrators(self, *, owner_telegram_id: int) -> int:
        return int(
            await self._session.scalar(
                select(func.count())
                .select_from(User)
                .where(
                    User.role == UserRole.ADMIN,
                    User.telegram_id != owner_telegram_id,
                )
            )
            or 0
        )

    async def list_administrators(
        self, *, owner_telegram_id: int, limit: int, offset: int
    ) -> tuple[User, ...]:
        rows = await self._session.scalars(
            select(User)
            .where(
                User.role == UserRole.ADMIN,
                User.telegram_id != owner_telegram_id,
            )
            .order_by(User.id.asc())
            .limit(limit)
            .offset(offset)
        )
        return tuple(rows)

    async def conditional_promote_user_to_admin(
        self, *, actor_user_id: int, target_user_id: int, owner_telegram_id: int
    ) -> bool:
        return await self._conditional_admin_transition(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            owner_telegram_id=owner_telegram_id,
            expected_role=UserRole.USER,
            new_role=UserRole.ADMIN,
        )

    async def conditional_demote_admin_to_user(
        self, *, actor_user_id: int, target_user_id: int, owner_telegram_id: int
    ) -> bool:
        return await self._conditional_admin_transition(
            actor_user_id=actor_user_id,
            target_user_id=target_user_id,
            owner_telegram_id=owner_telegram_id,
            expected_role=UserRole.ADMIN,
            new_role=UserRole.USER,
        )

    async def _conditional_admin_transition(
        self,
        *,
        actor_user_id: int,
        target_user_id: int,
        owner_telegram_id: int,
        expected_role: UserRole,
        new_role: UserRole,
    ) -> bool:
        actor = aliased(User)
        actor_is_authoritative_owner = (
            select(actor.id)
            .where(
                actor.id == actor_user_id,
                actor.role == UserRole.OWNER,
                actor.telegram_id == owner_telegram_id,
            )
            .exists()
        )
        result = await self._session.execute(
            update(User)
            .where(
                User.id == target_user_id,
                User.role == expected_role,
                User.telegram_id != owner_telegram_id,
                actor_is_authoritative_owner,
            )
            .values(role=new_role)
            .execution_options(synchronize_session=False)
        )
        return bool(cast(CursorResult[Any], result).rowcount)

    async def create_user(
        self,
        telegram_id: int,
        *,
        username: str | None = None,
        first_name: str | None = None,
        telegram_language_code: str | None = None,
        preferred_locale: str | None = None,
        preferred_quality_profile: QualityProfile | None = None,
        default_quality: QualityProfile | None = None,
        role: UserRole = UserRole.USER,
    ) -> User:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            telegram_language_code=telegram_language_code,
            preferred_locale=preferred_locale,
            preferred_quality_profile=preferred_quality_profile or default_quality,
            role=role,
        )
        self._session.add(user)
        await self._session.flush()
        return user

    async def observe_telegram_user(
        self,
        telegram_id: int,
        *,
        username: str | None,
        first_name: str | None,
        telegram_language_code: str | None,
        owner_id: int | None,
    ) -> User:
        user = await self.get_by_telegram_id(telegram_id)
        if user is None:
            user = await self.create_user(
                telegram_id,
                username=username,
                first_name=first_name,
                telegram_language_code=telegram_language_code,
                role=UserRole.OWNER if telegram_id == owner_id else UserRole.USER,
            )
        else:
            user.username = username
            user.first_name = first_name
            if telegram_language_code:
                user.telegram_language_code = telegram_language_code
            if telegram_id == owner_id:
                user.role = UserRole.OWNER
            user.last_seen_at = utc_now()
            await self._session.flush()
        return user

    async def set_preferred_quality(self, user: User, quality_profile: QualityProfile) -> User:
        user.preferred_quality_profile = quality_profile
        await self._session.flush()
        return user

    async def set_preferred_locale(self, user: User, locale: str) -> User:
        user.preferred_locale = locale
        await self._session.flush()
        return user

    async def set_role(self, user: User, role: UserRole) -> User:
        user.role = role
        await self._session.flush()
        return user
