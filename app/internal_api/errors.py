"""Sanitized stable HTTP error contract."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class InternalApiError(Exception):
    status_code: int
    code: str
    message: str
    headers: dict[str, str] = field(default_factory=dict)
