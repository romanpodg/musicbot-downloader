"""Localized Stage 10.3 worker-control screens and strict callback codec."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.models import WorkerPoolSnapshot
from app.i18n import LocalizationService
from app.services.runtime_worker_control import WorkerControlSnapshot, WorkerPoolType

CALLBACK_PREFIX = "adm3"
CALLBACK_LIMIT_BYTES = 64


class WorkerCallbackAction(StrEnum):
    OVERVIEW = "overview"
    DETAIL = "detail"
    INCREASE = "increase"
    DECREASE = "decrease"
    RESET = "reset"
    BACK = "back"


@dataclass(frozen=True, slots=True)
class WorkerCallback:
    action: WorkerCallbackAction
    pool: WorkerPoolType | None = None


_POOL_CODES = {WorkerPoolType.DOWNLOAD: "d", WorkerPoolType.UPLOAD: "u"}
_CODE_POOLS = {code: pool for pool, code in _POOL_CODES.items()}
_OPERATION_CODES = {
    WorkerCallbackAction.INCREASE: "+",
    WorkerCallbackAction.DECREASE: "-",
    WorkerCallbackAction.RESET: "r",
}
_CODE_OPERATIONS = {code: action for action, code in _OPERATION_CODES.items()}


def encode_worker_callback(action: WorkerCallbackAction, pool: WorkerPoolType | None = None) -> str:
    if action is WorkerCallbackAction.OVERVIEW and pool is None:
        return f"{CALLBACK_PREFIX}:w"
    if action is WorkerCallbackAction.BACK and pool is None:
        return f"{CALLBACK_PREFIX}:b"
    if pool is None:
        raise ValueError("worker pool is required")
    pool_code = _POOL_CODES[pool]
    if action is WorkerCallbackAction.DETAIL:
        return f"{CALLBACK_PREFIX}:{pool_code}"
    operation = _OPERATION_CODES.get(action)
    if operation is None:
        raise ValueError("invalid worker callback action")
    return f"{CALLBACK_PREFIX}:{pool_code}:{operation}"


def parse_worker_callback(value: str | None) -> WorkerCallback | None:
    if not value or len(value.encode()) > CALLBACK_LIMIT_BYTES:
        return None
    parts = value.split(":")
    if parts == [CALLBACK_PREFIX, "w"]:
        return WorkerCallback(WorkerCallbackAction.OVERVIEW)
    if parts == [CALLBACK_PREFIX, "b"]:
        return WorkerCallback(WorkerCallbackAction.BACK)
    if len(parts) == 2 and parts[0] == CALLBACK_PREFIX:
        pool = _CODE_POOLS.get(parts[1])
        return WorkerCallback(WorkerCallbackAction.DETAIL, pool) if pool else None
    if len(parts) != 3 or parts[0] != CALLBACK_PREFIX:
        return None
    pool = _CODE_POOLS.get(parts[1])
    action = _CODE_OPERATIONS.get(parts[2])
    if pool is None or action is None:
        return None
    return WorkerCallback(action, pool)


class WorkerControlPresentation:
    def __init__(self, i18n: LocalizationService) -> None:
        self.i18n = i18n

    def text(self, key: str, locale: str, **values: object) -> str:
        return self.i18n.translate(key, locale, **values)

    def overview_text(self, snapshot: WorkerControlSnapshot, locale: str) -> str:
        rendered = "\n\n".join(
            (
                self.text("admin.workers_title", locale),
                self._pool_text(WorkerPoolType.DOWNLOAD, snapshot.download, locale),
                self._pool_text(WorkerPoolType.UPLOAD, snapshot.upload, locale),
                self.text("admin.workers_runtime_hint", locale),
                self.text("admin.workers_downscale_hint", locale),
            )
        )
        self._validate_message(rendered)
        return rendered

    def detail_text(
        self, pool: WorkerPoolType, snapshot: WorkerControlSnapshot, locale: str
    ) -> str:
        values = snapshot.download if pool is WorkerPoolType.DOWNLOAD else snapshot.upload
        title_key = (
            "admin.workers_download_title"
            if pool is WorkerPoolType.DOWNLOAD
            else "admin.workers_upload_title"
        )
        rendered = "\n".join(
            (
                self.text(title_key, locale),
                "",
                self.text("admin.workers_desired", locale, value=_count(values.desired_workers)),
                self.text("admin.workers_actual", locale, value=_count(values.actual_workers)),
                self.text("admin.workers_default", locale, value=_count(values.default_workers)),
                self.text("admin.workers_maximum", locale, value=_count(values.max_workers)),
                "",
                self.text("admin.workers_downscale_hint", locale),
            )
        )
        self._validate_message(rendered)
        return rendered

    def overview_keyboard(self, locale: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_download", locale),
                        callback_data=encode_worker_callback(
                            WorkerCallbackAction.DETAIL, WorkerPoolType.DOWNLOAD
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_upload", locale),
                        callback_data=encode_worker_callback(
                            WorkerCallbackAction.DETAIL, WorkerPoolType.UPLOAD
                        ),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_refresh", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.OVERVIEW),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_back", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.BACK),
                    )
                ],
            ]
        )

    def detail_keyboard(self, pool: WorkerPoolType, locale: str) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_decrease", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.DECREASE, pool),
                    ),
                    InlineKeyboardButton(
                        text=self.text("admin.workers_increase", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.INCREASE, pool),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_reset_default", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.RESET, pool),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_refresh", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.DETAIL, pool),
                    )
                ],
                [
                    InlineKeyboardButton(
                        text=self.text("admin.workers_back", locale),
                        callback_data=encode_worker_callback(WorkerCallbackAction.OVERVIEW),
                    )
                ],
            ]
        )

    def _pool_text(self, pool: WorkerPoolType, values: WorkerPoolSnapshot, locale: str) -> str:
        title_key = (
            "admin.workers_download" if pool is WorkerPoolType.DOWNLOAD else "admin.workers_upload"
        )
        return "\n".join(
            (
                self.text(title_key, locale),
                self.text("admin.workers_desired", locale, value=_count(values.desired_workers)),
                self.text("admin.workers_actual", locale, value=_count(values.actual_workers)),
                self.text("admin.workers_default", locale, value=_count(values.default_workers)),
                self.text("admin.workers_maximum", locale, value=_count(values.max_workers)),
            )
        )

    @staticmethod
    def _validate_message(rendered: str) -> None:
        if len(rendered) > 4096:
            raise ValueError("worker-control message exceeds Telegram limit")


def _count(value: int) -> str:
    return f"{value:,}"
