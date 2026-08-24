"""Thin aiogram router for the authorized, private-chat admin panel."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from aiogram import F, Router
from aiogram.dispatcher.event.bases import SkipHandler
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.types import User as AiogramUser

from app.core.enums import MusicProviderName
from app.core.provider_accounts import (
    ProviderAccountErrorCode,
    ProviderAuthorizationMethod,
    ProviderAuthorizationOutcome,
    ProviderAuthorizationOutcomeStatus,
    ProviderAuthorizationStartStatus,
    ProviderDisconnectOutcomeStatus,
    ProviderSensitiveInputChallenge,
    SensitiveValue,
)
from app.services.admin_management import (
    AdministratorManagementService,
    AdminManagementError,
    AdminManagementErrorCode,
    AdminMutationStatus,
)
from app.services.admin_overview import AdminOverviewError, AdminOverviewService
from app.services.authorization import AuthorizationError
from app.services.provider_accounts import ProviderAccountManagementService
from app.services.provider_health import ProviderHealthService
from app.services.runtime_worker_control import (
    RuntimeWorkerControlService,
    WorkerMutationResult,
    WorkerMutationStatus,
    WorkerPoolType,
)
from app.services.telegram_users import TelegramUserProfile, TelegramUserService
from app.storage.models import User
from app.telegram.admin_management_presentation import (
    AdminManagementCallbackAction,
    AdminManagementPresentation,
    parse_admin_management_callback,
)
from app.telegram.admin_presentation import (
    AdminCallbackAction,
    AdminPresentation,
    parse_admin_callback,
)
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    ProviderAccountsPresentation,
    parse_provider_accounts_callback,
)
from app.telegram.provider_authorization_ui import ProviderAuthorizationUiManager
from app.telegram.provider_health_presentation import (
    ProviderHealthCallbackAction,
    ProviderHealthPresentation,
    parse_provider_health_callback,
)
from app.telegram.worker_control_presentation import (
    WorkerCallbackAction,
    WorkerControlPresentation,
    parse_worker_callback,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AdminHandlerDependencies:
    users: TelegramUserService
    admin: AdminOverviewService
    presentation: AdminPresentation
    management: AdministratorManagementService
    management_presentation: AdminManagementPresentation
    worker_control: RuntimeWorkerControlService | None = None
    worker_presentation: WorkerControlPresentation | None = None
    provider_health: ProviderHealthService | None = None
    provider_health_presentation: ProviderHealthPresentation | None = None
    provider_accounts: ProviderAccountManagementService | None = None
    provider_accounts_presentation: ProviderAccountsPresentation | None = None
    provider_authorization_ui: ProviderAuthorizationUiManager | None = None


def create_admin_router(dependencies: AdminHandlerDependencies) -> Router:
    router = Router(name="stage10-admin-panel")
    last_sensitive_message_by_chat: dict[int, int] = {}
    expected_sensitive_flow_by_chat: dict[
        int, tuple[MusicProviderName, str, ProviderAuthorizationMethod]
    ] = {}

    @router.message(F.text & ~F.text.startswith("/"))
    async def deezer_sensitive_input(message: Message) -> None:
        """Consume only an authoritative OWNER's current private secret generation."""

        service = dependencies.provider_accounts
        presentation = dependencies.provider_accounts_presentation
        if (
            service is None
            or presentation is None
            or message.chat.type != ChatType.PRIVATE
            or message.from_user is None
            or message.forward_origin is not None
        ):
            raise SkipHandler
        try:
            user = await _observe_message(message, dependencies.users)
            if user is None:
                raise SkipHandler
            await service.authorize(user.id)
            expected = expected_sensitive_flow_by_chat.get(message.chat.id)
            if expected is not None:
                provider, expected_flow_id, method = expected
                challenge = await service.pending_sensitive_challenge(user.id, provider, method)
                if challenge is None or challenge.flow_id != expected_flow_id:
                    raise SkipHandler
            else:
                candidates = tuple(
                    candidate
                    for candidate in (
                        await service.pending_sensitive_challenge(
                            user.id,
                            MusicProviderName.DEEZER,
                            ProviderAuthorizationMethod.SENSITIVE_SECRET,
                        ),
                        await service.pending_sensitive_challenge(
                            user.id,
                            MusicProviderName.SPOTIFY,
                            ProviderAuthorizationMethod.COMPOUND_CREDENTIALS,
                        ),
                    )
                    if candidate is not None
                )
                if len(candidates) != 1:
                    raise SkipHandler
                challenge = candidates[0]
                provider = challenge.provider
                method = challenge.authorization_method
        except AuthorizationError:
            raise SkipHandler from None
        except SkipHandler:
            raise
        except Exception:
            raise SkipHandler from None
        if challenge is None:
            raise SkipHandler

        # Recheck the generation immediately before the destructive security gate.
        try:
            current = await service.pending_sensitive_challenge(user.id, provider, method)
        except Exception:
            raise SkipHandler from None
        if current is None or current.flow_id != challenge.flow_id:
            raise SkipHandler
        previous_message_id = last_sensitive_message_by_chat.get(message.chat.id, 0)
        if message.message_id <= previous_message_id:
            raise SkipHandler
        last_sensitive_message_by_chat[message.chat.id] = message.message_id
        locale = dependencies.users.locale_for(user)
        try:
            await message.delete()
        except Exception:
            try:
                await service.fail_sensitive_input(
                    user.id,
                    provider,
                    challenge.flow_id,
                    ProviderAccountErrorCode.SPOTIFY_WEBAPI_MESSAGE_DELETE_FAILED
                    if provider is MusicProviderName.SPOTIFY
                    else ProviderAccountErrorCode.DEEZER_AUTH_MESSAGE_DELETE_FAILED,
                )
            except Exception:
                pass
            try:
                await message.answer(
                    presentation.text(
                        "admin.spotify_webapi_auth_delete_failed"
                        if provider is MusicProviderName.SPOTIFY
                        else "admin.deezer_auth_delete_failed",
                        locale,
                    )
                )
            except Exception:
                pass
            expected_sensitive_flow_by_chat.pop(message.chat.id, None)
            return

        progress: Message | None = None
        try:
            progress = await message.answer(
                presentation.text(
                    "admin.spotify_webapi_auth_progress"
                    if provider is MusicProviderName.SPOTIFY
                    else "admin.deezer_auth_progress",
                    locale,
                )
            )
        except Exception:
            pass
        try:
            if provider is MusicProviderName.SPOTIFY:
                parsed_credentials = _parse_spotify_webapi_submission(message.text or "")
                if parsed_credentials is None:
                    outcome = await service.fail_sensitive_input(
                        user.id,
                        provider,
                        challenge.flow_id,
                        ProviderAccountErrorCode.SPOTIFY_WEBAPI_INVALID_FORMAT,
                    )
                else:
                    client_id = SensitiveValue(parsed_credentials[0])
                    client_secret = SensitiveValue(parsed_credentials[1])
                    outcome = await service.submit_compound_credentials(
                        user.id,
                        provider,
                        challenge.flow_id,
                        client_id,
                        client_secret,
                    )
                    del client_id, client_secret
            else:
                credential = SensitiveValue(message.text or " ")
                outcome = await service.submit_sensitive_secret(
                    user.id,
                    provider,
                    challenge.flow_id,
                    credential,
                )
                del credential
        except Exception:
            try:
                outcome = await service.fail_sensitive_input(
                    user.id,
                    provider,
                    challenge.flow_id,
                    ProviderAccountErrorCode.AUTHORIZATION_FAILED,
                )
            except Exception:
                outcome = ProviderAuthorizationOutcome(
                    provider,
                    ProviderAuthorizationOutcomeStatus.FAILED,
                    ProviderAccountErrorCode.AUTHORIZATION_FAILED,
                )
        if progress is not None:
            try:
                status = await service.get_status(user.id, provider)
                await progress.edit_text(
                    presentation.authorization_result_text(status, outcome, locale),
                    reply_markup=presentation.detail_keyboard(status, locale),
                )
            except Exception:
                pass
        current_expected = expected_sensitive_flow_by_chat.get(message.chat.id)
        if current_expected is not None and current_expected[1] == challenge.flow_id:
            expected_sensitive_flow_by_chat.pop(message.chat.id, None)

    @router.message(Command("admin"))
    async def admin_command(message: Message) -> None:
        user = await _observe_message(message, dependencies.users)
        if user is None:
            return
        locale = dependencies.users.locale_for(user)
        try:
            await dependencies.admin.authorize_view(user.id)
        except AuthorizationError:
            await message.answer(dependencies.presentation.text("admin.access_denied", locale))
            return
        if message.chat.type != ChatType.PRIVATE:
            await message.answer(dependencies.presentation.text("admin.private_chat_only", locale))
            return
        try:
            result = await dependencies.admin.get_overview(user.id)
        except AuthorizationError:
            await message.answer(dependencies.presentation.text("admin.access_denied", locale))
            return
        except AdminOverviewError:
            await message.answer(dependencies.presentation.text("admin.refresh_failed", locale))
            return
        await message.answer(
            dependencies.presentation.overview_text(result, locale),
            reply_markup=dependencies.presentation.keyboard(
                locale, authoritative_owner=result.access.is_authoritative_owner
            ),
        )

    @router.callback_query(F.data.startswith("adm1:"))
    async def admin_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        try:
            await dependencies.admin.authorize_view(user.id)
        except AuthorizationError:
            await _deny_callback(callback, dependencies.presentation, locale)
            return
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        action = parse_admin_callback(callback.data)
        if action is AdminCallbackAction.REFRESH:
            try:
                result = await dependencies.admin.get_overview(user.id)
            except AuthorizationError:
                await _deny_callback(callback, dependencies.presentation, locale)
                return
            except AdminOverviewError:
                await callback.answer(
                    dependencies.presentation.text("admin.refresh_failed", locale), show_alert=True
                )
                return
            await _edit_or_send(
                callback,
                dependencies.presentation.overview_text(result, locale),
                dependencies.presentation.keyboard(
                    locale, authoritative_owner=result.access.is_authoritative_owner
                ),
            )
            await callback.answer()
            return

        if action is AdminCallbackAction.CLOSE:
            await _close_panel(callback)
            await callback.answer()
            return
        await callback.answer(
            dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
        )

    @router.callback_query(F.data.startswith("adm2:"))
    async def admin_management_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        parsed = parse_admin_management_callback(callback.data)
        if parsed is None:
            try:
                await dependencies.management.authorize_owner(user.id)
            except AuthorizationError:
                await _deny_callback(callback, dependencies.presentation, locale)
                return
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return

        try:
            if parsed.action is AdminManagementCallbackAction.LIST:
                administrator_page = await dependencies.management.list_administrators(
                    user.id, page=parsed.page
                )
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.administrators_text(
                        administrator_page, locale
                    ),
                    dependencies.management_presentation.administrators_keyboard(
                        administrator_page, locale
                    ),
                )
                await callback.answer()
                return

            if parsed.action is AdminManagementCallbackAction.CANDIDATES:
                candidate_page = await dependencies.management.list_promotion_candidates(
                    user.id, page=parsed.page
                )
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.candidates_text(candidate_page, locale),
                    dependencies.management_presentation.candidates_keyboard(
                        candidate_page, locale
                    ),
                )
                await callback.answer()
                return

            target_user_id = parsed.target_user_id
            if target_user_id is None:
                await dependencies.management.authorize_owner(user.id)
                await callback.answer(
                    dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
                )
                return

            if parsed.action is AdminManagementCallbackAction.ADMINISTRATOR:
                target = await dependencies.management.get_administrator(user.id, target_user_id)
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.administrator_text(target, locale),
                    dependencies.management_presentation.administrator_keyboard(
                        target, page=parsed.page, locale=locale
                    ),
                )
                await callback.answer()
                return

            if parsed.action is AdminManagementCallbackAction.PROMOTE_CONFIRMATION:
                target = await dependencies.management.get_promotion_candidate(
                    user.id, target_user_id
                )
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.promotion_confirmation_text(
                        target, locale
                    ),
                    dependencies.management_presentation.promotion_confirmation_keyboard(
                        target, page=parsed.page, locale=locale
                    ),
                )
                await callback.answer()
                return

            if parsed.action is AdminManagementCallbackAction.REMOVE_CONFIRMATION:
                target = await dependencies.management.get_administrator(user.id, target_user_id)
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.removal_confirmation_text(target, locale),
                    dependencies.management_presentation.removal_confirmation_keyboard(
                        target, page=parsed.page, locale=locale
                    ),
                )
                await callback.answer()
                return

            if parsed.action is AdminManagementCallbackAction.PROMOTE_COMMIT:
                result = await dependencies.management.promote_to_admin(user.id, target_user_id)
                updated_page = await dependencies.management.list_administrators(user.id, page=0)
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.administrators_text(updated_page, locale),
                    dependencies.management_presentation.administrators_keyboard(
                        updated_page, locale
                    ),
                )
                key = (
                    "admin.promote_success"
                    if result.status is AdminMutationStatus.PROMOTED
                    else "admin.target_stale"
                )
                await callback.answer(
                    dependencies.management_presentation.text(key, locale), show_alert=True
                )
                return

            if parsed.action is AdminManagementCallbackAction.REMOVE_COMMIT:
                result = await dependencies.management.demote_admin(user.id, target_user_id)
                updated_page = await dependencies.management.list_administrators(
                    user.id, page=parsed.page
                )
                await _edit_or_send(
                    callback,
                    dependencies.management_presentation.administrators_text(updated_page, locale),
                    dependencies.management_presentation.administrators_keyboard(
                        updated_page, locale
                    ),
                )
                key = (
                    "admin.remove_success"
                    if result.status is AdminMutationStatus.DEMOTED
                    else "admin.target_stale"
                )
                await callback.answer(
                    dependencies.management_presentation.text(key, locale), show_alert=True
                )
                return
        except AuthorizationError:
            await _deny_callback(callback, dependencies.presentation, locale)
            return
        except AdminManagementError as exc:
            await callback.answer(
                dependencies.management_presentation.text(_management_error_key(exc.code), locale),
                show_alert=True,
            )
            return

        await callback.answer(
            dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
        )

    @router.callback_query(F.data.startswith("adm3:"))
    async def worker_control_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        service = dependencies.worker_control
        presentation = dependencies.worker_presentation
        if service is None or presentation is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return

        # Authorize even malformed/private-chat callbacks; callback shape and navigation history
        # never establish authority.
        try:
            await service.authorize(user.id)
        except AuthorizationError:
            await _deny_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.workers_access_denied",
            )
            return
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        parsed = parse_worker_callback(callback.data)
        if parsed is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return

        try:
            if parsed.action is WorkerCallbackAction.BACK:
                result = await dependencies.admin.get_overview(user.id)
                await _edit_or_send(
                    callback,
                    dependencies.presentation.overview_text(result, locale),
                    dependencies.presentation.keyboard(
                        locale, authoritative_owner=result.access.is_authoritative_owner
                    ),
                )
                await callback.answer()
                return

            if parsed.action is WorkerCallbackAction.OVERVIEW:
                snapshot = await service.get_snapshot(user.id)
                await _edit_or_send(
                    callback,
                    presentation.overview_text(snapshot, locale),
                    presentation.overview_keyboard(locale),
                )
                await callback.answer()
                return

            pool = parsed.pool
            if pool is None:
                await callback.answer(
                    dependencies.presentation.text("admin.invalid_action", locale),
                    show_alert=True,
                )
                return

            if parsed.action is WorkerCallbackAction.DETAIL:
                snapshot = await service.get_snapshot(user.id)
                await _edit_or_send(
                    callback,
                    presentation.detail_text(pool, snapshot, locale),
                    presentation.detail_keyboard(pool, locale),
                )
                await callback.answer()
                return

            if parsed.action is WorkerCallbackAction.INCREASE:
                mutation = await _adjust_worker(service, user.id, pool, 1)
            elif parsed.action is WorkerCallbackAction.DECREASE:
                mutation = await _adjust_worker(service, user.id, pool, -1)
            elif parsed.action is WorkerCallbackAction.RESET:
                mutation = await _reset_worker(service, user.id, pool)
            else:
                await callback.answer(
                    dependencies.presentation.text("admin.invalid_action", locale),
                    show_alert=True,
                )
                return

            await _edit_or_send(
                callback,
                presentation.detail_text(pool, mutation.snapshot, locale),
                presentation.detail_keyboard(pool, locale),
            )
            await callback.answer(
                presentation.text(_worker_status_key(mutation.status), locale),
                show_alert=mutation.status
                in {
                    WorkerMutationStatus.MINIMUM_REACHED,
                    WorkerMutationStatus.MAXIMUM_REACHED,
                },
            )
        except AuthorizationError:
            await _deny_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.workers_access_denied",
            )
        except AdminOverviewError:
            await callback.answer(
                dependencies.presentation.text("admin.refresh_failed", locale), show_alert=True
            )
        except Exception:
            logger.exception(
                "Worker-control callback failed",
                extra={"action": "worker_control", "user_id": user.id},
            )
            await callback.answer(
                presentation.text("admin.workers_update_failed", locale), show_alert=True
            )

    @router.callback_query(F.data.startswith("adm4:"))
    async def provider_health_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        service = dependencies.provider_health
        presentation = dependencies.provider_health_presentation
        if service is None or presentation is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return

        # Authorization precedes parsing and every navigation operation.
        try:
            await service.authorize(user.id)
        except AuthorizationError:
            await _deny_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.provider_health_access_denied",
            )
            return
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        action = parse_provider_health_callback(callback.data)
        if action is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return
        if action is ProviderHealthCallbackAction.BACK:
            try:
                result = await dependencies.admin.get_overview(user.id)
                await _edit_or_send(
                    callback,
                    dependencies.presentation.overview_text(result, locale),
                    dependencies.presentation.keyboard(
                        locale, authoritative_owner=result.access.is_authoritative_owner
                    ),
                )
                await callback.answer()
            except AuthorizationError:
                await _deny_callback(
                    callback,
                    dependencies.presentation,
                    locale,
                    key="admin.provider_health_access_denied",
                )
            except AdminOverviewError:
                await callback.answer(
                    dependencies.presentation.text("admin.refresh_failed", locale), show_alert=True
                )
            return

        # Acknowledge Telegram before the serialized provider operation starts.
        await callback.answer()
        await _edit_or_send(
            callback,
            presentation.checking_text(locale),
            presentation.keyboard(locale),
        )
        try:
            snapshot = await service.check_all(user.id)
            await _edit_or_send(
                callback,
                presentation.snapshot_text(snapshot, locale),
                presentation.keyboard(locale),
            )
        except AuthorizationError:
            await _deny_answered_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.provider_health_access_denied",
            )
        except Exception:
            logger.exception(
                "Provider Health callback failed",
                extra={"action": "provider_health", "user_id": user.id},
            )
            await _edit_or_send(
                callback,
                presentation.text("admin.provider_health_check_failed", locale),
                presentation.keyboard(locale),
            )

    @router.callback_query(F.data.startswith("adm5:"))
    async def provider_accounts_callback(callback: CallbackQuery) -> None:
        user = await _observe_callback(callback, dependencies.users)
        locale = dependencies.users.locale_for(user)
        service = dependencies.provider_accounts
        presentation = dependencies.provider_accounts_presentation
        if service is None or presentation is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return

        # Authority is established before callback parsing or any account operation.
        try:
            await service.authorize(user.id)
        except AuthorizationError:
            await _deny_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.provider_accounts_access_denied",
            )
            return
        if not isinstance(callback.message, Message) or (
            callback.message.chat.type != ChatType.PRIVATE
        ):
            await callback.answer(
                dependencies.presentation.text("admin.private_chat_only", locale), show_alert=True
            )
            return

        parsed = parse_provider_accounts_callback(callback.data)
        if parsed is None:
            await callback.answer(
                dependencies.presentation.text("admin.invalid_action", locale), show_alert=True
            )
            return
        try:
            if parsed.action is ProviderAccountsCallbackAction.BACK:
                result = await dependencies.admin.get_overview(user.id)
                await _edit_or_send(
                    callback,
                    dependencies.presentation.overview_text(result, locale),
                    dependencies.presentation.keyboard(
                        locale, authoritative_owner=result.access.is_authoritative_owner
                    ),
                )
            elif parsed.action is ProviderAccountsCallbackAction.OPEN:
                overview = await service.get_overview(user.id)
                await _edit_or_send(
                    callback,
                    presentation.overview_text(overview, locale),
                    presentation.overview_keyboard(overview, locale),
                )
            elif parsed.action is ProviderAccountsCallbackAction.DETAIL:
                if parsed.provider is None:
                    raise ValueError("provider is required")
                status = await service.get_status(user.id, parsed.provider)
                await _edit_or_send(
                    callback,
                    presentation.detail_text(status, locale),
                    presentation.detail_keyboard(status, locale),
                )
            elif parsed.action is ProviderAccountsCallbackAction.RESET:
                if parsed.provider is None:
                    raise ValueError("provider is required")
                status = await service.get_status(user.id, parsed.provider)
                if not status.disconnect_supported:
                    raise ValueError("provider reset is unsupported")
                await _edit_or_send(
                    callback,
                    presentation.reset_confirmation_text(parsed.provider, locale),
                    presentation.reset_confirmation_keyboard(parsed.provider, locale),
                )
            elif parsed.action is ProviderAccountsCallbackAction.CONFIRM_RESET:
                if parsed.provider is None:
                    raise ValueError("provider is required")
                reset_outcome = await service.disconnect(user.id, parsed.provider)
                status = await service.get_status(user.id, parsed.provider)
                await _edit_or_send(
                    callback,
                    presentation.detail_text(status, locale),
                    presentation.detail_keyboard(status, locale),
                )
                await callback.answer(
                    presentation.text(
                        "admin.provider_accounts_reset_done"
                        if reset_outcome.status is ProviderDisconnectOutcomeStatus.DISCONNECTED
                        else "admin.provider_accounts_reset_failed",
                        locale,
                    ),
                    show_alert=True,
                )
                return
            elif parsed.action in {
                ProviderAccountsCallbackAction.CONNECT,
                ProviderAccountsCallbackAction.CONNECT_PLAYBACK,
                ProviderAccountsCallbackAction.CONFIGURE_WEB_API,
            }:
                if parsed.provider is None:
                    raise ValueError("provider is required")
                method = (
                    ProviderAuthorizationMethod.BROWSER_DEVICE_LINK
                    if parsed.action is ProviderAccountsCallbackAction.CONNECT_PLAYBACK
                    else ProviderAuthorizationMethod.COMPOUND_CREDENTIALS
                    if parsed.action is ProviderAccountsCallbackAction.CONFIGURE_WEB_API
                    else None
                )
                if (
                    parsed.action
                    in {
                        ProviderAccountsCallbackAction.CONNECT_PLAYBACK,
                        ProviderAccountsCallbackAction.CONFIGURE_WEB_API,
                    }
                    and parsed.provider is not MusicProviderName.SPOTIFY
                ):
                    raise ValueError("Spotify action requires Spotify provider")
                started = await service.start_authorization(user.id, parsed.provider, method)
                if (
                    started.status is ProviderAuthorizationStartStatus.STARTED
                    and started.challenge is not None
                ):
                    await _edit_or_send(
                        callback,
                        presentation.authorization_text(started.challenge, locale),
                        presentation.authorization_keyboard(started.challenge, locale),
                    )
                    if isinstance(started.challenge, ProviderSensitiveInputChallenge):
                        expected_sensitive_flow_by_chat[callback.message.chat.id] = (
                            parsed.provider,
                            started.challenge.flow_id,
                            started.challenge.authorization_method,
                        )
                    if dependencies.provider_authorization_ui is not None:
                        dependencies.provider_authorization_ui.watch(
                            callback.message,
                            actor_user_id=user.id,
                            provider=parsed.provider,
                            flow_id=started.challenge.flow_id,
                            locale=locale,
                        )
                else:
                    status = await service.get_status(user.id, parsed.provider)
                    await _edit_or_send(
                        callback,
                        presentation.detail_text(status, locale),
                        presentation.detail_keyboard(status, locale),
                    )
                    if started.status is ProviderAuthorizationStartStatus.FAILED:
                        await callback.answer(
                            presentation.text(
                                "admin.deezer_auth_failed"
                                if parsed.provider is MusicProviderName.DEEZER
                                else "admin.spotify_auth_failed"
                                if parsed.provider is MusicProviderName.SPOTIFY
                                else "admin.tidal_auth_failed",
                                locale,
                            ),
                            show_alert=True,
                        )
                        return
            elif parsed.action is ProviderAccountsCallbackAction.CANCEL:
                if parsed.provider is None or parsed.flow_id is None:
                    raise ValueError("provider and flow are required")
                outcome = await service.cancel_authorization(
                    user.id, parsed.provider, parsed.flow_id
                )
                expected = expected_sensitive_flow_by_chat.get(callback.message.chat.id)
                if expected is not None and expected[1] == parsed.flow_id:
                    expected_sensitive_flow_by_chat.pop(callback.message.chat.id, None)
                status = await service.get_status(user.id, parsed.provider)
                await _edit_or_send(
                    callback,
                    presentation.authorization_result_text(status, outcome, locale),
                    presentation.detail_keyboard(status, locale),
                )
            elif parsed.provider is None:
                overview = await service.refresh(user.id)
                await _edit_or_send(
                    callback,
                    presentation.overview_text(overview, locale),
                    presentation.overview_keyboard(overview, locale),
                )
            else:
                status = await service.refresh_status(user.id, parsed.provider)
                await _edit_or_send(
                    callback,
                    presentation.detail_text(status, locale),
                    presentation.detail_keyboard(status, locale),
                )
            await callback.answer()
        except AuthorizationError:
            await _deny_callback(
                callback,
                dependencies.presentation,
                locale,
                key="admin.provider_accounts_access_denied",
            )
        except Exception:
            logger.error(
                "Provider account callback failed",
                extra={"action": "provider_accounts", "user_id": user.id},
            )
            await callback.answer(
                presentation.text("admin.provider_accounts_failed", locale), show_alert=True
            )

    return router


def _parse_spotify_webapi_submission(value: str) -> tuple[str, str] | None:
    """Accept exactly two bounded lines without ever embedding them in an error."""

    if not value or len(value) > 2 * 1024 + 2:
        return None
    normalized = value.replace("\r\n", "\n")
    if "\r" in normalized:
        return None
    parts = normalized.split("\n")
    if len(parts) != 2:
        return None
    client_id, client_secret = (part.strip(" ") for part in parts)
    if not client_id or not client_secret:
        return None
    if len(client_id) > 1024 or len(client_secret) > 1024:
        return None
    if any(
        ord(character) < 32 or ord(character) == 127
        for field in (client_id, client_secret)
        for character in field
    ):
        return None
    return client_id, client_secret


async def _adjust_worker(
    service: RuntimeWorkerControlService,
    user_id: int,
    pool: WorkerPoolType,
    delta: int,
) -> WorkerMutationResult:
    if pool is WorkerPoolType.DOWNLOAD:
        return await service.adjust_download_workers(user_id, delta)
    return await service.adjust_upload_workers(user_id, delta)


async def _reset_worker(
    service: RuntimeWorkerControlService, user_id: int, pool: WorkerPoolType
) -> WorkerMutationResult:
    if pool is WorkerPoolType.DOWNLOAD:
        return await service.reset_download_workers(user_id)
    return await service.reset_upload_workers(user_id)


def _worker_status_key(status: WorkerMutationStatus) -> str:
    if status is WorkerMutationStatus.MINIMUM_REACHED:
        return "admin.workers_minimum_reached"
    if status is WorkerMutationStatus.MAXIMUM_REACHED:
        return "admin.workers_maximum_reached"
    return "admin.workers_updated"


def _management_error_key(code: AdminManagementErrorCode) -> str:
    if code is AdminManagementErrorCode.TARGET_NOT_FOUND:
        return "admin.target_not_found"
    if code is AdminManagementErrorCode.TARGET_IS_OWNER:
        return "admin.target_owner_forbidden"
    return "admin.target_stale"


async def _deny_callback(
    callback: CallbackQuery,
    presentation: AdminPresentation,
    locale: str,
    *,
    key: str = "admin.access_denied",
) -> None:
    try:
        if isinstance(callback.message, Message):
            await callback.message.edit_reply_markup(reply_markup=None)
    except TelegramAPIError:
        pass
    await callback.answer(presentation.text(key, locale), show_alert=True)


async def _deny_answered_callback(
    callback: CallbackQuery,
    presentation: AdminPresentation,
    locale: str,
    *,
    key: str,
) -> None:
    """Render a denial after the callback was already acknowledged before network work."""

    if not isinstance(callback.message, Message):
        return
    text = presentation.text(key, locale)
    try:
        await callback.message.edit_text(text, reply_markup=None)
    except TelegramAPIError:
        try:
            await callback.message.answer(text)
        except TelegramAPIError:
            pass


async def _close_panel(callback: CallbackQuery) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.delete()
    except TelegramAPIError:
        try:
            await callback.message.edit_reply_markup(reply_markup=None)
        except TelegramAPIError:
            pass


async def _edit_or_send(
    callback: CallbackQuery, text: str, reply_markup: InlineKeyboardMarkup
) -> None:
    if not isinstance(callback.message, Message):
        return
    try:
        await callback.message.edit_text(text, reply_markup=reply_markup)
    except TelegramAPIError:
        await callback.message.answer(text, reply_markup=reply_markup)


async def _observe_message(message: Message, service: TelegramUserService) -> User | None:
    if message.from_user is None:
        return None
    return await service.observe(_profile(message.from_user))


async def _observe_callback(callback: CallbackQuery, service: TelegramUserService) -> User:
    return await service.observe(_profile(callback.from_user))


def _profile(user: AiogramUser) -> TelegramUserProfile:
    return TelegramUserProfile(user.id, user.username, user.first_name, user.language_code)
