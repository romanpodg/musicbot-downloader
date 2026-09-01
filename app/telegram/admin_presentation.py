"""Localized read-only admin dashboard presentation and callback codec."""

from __future__ import annotations

from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.i18n import LocalizationService
from app.services.admin_overview import AuthorizedAdminOverview
from app.services.system_diagnostics import JobDiagnostic, SystemDiagnostic
from app.telegram.admin_management_presentation import (
    AdminManagementCallbackAction,
    encode_admin_management_callback,
)
from app.telegram.provider_accounts_presentation import (
    ProviderAccountsCallbackAction,
    encode_provider_accounts_callback,
)
from app.telegram.provider_health_presentation import (
    ProviderHealthCallbackAction,
    encode_provider_health_callback,
)
from app.telegram.worker_control_presentation import (
    WorkerCallbackAction,
    encode_worker_callback,
)


class AdminCallbackAction(StrEnum):
    REFRESH = "refresh"
    CLOSE = "close"


def encode_admin_callback(action: AdminCallbackAction) -> str:
    return f"adm1:{action.value}"


def parse_admin_callback(value: str | None) -> AdminCallbackAction | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) != 2 or parts[0] != "adm1":
        return None
    try:
        return AdminCallbackAction(parts[1])
    except ValueError:
        return None


class AdminPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def overview_text(self, result: AuthorizedAdminOverview, locale: str) -> str:
        overview = result.overview
        queues = overview.queues
        singleflight = queues.singleflight
        role_key = (
            "admin.role_owner"
            if result.access.is_authoritative_owner
            else "admin.role_administrator"
        )
        sections = [
            self.text("admin.title", locale),
            self.text(
                "admin.role",
                locale,
                role=self.text(role_key, locale),
            ),
            "\n".join(
                (
                    self.text("admin.queues", locale),
                    self.text(
                        "admin.queue_download",
                        locale,
                        queued=_count(queues.download_jobs.queued),
                        running=_count(queues.download_jobs.running),
                        failed=_count(queues.download_jobs.failed),
                    ),
                    self.text(
                        "admin.queue_upload",
                        locale,
                        queued=_count(queues.upload_jobs.queued),
                        running=_count(queues.upload_jobs.running),
                        failed=_count(queues.upload_jobs.failed),
                    ),
                )
            ),
            "\n".join(
                (
                    self.text("admin.workers", locale),
                    self.text(
                        "admin.worker_download",
                        locale,
                        desired=_count(queues.download.desired_workers),
                        actual=_count(queues.download.actual_workers),
                        default=_count(queues.download.default_workers),
                        maximum=_count(queues.download.max_workers),
                    ),
                    self.text(
                        "admin.worker_upload",
                        locale,
                        desired=_count(queues.upload.desired_workers),
                        actual=_count(queues.upload.actual_workers),
                        default=_count(queues.upload.default_workers),
                        maximum=_count(queues.upload.max_workers),
                    ),
                )
            ),
            "\n".join(
                (
                    self.text("admin.singleflight", locale),
                    self.text(
                        "admin.singleflight_values",
                        locale,
                        active=_count(singleflight.active_flights if singleflight else 0),
                        waiting=_count(singleflight.subscribers.waiting if singleflight else 0),
                    ),
                )
            ),
            "\n".join(
                (
                    self.text("admin.cache", locale),
                    self.text(
                        "admin.cache_values",
                        locale,
                        active=_count(overview.telegram_cache.active_entries),
                        invalid=_count(overview.telegram_cache.invalid_entries),
                    ),
                )
            ),
            "\n".join(
                (
                    self.text("admin.deliveries", locale),
                    self.text(
                        "admin.delivery_values",
                        locale,
                        waiting=_count(overview.deliveries.waiting_or_queued),
                        sending=_count(overview.deliveries.sending),
                        failed=_count(overview.deliveries.failed),
                    ),
                )
            ),
            "\n".join(
                (
                    self.text("admin.albums", locale),
                    self.text(
                        "admin.album_values",
                        locale,
                        active=_count(overview.albums.active),
                    ),
                )
            ),
        ]
        rendered = "\n\n".join(sections)
        if len(rendered) > 4096:
            raise ValueError("admin overview exceeds Telegram message limit")
        return rendered

    def keyboard(self, locale: str, *, authoritative_owner: bool = False) -> InlineKeyboardMarkup:
        rows: list[list[InlineKeyboardButton]] = []
        if authoritative_owner:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self.text("admin.administrators", locale),
                        callback_data=encode_admin_management_callback(
                            AdminManagementCallbackAction.LIST, page=0
                        ),
                    )
                ]
            )
            rows.append(
                [
                    InlineKeyboardButton(
                        text=self.text("admin.provider_accounts", locale),
                        callback_data=encode_provider_accounts_callback(
                            ProviderAccountsCallbackAction.OPEN
                        ),
                    )
                ]
            )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("admin.workers", locale),
                    callback_data=encode_worker_callback(WorkerCallbackAction.OVERVIEW),
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    text=self.text("admin.provider_health", locale),
                    callback_data=encode_provider_health_callback(
                        ProviderHealthCallbackAction.OPEN
                    ),
                )
            ]
        )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text=self.text("admin.refresh", locale),
                        callback_data=encode_admin_callback(AdminCallbackAction.REFRESH),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.close", locale),
                        callback_data=encode_admin_callback(AdminCallbackAction.CLOSE),
                    )
                ],
            ]
        )
        return InlineKeyboardMarkup(inline_keyboard=rows)

    def system_text(self, diagnostic: SystemDiagnostic, locale: str) -> str:
        q = diagnostic.queues
        s = diagnostic.storage
        rendered = (
            f"System\n\nQueue\n"
            f"Download: {q.download_jobs.running} running / {q.download_jobs.queued} queued\n"
            f"Upload: {q.upload_jobs.running} running / {q.upload_jobs.queued} queued\n\n"
            f"Workers\nDownload: {q.download.actual_workers} / {q.download.desired_workers}\n"
            f"Upload: {q.upload.actual_workers} / {q.upload.desired_workers}\n\n"
            f"Storage\nUsed: {s.used_bytes} bytes\n"
            f"Free: {s.free_bytes} bytes\nPressure: {s.pressure}\n\n"
            f"Recovery\nExpired/stuck: {diagnostic.expired_claims}\n"
            f"Failed (1h): {diagnostic.recent_failures}"
        )
        if len(rendered) > 4096:
            raise ValueError("system diagnostic exceeds Telegram message limit")
        return rendered

    def job_text(self, diagnostic: JobDiagnostic, locale: str) -> str:
        lines = [
            f"Job {diagnostic.job_id}",
            f"Status: {diagnostic.status}",
            f"Attempts: {diagnostic.attempt_count}",
        ]
        if diagnostic.last_error_code:
            lines.append(f"Error: {diagnostic.last_error_code}")
        for item in diagnostic.provider_attempts[:20]:
            lines.append(
                f"Provider attempt: {item.get('status')}"
                + (f" ({item.get('failure_code')})" if item.get("failure_code") else "")
            )
        return "\n".join(lines)[:4096]


def _count(value: int) -> str:
    return f"{value:,}"
