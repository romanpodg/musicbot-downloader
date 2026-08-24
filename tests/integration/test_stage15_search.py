from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser

from app.application.search import SearchTracksUseCase
from app.application.ux import UserUxStateService, UxErrorService, UxFlowService, UxState
from app.core.enums import MusicProviderName
from app.core.search import Artist, Track, TrackSearchRequest
from app.i18n import LocalizationService
from app.providers.search import TrackSearchProvider
from app.services.telegram_users import TelegramUserService
from app.services.track_search import TrackSearchProviderRegistry, TrackSearchService
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessageService
from app.telegram.ux_handlers import UxHandlerDependencies, create_ux_router


class FakeSearchProvider(TrackSearchProvider):
    requests: list[TrackSearchRequest]

    def __init__(self) -> None:
        self.requests = []

    @property
    def provider(self) -> MusicProviderName:
        return MusicProviderName.SPOTIFY

    async def search(self, request: TrackSearchRequest) -> tuple[Track, ...]:
        self.requests.append(request)
        return (
            Track(
                id="result:spotify:one-more-time",
                title="One More Time",
                artists=(Artist("Daft Punk"),),
                provider=MusicProviderName.SPOTIFY,
                provider_track_id="one-more-time",
            ),
        )


def _message_update(update_id: int, user: TgUser, chat: Chat, text: str) -> Update:
    entities = []
    if text.startswith("/"):
        entities = [
            MessageEntity(
                type=MessageEntityType.BOT_COMMAND,
                offset=0,
                length=len(text.split()[0]),
            )
        ]
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=chat,
            from_user=user,
            text=text,
            entities=entities,
        ),
    )


async def test_stage15_telegram_search_uses_normalized_mock_provider(database) -> None:  # type: ignore[no-untyped-def]
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    states = UserUxStateService()
    provider = FakeSearchProvider()
    use_case = SearchTracksUseCase(TrackSearchService(TrackSearchProviderRegistry((provider,))))
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_ux_router(
            UxHandlerDependencies(
                users,
                UxFlowService(users, states, use_case),
                UxMessageService(i18n),
                UxKeyboardFactory(i18n),
                UxErrorService(),
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")
    user = TgUser(id=15001, is_bot=False, first_name="Stage 15", language_code="en")
    chat = Chat(id=15001, type=ChatType.PRIVATE)
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            await dispatcher.feed_update(bot, _message_update(1, user, chat, "/search"))
            assert states.current(15001) is UxState.SEARCH_INPUT
            await dispatcher.feed_update(
                bot, _message_update(2, user, chat, "Daft Punk One More Time")
            )
            assert "Search completed" in repr(api.await_args_list)
        assert provider.requests == [TrackSearchRequest("Daft Punk One More Time")]
        assert states.current(15001) is UxState.SEARCH_RESULTS
    finally:
        await bot.session.close()


async def test_stage15_search_filter_preserves_non_search_text_routes(database) -> None:  # type: ignore[no-untyped-def]
    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    states = UserUxStateService()
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_ux_router(
            UxHandlerDependencies(
                users,
                UxFlowService(users, states),
                UxMessageService(i18n),
                UxKeyboardFactory(i18n),
                UxErrorService(),
            )
        )
    )
    downstream = Router(name="stage9-preservation-probe")
    received: list[str] = []

    @downstream.message()
    async def receive_text(message: Message) -> None:
        if message.text is not None:
            received.append(message.text)

    dispatcher.include_router(downstream)
    bot = Bot("123456:TEST_TOKEN")
    user = TgUser(id=15002, is_bot=False, first_name="Stage 15", language_code="en")
    chat = Chat(id=15002, type=ChatType.PRIVATE)
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True):
            await dispatcher.feed_update(
                bot, _message_update(1, user, chat, "https://open.spotify.com/track/example")
            )
        assert received == ["https://open.spotify.com/track/example"]
    finally:
        await bot.session.close()
