"""Telegram user observation and durable preference service."""

from __future__ import annotations

from dataclasses import dataclass

from app.core.enums import QualityProfile
from app.i18n import LocalizationService
from app.storage import Database
from app.storage.models import User


@dataclass(frozen=True, slots=True)
class TelegramUserProfile:
    telegram_id: int
    username: str | None
    first_name: str | None
    language_code: str | None


class TelegramUserService:
    def __init__(
        self,
        database: Database,
        i18n: LocalizationService,
        *,
        owner_id: int | None,
    ) -> None:
        self._database = database
        self._i18n = i18n
        self._owner_id = owner_id

    async def observe(self, profile: TelegramUserProfile) -> User:
        async with self._database.transaction() as repositories:
            return await repositories.users.observe_telegram_user(
                profile.telegram_id,
                username=profile.username,
                first_name=profile.first_name,
                telegram_language_code=profile.language_code,
                owner_id=self._owner_id,
            )

    async def set_quality(self, telegram_id: int, quality: QualityProfile) -> User:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_id)
            if user is None:
                raise ValueError("Telegram user must be observed first")
            return await repositories.users.set_preferred_quality(user, quality)

    async def set_locale(self, telegram_id: int, locale: str) -> User:
        normalized = self._i18n.resolve_locale(locale, None)
        if normalized != locale.strip().lower().replace("_", "-"):
            raise ValueError("unsupported locale")
        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(telegram_id)
            if user is None:
                raise ValueError("Telegram user must be observed first")
            return await repositories.users.set_preferred_locale(user, normalized)

    def locale_for(self, user: User) -> str:
        return self._i18n.resolve_locale(user.preferred_locale, user.telegram_language_code)
