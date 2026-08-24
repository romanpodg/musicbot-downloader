from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

from aiogram import Bot, Dispatcher
from aiogram.enums import ChatType, MessageEntityType
from aiogram.types import CallbackQuery, Chat, Message, MessageEntity, Update
from aiogram.types import User as TgUser

from app.application.ux import UserUxStateService, UxErrorService, UxFlowService
from app.i18n import LocalizationService
from app.services.telegram_users import TelegramUserService
from app.telegram.callbacks import encode_ux_callback
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessageService
from app.telegram.ux_handlers import UxHandlerDependencies, create_ux_router


def _message_update(update_id: int, user: TgUser, chat: Chat, text: str) -> Update:
    return Update(
        update_id=update_id,
        message=Message(
            message_id=update_id,
            date=datetime.now(UTC),
            chat=chat,
            from_user=user,
            text=text,
            entities=[
                MessageEntity(
                    type=MessageEntityType.BOT_COMMAND,
                    offset=0,
                    length=len(text.split()[0]),
                )
            ],
        ),
    )


async def test_stage14_start_and_menu_navigation(database) -> None:  # type: ignore[no-untyped-def]
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
    bot = Bot("123456:TEST_TOKEN")
    user = TgUser(id=14001, is_bot=False, first_name="Stage 14", language_code="en")
    chat = Chat(id=14001, type=ChatType.PRIVATE)
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            await dispatcher.feed_update(bot, _message_update(1, user, chat, "/start"))
            callback_message = Message(
                message_id=2,
                date=datetime.now(UTC),
                chat=chat,
                from_user=user,
                text="menu",
            )
            await dispatcher.feed_update(
                bot,
                Update(
                    update_id=2,
                    callback_query=CallbackQuery(
                        id="stage14-menu",
                        from_user=user,
                        chat_instance="chat",
                        message=callback_message,
                        data=encode_ux_callback("menu", "section", "settings"),
                    ),
                ),
            )
            assert api.await_count >= 3
        async with database.transaction() as repositories:
            assert await repositories.users.get_by_telegram_id(14001) is not None
        assert states.current(14001).value == "MENU"
    finally:
        await bot.session.close()


async def test_stage14_error_is_sanitized_for_the_user(database) -> None:  # type: ignore[no-untyped-def]
    class FailingFlow:
        async def start(self, profile):  # type: ignore[no-untyped-def]
            raise RuntimeError("database password=not-for-telegram")

    i18n = LocalizationService(("en", "ru"), "en")
    users = TelegramUserService(database, i18n, owner_id=None)
    dispatcher = Dispatcher()
    dispatcher.include_router(
        create_ux_router(
            UxHandlerDependencies(
                users,
                FailingFlow(),  # type: ignore[arg-type]
                UxMessageService(i18n),
                UxKeyboardFactory(i18n),
                UxErrorService(),
            )
        )
    )
    bot = Bot("123456:TEST_TOKEN")
    user = TgUser(id=14002, is_bot=False, first_name="Stage 14", language_code="en")
    chat = Chat(id=14002, type=ChatType.PRIVATE)
    try:
        with patch.object(Bot, "__call__", new_callable=AsyncMock, return_value=True) as api:
            await dispatcher.feed_update(bot, _message_update(1, user, chat, "/start"))
            rendered = repr(api.await_args_list)
            assert "Operation failed" in rendered
            assert "not-for-telegram" not in rendered
    finally:
        await bot.session.close()
