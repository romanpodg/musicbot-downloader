"""Versioned, validated callback codec for Stage 14 UX navigation."""

from __future__ import annotations

import re
from dataclasses import dataclass

_PART = re.compile(r"^[a-z][a-z0-9_-]{0,23}$")
_IDENTIFIER = re.compile(r"^[a-z0-9_-]{1,32}$")


@dataclass(frozen=True, slots=True)
class UxCallback:
    action: str
    entity: str
    identifier: str | None = None


def encode_ux_callback(action: str, entity: str, identifier: str | None = None) -> str:
    _validate_part(action)
    _validate_part(entity)
    if identifier is not None and not _IDENTIFIER.fullmatch(identifier):
        raise ValueError("invalid callback identifier")
    encoded = ":".join(part for part in ("ux1", action, entity, identifier) if part is not None)
    if len(encoded.encode()) > 64:
        raise ValueError("callback exceeds Telegram limit")
    return encoded


def parse_ux_callback(value: str | None) -> UxCallback | None:
    if not value:
        return None
    parts = value.split(":")
    if len(parts) not in (3, 4) or parts[0] != "ux1":
        return None
    action, entity = parts[1:3]
    identifier = parts[3] if len(parts) == 4 else None
    if not _PART.fullmatch(action) or not _PART.fullmatch(entity):
        return None
    if identifier is not None and not _IDENTIFIER.fullmatch(identifier):
        return None
    return UxCallback(action, entity, identifier)


def _validate_part(value: str) -> None:
    if not _PART.fullmatch(value):
        raise ValueError("invalid callback part")
