"""Stage 14 navigation flow with no Telegram presentation types."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.application.search import SearchTracksUseCase
from app.application.ux.services.state import UserUxStateService, UxState
from app.core.search import TrackSearchRequest
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

    def __init__(
        self,
        users: TelegramUserService,
        states: UserUxStateService,
        search_tracks: SearchTracksUseCase | None = None,
    ) -> None:
        self._users = users
        self._states = states
        self._search_tracks = search_tracks

    async def start(self, profile: TelegramUserProfile) -> UxScreen:
        await self._users.observe(profile)
        return self._screen(profile.telegram_id, "ux.welcome", UxMenu.MAIN)

    async def help(self, profile: TelegramUserProfile) -> UxScreen:
        await self._users.observe(profile)
        return self._screen(profile.telegram_id, "ux.help")

    async def open_menu(self, profile: TelegramUserProfile, menu: UxMenu = UxMenu.MAIN) -> UxScreen:
        await self._users.observe(profile)
        if menu is UxMenu.SEARCH:
            return self._screen(
                profile.telegram_id, "ux.search.prompt", state=UxState.SEARCH_INPUT, menu=menu
            )
        key = {
            UxMenu.MAIN: "ux.menu.main",
            UxMenu.SEARCH: "ux.menu.search",
            UxMenu.ACCOUNT: "ux.menu.account",
            UxMenu.PROVIDERS: "ux.menu.providers",
            UxMenu.SETTINGS: "ux.menu.settings",
        }[menu]
        return self._screen(profile.telegram_id, key, menu)

    async def begin_search(self, profile: TelegramUserProfile) -> UxScreen:
        await self._users.observe(profile)
        return self._screen(
            profile.telegram_id,
            "ux.search.prompt",
            state=UxState.SEARCH_INPUT,
            menu=UxMenu.SEARCH,
        )

    async def search(self, profile: TelegramUserProfile, query: str) -> UxScreen:
        await self._users.observe(profile)
        request = TrackSearchRequest(query=query)
        self._states.transition(profile.telegram_id, UxState.SEARCHING)
        try:
            if self._search_tracks is None:
                raise RuntimeError("track search use case is not composed")
            await self._search_tracks.recognize(request)
        except Exception:
            self._states.transition(profile.telegram_id, UxState.ERROR)
            raise
        return self._screen(
            profile.telegram_id,
            "ux.search.results",
            state=UxState.SEARCH_RESULTS,
            menu=UxMenu.SEARCH,
        )

    def awaiting_search_input(self, user_id: int) -> bool:
        return self._states.current(user_id) is UxState.SEARCH_INPUT

    def _screen(
        self,
        user_id: int,
        message_key: str,
        menu: UxMenu | None = None,
        state: UxState = UxState.MENU,
    ) -> UxScreen:
        return UxScreen(message_key, self._states.transition(user_id, state), menu)
