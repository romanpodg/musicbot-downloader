"""Stage 28 home and active-downloads adapter; lifecycle work stays in services."""

from __future__ import annotations

from dataclasses import dataclass

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message

from app.services.download_activity import DownloadActivity, DownloadActivityService
from app.services.telegram_users import TelegramUserProfile, TelegramUserService


@dataclass(frozen=True, slots=True)
class Stage28HandlerDependencies:
    users: TelegramUserService
    activity: DownloadActivityService


def create_stage28_router(dependencies: Stage28HandlerDependencies) -> Router:
    router = Router(name="stage28-telegram-ux")

    @router.message(Command("downloads"))
    async def downloads(message: Message) -> None:
        if message.from_user is None or message.chat.id != message.from_user.id:
            return
        await dependencies.users.observe(_profile(message.from_user))
        activities = await dependencies.activity.list_for_telegram_user(message.from_user.id)
        await message.answer(
            _downloads_text(activities), reply_markup=_downloads_keyboard(activities)
        )

    @router.callback_query(F.data.startswith("u28:d:"))
    async def detail(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer("This action is no longer available.", show_alert=True)
            return
        request_id = _request_id(callback.data)
        if request_id is None:
            await callback.answer("This action is no longer available.", show_alert=True)
            return
        await callback.answer()
        activity = await dependencies.activity.detail_for_telegram_user(
            callback.from_user.id, request_id
        )
        if activity is None:
            await callback.answer("This download is no longer available.", show_alert=True)
            return
        await callback.message.edit_text(_detail_text(activity), reply_markup=_detail_keyboard())

    @router.callback_query(F.data == "u28:downloads")
    async def downloads_callback(callback: CallbackQuery) -> None:
        if not isinstance(callback.message, Message):
            await callback.answer()
            return
        await callback.answer()
        activities = await dependencies.activity.list_for_telegram_user(callback.from_user.id)
        await callback.message.edit_text(
            _downloads_text(activities), reply_markup=_downloads_keyboard(activities)
        )

    return router


def _profile(user: object) -> TelegramUserProfile:
    from aiogram.types import User as AiogramUser

    if not isinstance(user, AiogramUser):
        raise TypeError("invalid Telegram user")
    return TelegramUserProfile(user.id, user.username, user.first_name, user.language_code)


def _downloads_text(activities: list[DownloadActivity]) -> str:
    if not activities:
        return "Downloads\n\nNo active or recent downloads."
    rows = ["Downloads"]
    for item in activities:
        creator = f" — {item.artist}" if item.artist else ""
        rows.append(f"{item.title}{creator}\n{item.view.label}")
    return "\n\n".join(rows)


def _downloads_keyboard(activities: list[DownloadActivity]) -> InlineKeyboardMarkup:
    rows = [
        [InlineKeyboardButton(text=item.title[:48], callback_data=f"u28:d:{item.request_id}")]
        for item in activities
    ]
    rows.extend(
        [
            [InlineKeyboardButton(text="Search", callback_data="ux1:menu:section:search")],
            [InlineKeyboardButton(text="Home", callback_data="ux1:menu:open")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _detail_text(item: DownloadActivity) -> str:
    creator = f" — {item.artist}" if item.artist else ""
    return f"{item.title}{creator}\n\n{item.view.label}"


def _detail_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Back", callback_data="u28:downloads")],
            [InlineKeyboardButton(text="Home", callback_data="ux1:menu:open")],
        ]
    )


def _request_id(value: str | None) -> int | None:
    if value is None:
        return None
    parts = value.split(":")
    if len(parts) != 3 or parts[:2] != ["u28", "d"] or not parts[2].isdigit():
        return None
    parsed = int(parts[2])
    return parsed if parsed > 0 else None
