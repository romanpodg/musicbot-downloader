from __future__ import annotations

from datetime import UTC, datetime

from app.core.enums import UserRole
from app.core.models import (
    QueueRuntimeSnapshot,
    QueueStatusCounts,
    SingleFlightSnapshot,
    SubscriberStatusCounts,
    TelegramCacheStats,
    WorkerPoolSnapshot,
)
from app.i18n import LocalizationService
from app.services.admin_overview import (
    AdminOverview,
    AlbumOverview,
    AuthorizedAdminOverview,
    DeliveryOverview,
)
from app.services.authorization import (
    AdminAccessContext,
    AdminPermission,
)
from app.telegram.admin_presentation import (
    AdminCallbackAction,
    AdminPresentation,
    encode_admin_callback,
    parse_admin_callback,
)


def _result(*, owner: bool, value: int) -> AuthorizedAdminOverview:
    role = UserRole.OWNER if owner else UserRole.ADMIN
    permissions = {AdminPermission.ADMIN_PANEL_VIEW}
    if owner:
        permissions.add(AdminPermission.OWNER_ONLY)
    return AuthorizedAdminOverview(
        access=AdminAccessContext(
            user_id=1,
            telegram_id=2,
            effective_role=role,
            permissions=frozenset(permissions),
            is_authoritative_owner=owner,
        ),
        overview=AdminOverview(
            generated_at=datetime.now(UTC),
            queues=QueueRuntimeSnapshot(
                download=WorkerPoolSnapshot(value, value - 1 if value else 0, 2, 8),
                upload=WorkerPoolSnapshot(value, value, 3, 10),
                download_jobs=QueueStatusCounts(queued=value, running=value, failed=value),
                upload_jobs=QueueStatusCounts(queued=value, running=value, failed=value),
                singleflight=SingleFlightSnapshot(value, SubscriberStatusCounts(waiting=value)),
            ),
            telegram_cache=TelegramCacheStats(value, value, value),
            deliveries=DeliveryOverview(value, value, value),
            albums=AlbumOverview(value),
        ),
    )


def test_admin_presentation_renders_zero_normal_and_large_values_in_both_locales() -> None:
    presentation = AdminPresentation(LocalizationService(("en", "ru"), "en"))

    for locale in ("en", "ru"):
        for value in (0, 12, 4_231_999):
            text = presentation.overview_text(_result(owner=False, value=value), locale)
            assert len(text) < 4096
            assert f"{value:,}" in text
            assert "BOT_TOKEN" not in text
            assert "file_id" not in text

    assert "Role: Administrator" in presentation.overview_text(_result(owner=False, value=12), "en")
    assert "Role: Owner" in presentation.overview_text(_result(owner=True, value=12), "en")
    keyboard = presentation.keyboard("en")
    callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
    assert callbacks == ["adm3:w", "adm4:h", "adm1:refresh", "adm1:close"]
    assert all(callback is not None and len(callback.encode()) <= 64 for callback in callbacks)

    owner_keyboard = presentation.keyboard("en", authoritative_owner=True)
    owner_callbacks = [
        button.callback_data for row in owner_keyboard.inline_keyboard for button in row
    ]
    assert owner_callbacks == [
        "adm2:l:0",
        "adm3:w",
        "adm4:h",
        "adm1:refresh",
        "adm1:close",
    ]


def test_admin_callback_codec_rejects_malformed_and_unknown_values() -> None:
    assert (
        parse_admin_callback(encode_admin_callback(AdminCallbackAction.REFRESH))
        is AdminCallbackAction.REFRESH
    )
    assert parse_admin_callback("adm1:") is None
    assert parse_admin_callback("adm1:unknown") is None
    assert parse_admin_callback("garbage") is None
    assert parse_admin_callback(None) is None
