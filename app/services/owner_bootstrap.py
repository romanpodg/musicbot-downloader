"""Ensure an observed Telegram user matching OWNER_ID has the OWNER role."""

from __future__ import annotations

from enum import StrEnum

from app.core.enums import UserRole
from app.storage.database import Database


class OwnerBootstrapResult(StrEnum):
    NOT_CONFIGURED = "not_configured"
    AWAITING_USER = "awaiting_user"
    ALREADY_OWNER = "already_owner"
    PROMOTED = "promoted"


class OwnerBootstrapService:
    def __init__(self, database: Database, owner_id: int | None) -> None:
        self._database = database
        self._owner_id = owner_id

    async def run(self) -> OwnerBootstrapResult:
        if self._owner_id is None:
            return OwnerBootstrapResult.NOT_CONFIGURED

        async with self._database.transaction() as repositories:
            user = await repositories.users.get_by_telegram_id(self._owner_id)
            if user is None:
                # The future Telegram user-observation flow will create the row with
                # real profile data; bootstrap deliberately creates no fake profile.
                return OwnerBootstrapResult.AWAITING_USER
            if user.role is UserRole.OWNER:
                return OwnerBootstrapResult.ALREADY_OWNER
            await repositories.users.set_role(user, UserRole.OWNER)
            return OwnerBootstrapResult.PROMOTED
