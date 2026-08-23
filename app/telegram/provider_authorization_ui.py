"""Tracked aiogram-side completion watchers for provider authorization messages."""

from __future__ import annotations

import asyncio
import logging

from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from app.core.enums import MusicProviderName
from app.services.provider_accounts import ProviderAccountManagementService
from app.telegram.provider_accounts_presentation import ProviderAccountsPresentation

logger = logging.getLogger(__name__)


class ProviderAuthorizationUiManager:
    def __init__(
        self,
        service: ProviderAccountManagementService,
        presentation: ProviderAccountsPresentation,
    ) -> None:
        self._service = service
        self._presentation = presentation
        self._tasks: set[asyncio.Task[None]] = set()

    def watch(
        self,
        message: Message,
        *,
        actor_user_id: int,
        provider: MusicProviderName,
        flow_id: str,
        locale: str,
    ) -> None:
        task = asyncio.create_task(
            self._watch(message, actor_user_id, provider, flow_id, locale),
            name=f"provider-authorization-ui-{provider.value}-{flow_id}",
        )
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def close(self) -> None:
        tasks = tuple(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()

    async def _watch(
        self,
        message: Message,
        actor_user_id: int,
        provider: MusicProviderName,
        flow_id: str,
        locale: str,
    ) -> None:
        try:
            outcome = await self._service.wait_authorization(actor_user_id, provider, flow_id)
            status = await self._service.get_status(actor_user_id, provider)
            await message.edit_text(
                self._presentation.authorization_result_text(status, outcome, locale),
                reply_markup=self._presentation.detail_keyboard(status, locale),
            )
        except asyncio.CancelledError:
            raise
        except TelegramAPIError:
            # The flow result is authoritative even if its Telegram message disappeared.
            return
        except Exception:
            logger.error(
                "Provider authorization UI completion failed",
                extra={"action": "provider_authorization_ui", "provider": provider.value},
            )
