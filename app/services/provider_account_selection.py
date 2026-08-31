"""Health-aware account selection that preserves caller-provided fairness order."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from app.core.enums import MusicProviderName


class AccountHealthState(StrEnum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    COOLDOWN = "COOLDOWN"
    AUTH_FAILED = "AUTH_FAILED"
    DISABLED = "DISABLED"


@dataclass(frozen=True, slots=True)
class ProviderAccountHealth:
    provider: MusicProviderName
    account_id: str
    state: AccountHealthState = AccountHealthState.HEALTHY
    failure_streak: int = 0
    last_success_at: datetime | None = None
    last_failure_at: datetime | None = None
    cooldown_until: datetime | None = None

    def usable(self, now: datetime) -> bool:
        if self.state in {AccountHealthState.AUTH_FAILED, AccountHealthState.DISABLED}:
            return False
        return self.cooldown_until is None or self.cooldown_until <= now


class ProviderAccountSelector:
    """Filter health first, then return accounts in existing fairness order."""

    def eligible(
        self, accounts: Iterable[ProviderAccountHealth], now: datetime | None = None
    ) -> tuple[ProviderAccountHealth, ...]:
        at = now or datetime.now(UTC)
        return tuple(account for account in accounts if account.usable(at))

    def record_success(
        self, account: ProviderAccountHealth, now: datetime | None = None
    ) -> ProviderAccountHealth:
        return replace(
            account,
            state=AccountHealthState.HEALTHY,
            failure_streak=0,
            last_success_at=now or datetime.now(UTC),
            cooldown_until=None,
        )

    def record_failure(
        self,
        account: ProviderAccountHealth,
        *,
        auth: bool = False,
        rate_limited: bool = False,
        cooldown_seconds: float = 60.0,
        now: datetime | None = None,
    ) -> ProviderAccountHealth:
        at = now or datetime.now(UTC)
        if auth:
            return replace(
                account,
                state=AccountHealthState.AUTH_FAILED,
                failure_streak=account.failure_streak + 1,
                last_failure_at=at,
            )
        if rate_limited:
            return replace(
                account,
                state=AccountHealthState.COOLDOWN,
                failure_streak=account.failure_streak + 1,
                last_failure_at=at,
                cooldown_until=at + timedelta(seconds=max(1.0, cooldown_seconds)),
            )
        return replace(
            account,
            state=AccountHealthState.DEGRADED,
            failure_streak=account.failure_streak + 1,
            last_failure_at=at,
        )


__all__ = ["AccountHealthState", "ProviderAccountHealth", "ProviderAccountSelector"]
