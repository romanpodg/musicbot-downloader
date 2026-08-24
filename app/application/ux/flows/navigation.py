"""Stage 14 navigation flow with no Telegram presentation types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.ux.services.state import UserUxStateService, UxState
from app.services.telegram_users import TelegramUserProfile, TelegramUserService


class UxMenu(StrEnum):
    MAIN = "main"
    SEARCH = "search"
    ACCOUNT = "account"
    PROVIDERS = "providers"
    SETTINGS = "settings"


@dataclass(frozen=True, slots=True)
class UxScreen:
    message_key: str
    state: UxState
    menu: UxMenu | None = None


class UxFlowService:
    """Coordinates observation and navigation; handlers only render its result."""

    def __init__(self, users: TelegramUserService, states: UserUxStateService) -> None:
        self._users = users
        self._states = states

    async def start(self, profile: TelegramUserProfile) -> UxScreen:
        await self._users.observe(profile)
        return self._screen(profile.telegram_id, "ux.welcome", UxMenu.MAIN)

    async def help(self, profile: TelegramUserProfile) -> UxScreen:
        await self._users.observe(profile)
        return self._screen(profile.telegram_id, "ux.help")

    async def open_menu(self, profile: TelegramUserProfile, menu: UxMenu = UxMenu.MAIN) -> UxScreen:
        await self._users.observe(profile)
        key = {
            UxMenu.MAIN: "ux.menu.main",
            UxMenu.SEARCH: "ux.menu.search",
            UxMenu.ACCOUNT: "ux.menu.account",
            UxMenu.PROVIDERS: "ux.menu.providers",
            UxMenu.SETTINGS: "ux.menu.settings",
        }[menu]
        return self._screen(profile.telegram_id, key, menu)

    def _screen(self, user_id: int, message_key: str, menu: UxMenu | None = None) -> UxScreen:
        return UxScreen(message_key, self._states.transition(user_id, UxState.MENU), menu)
