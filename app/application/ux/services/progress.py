"""Progress-state contract for future long-running UX operations."""

from __future__ import annotations

from dataclasses import dataclass

from app.application.ux.services.state import UserUxStateService, UxState


@dataclass(frozen=True, slots=True)
class UxProgress:
    user_id: int
    state: UxState


class UxProgressService:
    def __init__(self, states: UserUxStateService) -> None:
        self._states = states

    def update(self, *, user_id: int, state: UxState) -> UxProgress:
        return UxProgress(user_id=user_id, state=self._states.transition(user_id, state))
