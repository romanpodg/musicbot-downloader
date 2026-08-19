"""Dedicated static Bearer authentication for the private Internal API."""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Header

from app.internal_api.errors import InternalApiError


class InternalApiAuth:
    def __init__(self, expected_token: str) -> None:
        if len(expected_token) < 32:
            raise ValueError("Internal API token is too short")
        self._expected_token = expected_token

    async def __call__(self, authorization: Annotated[str | None, Header()] = None) -> None:
        candidate = ""
        if authorization is not None and authorization.startswith("Bearer "):
            candidate = authorization[7:]
            if not candidate or any(character.isspace() for character in candidate):
                candidate = ""
        if not secrets.compare_digest(candidate, self._expected_token):
            raise InternalApiError(
                401,
                "UNAUTHORIZED",
                "Authentication is required.",
                headers={"WWW-Authenticate": "Bearer"},
            )
