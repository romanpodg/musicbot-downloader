"""Strict opaque callback codec for Stage 18 download confirmation."""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

_TOKEN = re.compile(r"^[a-f0-9]{24}$")


class DownloadCallbackAction(StrEnum):
    CONFIRM = "confirm"
    CANCEL = "cancel"
    SELECT = "select"
    EXPAND = "expand"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class DownloadCallback:
    action: DownloadCallbackAction
    token: str
    alternative_index: int | None = None


def encode_download_callback(
    action: DownloadCallbackAction, token: str, alternative_index: int | None = None
) -> str:
    if not _TOKEN.fullmatch(token):
        raise ValueError("invalid opaque download confirmation token")
    if action is DownloadCallbackAction.SELECT:
        if alternative_index is None or not 0 <= alternative_index <= 99:
            raise ValueError("invalid download alternative index")
        encoded = f"dl18:{action.value}:{token}:{alternative_index}"
    elif alternative_index is None:
        encoded = f"dl18:{action.value}:{token}"
    else:
        raise ValueError("only selection callbacks may carry an alternative index")
    if len(encoded.encode()) > 64:
        raise ValueError("callback exceeds Telegram limit")
    return encoded


def parse_download_callback(value: str | None) -> DownloadCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) not in (3, 4) or parts[0] != "dl18" or not _TOKEN.fullmatch(parts[2]):
        return None
    try:
        action = DownloadCallbackAction(parts[1])
    except ValueError:
        return None
    if action is DownloadCallbackAction.SELECT:
        if len(parts) != 4 or not parts[3].isdigit():
            return None
        index = int(parts[3])
        return DownloadCallback(action, parts[2], index) if 0 <= index <= 99 else None
    return DownloadCallback(action, parts[2]) if len(parts) == 3 else None
