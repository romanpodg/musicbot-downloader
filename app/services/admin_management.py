"""Owner-only management of database-backed administrator roles."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum

from app.core.enums import UserRole
from app.core.exceptions import DatabaseConcurrencyError, MusicBotError
from app.services.authorization import AdminAccessContext, TelegramAuthorizationService
from app.storage import Database
from app.storage.models import User

logger = logging.getLogger(__name__)

ADMIN_MANAGEMENT_PAGE_SIZE = 8
_MAX_CONCURRENCY_ATTEMPTS = 3


class AdminManagementErrorCode(StrEnum):
    TARGET_NOT_FOUND = "TARGET_NOT_FOUND"
    TARGET_IS_OWNER = "TARGET_IS_OWNER"
    TARGET_STATE_CHANGED = "TARGET_STATE_CHANGED"


class AdminManagementError(MusicBotError):
    def __init__(self, code: AdminManagementErrorCode) -> None:
        super().__init__()
        self.code = code


class AdminMutationStatus(StrEnum):
    PROMOTED = "PROMOTED"
    DEMOTED = "DEMOTED"
    ALREADY_ADMIN = "ALREADY_ADMIN"
    NOT_ADMIN = "NOT_ADMIN"
    TARGET_STATE_CHANGED = "TARGET_STATE_CHANGED"


@dataclass(frozen=True, slots=True)
class ManagedUser:
    id: int
    telegram_id: int
    username: str | None
    first_name: str | None


@dataclass(frozen=True, slots=True)
class AdministratorPage:
    owner: ManagedUser
    administrators: tuple[ManagedUser, ...]
    total_count: int
    page: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class PromotionCandidatePage:
    candidates: tuple[ManagedUser, ...]
    total_count: int
    page: int
    total_pages: int


@dataclass(frozen=True, slots=True)
class AdminMutationResult:
    status: AdminMutationStatus
    target: ManagedUser


class AdministratorManagementService:
    """Authorize every read/change and expose only Stage 10.2 transitions."""

    def __init__(
        self,
        database: Database,
        authorization: TelegramAuthorizationService,
        *,
        owner_id: int | None,
    ) -> None:
        self._database = database
        self._authorization = authorization
        self._owner_id = owner_id

    async def authorize_owner(self, actor_user_id: int) -> AdminAccessContext:
        return await self._authorization.require_authoritative_owner(actor_user_id)

    async def list_administrators(self, actor_user_id: int, *, page: int = 0) -> AdministratorPage:
        access = await self.authorize_owner(actor_user_id)
        owner_id = self._required_owner_id()
        async with self._database.transaction() as repositories:
            owner = await repositories.users.get(access.user_id)
            if owner is None:
                raise AdminManagementError(AdminManagementErrorCode.TARGET_NOT_FOUND)
            total = await repositories.users.count_administrators(owner_telegram_id=owner_id)
            selected_page, total_pages = _page_bounds(page, total)
            users = await repositories.users.list_administrators(
                owner_telegram_id=owner_id,
                limit=ADMIN_MANAGEMENT_PAGE_SIZE,
                offset=selected_page * ADMIN_MANAGEMENT_PAGE_SIZE,
            )
        return AdministratorPage(
            owner=_managed(owner),
            administrators=tuple(_managed(user) for user in users),
            total_count=total,
            page=selected_page,
            total_pages=total_pages,
        )

    async def list_promotion_candidates(
        self, actor_user_id: int, *, page: int = 0
    ) -> PromotionCandidatePage:
        await self.authorize_owner(actor_user_id)
        owner_id = self._required_owner_id()
        async with self._database.transaction() as repositories:
            total = await repositories.users.count_admin_promotion_candidates(
                owner_telegram_id=owner_id
            )
            selected_page, total_pages = _page_bounds(page, total)
            users = await repositories.users.list_admin_promotion_candidates(
                owner_telegram_id=owner_id,
                limit=ADMIN_MANAGEMENT_PAGE_SIZE,
                offset=selected_page * ADMIN_MANAGEMENT_PAGE_SIZE,
            )
        return PromotionCandidatePage(
            candidates=tuple(_managed(user) for user in users),
            total_count=total,
            page=selected_page,
            total_pages=total_pages,
        )

    async def get_administrator(self, actor_user_id: int, target_user_id: int) -> ManagedUser:
        await self.authorize_owner(actor_user_id)
        target = await self._get_target(target_user_id)
        self._protect_owner(target)
        if target.role is not UserRole.ADMIN:
            raise AdminManagementError(AdminManagementErrorCode.TARGET_STATE_CHANGED)
        return _managed(target)

    async def get_promotion_candidate(self, actor_user_id: int, target_user_id: int) -> ManagedUser:
        await self.authorize_owner(actor_user_id)
        target = await self._get_target(target_user_id)
        self._protect_owner(target)
        if target.role is not UserRole.USER:
            raise AdminManagementError(AdminManagementErrorCode.TARGET_STATE_CHANGED)
        return _managed(target)

    async def promote_to_admin(
        self, actor_user_id: int, target_user_id: int
    ) -> AdminMutationResult:
        return await self._change_role(actor_user_id, target_user_id, promote=True)

    async def demote_admin(self, actor_user_id: int, target_user_id: int) -> AdminMutationResult:
        return await self._change_role(actor_user_id, target_user_id, promote=False)

    async def _change_role(
        self, actor_user_id: int, target_user_id: int, *, promote: bool
    ) -> AdminMutationResult:
        last_concurrency_error: DatabaseConcurrencyError | None = None
        for attempt in range(_MAX_CONCURRENCY_ATTEMPTS):
            await self.authorize_owner(actor_user_id)
            owner_id = self._required_owner_id()
            target = await self._get_target(target_user_id)
            self._protect_owner(target)
            if promote and target.role is UserRole.ADMIN:
                return AdminMutationResult(AdminMutationStatus.ALREADY_ADMIN, _managed(target))
            if not promote and target.role is UserRole.USER:
                return AdminMutationResult(AdminMutationStatus.NOT_ADMIN, _managed(target))
            expected = UserRole.USER if promote else UserRole.ADMIN
            if target.role is not expected:
                raise AdminManagementError(AdminManagementErrorCode.TARGET_STATE_CHANGED)
            try:
                async with self._database.transaction() as repositories:
                    if promote:
                        changed = await repositories.users.conditional_promote_user_to_admin(
                            actor_user_id=actor_user_id,
                            target_user_id=target_user_id,
                            owner_telegram_id=owner_id,
                        )
                    else:
                        changed = await repositories.users.conditional_demote_admin_to_user(
                            actor_user_id=actor_user_id,
                            target_user_id=target_user_id,
                            owner_telegram_id=owner_id,
                        )
            except DatabaseConcurrencyError as exc:
                last_concurrency_error = exc
                if attempt + 1 < _MAX_CONCURRENCY_ATTEMPTS:
                    await asyncio.sleep(0)
                    continue
                raise
            if not changed:
                await self.authorize_owner(actor_user_id)
                current = await self._get_target(target_user_id)
                self._protect_owner(current)
                stale_status = (
                    AdminMutationStatus.ALREADY_ADMIN
                    if promote and current.role is UserRole.ADMIN
                    else AdminMutationStatus.NOT_ADMIN
                    if not promote and current.role is UserRole.USER
                    else AdminMutationStatus.TARGET_STATE_CHANGED
                )
                return AdminMutationResult(stale_status, _managed(current))

            status = AdminMutationStatus.PROMOTED if promote else AdminMutationStatus.DEMOTED
            logger.info(
                "Administrator role changed",
                extra={
                    "action": "admin_promoted" if promote else "admin_demoted",
                    "actor_user_id": actor_user_id,
                    "target_user_id": target_user_id,
                    "old_role": expected.value,
                    "new_role": (UserRole.ADMIN if promote else UserRole.USER).value,
                },
            )
            return AdminMutationResult(status, _managed(target))
        assert last_concurrency_error is not None
        logger.warning(
            "Administrator role change could not acquire the database writer",
            extra={
                "action": "admin_role_change_stale",
                "actor_user_id": actor_user_id,
                "target_user_id": target_user_id,
                "code": AdminManagementErrorCode.TARGET_STATE_CHANGED.value,
            },
        )
        raise AdminManagementError(AdminManagementErrorCode.TARGET_STATE_CHANGED) from None

    async def _get_target(self, target_user_id: int) -> User:
        async with self._database.transaction() as repositories:
            target = await repositories.users.get(target_user_id)
        if target is None:
            raise AdminManagementError(AdminManagementErrorCode.TARGET_NOT_FOUND)
        return target

    def _protect_owner(self, target: User) -> None:
        if target.role is UserRole.OWNER or target.telegram_id == self._owner_id:
            logger.warning(
                "Administrator role target rejected",
                extra={
                    "action": "admin_target_rejected",
                    "target_user_id": target.id,
                    "code": AdminManagementErrorCode.TARGET_IS_OWNER.value,
                },
            )
            raise AdminManagementError(AdminManagementErrorCode.TARGET_IS_OWNER)

    def _required_owner_id(self) -> int:
        if self._owner_id is None:
            # No caller can pass centralized OWNER authorization in this configuration.
            raise AdminManagementError(AdminManagementErrorCode.TARGET_STATE_CHANGED)
        return self._owner_id


def _managed(user: User) -> ManagedUser:
    return ManagedUser(user.id, user.telegram_id, user.username, user.first_name)


def _page_bounds(requested_page: int, total_count: int) -> tuple[int, int]:
    total_pages = max(
        1, (total_count + ADMIN_MANAGEMENT_PAGE_SIZE - 1) // ADMIN_MANAGEMENT_PAGE_SIZE
    )
    return min(max(requested_page, 0), total_pages - 1), total_pages
