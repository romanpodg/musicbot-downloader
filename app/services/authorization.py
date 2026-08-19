"""Central, persistence-backed authorization policy for privileged operations."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import UserRole
from app.core.exceptions import MusicBotError
from app.storage import Database

logger = logging.getLogger(__name__)


class AdminPermission(StrEnum):
    ADMIN_PANEL_VIEW = "ADMIN_PANEL_VIEW"
    WORKERS_MANAGE = "WORKERS_MANAGE"
    OWNER_ONLY = "OWNER_ONLY"


class AuthorizationFailureCode(StrEnum):
    ACCESS_DENIED = "ACCESS_DENIED"
    OWNER_REQUIRED = "OWNER_REQUIRED"
    SUBJECT_NOT_FOUND = "SUBJECT_NOT_FOUND"
    OWNER_IDENTITY_MISMATCH = "OWNER_IDENTITY_MISMATCH"


class AuthorizationError(MusicBotError):
    """Typed denial with no presentation text or sensitive configuration."""

    def __init__(self, code: AuthorizationFailureCode) -> None:
        super().__init__()
        self.code = code


@dataclass(frozen=True, slots=True)
class AdminAccessContext:
    user_id: int
    telegram_id: int
    effective_role: UserRole
    permissions: frozenset[AdminPermission]
    is_authoritative_owner: bool

    @property
    def can_view_admin(self) -> bool:
        return AdminPermission.ADMIN_PANEL_VIEW in self.permissions


class TelegramAuthorizationService:
    """Resolve current authority from durable user state and immutable OWNER_ID."""

    def __init__(self, database: Database, *, owner_id: int | None) -> None:
        self._database = database
        self._owner_id = owner_id

    async def get_access_context(self, user_id: int) -> AdminAccessContext:
        async with self._database.transaction() as repositories:
            user = await repositories.users.get(user_id)
        if user is None:
            logger.warning(
                "Privileged authorization denied",
                extra={"action": "access_context", "user_id": user_id, "code": "subject_missing"},
            )
            raise AuthorizationError(AuthorizationFailureCode.SUBJECT_NOT_FOUND)

        authoritative_owner = (
            user.role is UserRole.OWNER
            and self._owner_id is not None
            and user.telegram_id == self._owner_id
        )
        if user.role is UserRole.OWNER and not authoritative_owner:
            logger.error(
                "Privileged-role invariant violation",
                extra={
                    "action": "access_context",
                    "user_id": user.id,
                    "telegram_id": user.telegram_id,
                    "code": "owner_identity_mismatch",
                },
            )
            permissions: frozenset[AdminPermission] = frozenset()
        elif authoritative_owner:
            permissions = frozenset(
                (
                    AdminPermission.ADMIN_PANEL_VIEW,
                    AdminPermission.WORKERS_MANAGE,
                    AdminPermission.OWNER_ONLY,
                )
            )
        elif user.role is UserRole.ADMIN:
            permissions = frozenset(
                (AdminPermission.ADMIN_PANEL_VIEW, AdminPermission.WORKERS_MANAGE)
            )
        else:
            permissions = frozenset()

        return AdminAccessContext(
            user_id=user.id,
            telegram_id=user.telegram_id,
            effective_role=user.role,
            permissions=permissions,
            is_authoritative_owner=authoritative_owner,
        )

    async def require_permission(
        self, user_id: int, permission: AdminPermission
    ) -> AdminAccessContext:
        context = await self.get_access_context(user_id)
        if permission in context.permissions:
            return context

        if context.effective_role is UserRole.OWNER and not context.is_authoritative_owner:
            code = AuthorizationFailureCode.OWNER_IDENTITY_MISMATCH
        elif permission is AdminPermission.OWNER_ONLY:
            code = AuthorizationFailureCode.OWNER_REQUIRED
        else:
            code = AuthorizationFailureCode.ACCESS_DENIED
        logger.info(
            "Privileged authorization denied",
            extra={
                "action": permission.value,
                "user_id": context.user_id,
                "telegram_id": context.telegram_id,
                "code": code.value,
            },
        )
        raise AuthorizationError(code)

    async def require_authoritative_owner(self, user_id: int) -> AdminAccessContext:
        """Stage 10.2 owner-only guard foundation."""

        return await self.require_permission(user_id, AdminPermission.OWNER_ONLY)
