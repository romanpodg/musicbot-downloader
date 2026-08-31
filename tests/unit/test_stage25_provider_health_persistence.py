from datetime import UTC, datetime, timedelta

import pytest

from app.core.enums import MusicProviderName, ProviderAccountHealthState
from app.services.provider_account_selection import ProviderAccountHealth, ProviderAccountSelector


@pytest.mark.asyncio
async def test_health_projection_survives_selector_restart(database) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    selector = ProviderAccountSelector()
    account_a = ProviderAccountHealth(MusicProviderName.TIDAL, "a")
    account_b = ProviderAccountHealth(MusicProviderName.TIDAL, "b")
    async with database.transaction() as repositories:
        await selector.record_failure_durable(
            repositories.provider_account_health,
            account_a,
            rate_limited=True,
            cooldown_seconds=60,
            now=now,
        )
        await repositories.provider_account_health.upsert(account_b, now)

    restarted = ProviderAccountSelector()
    async with database.transaction() as repositories:
        eligible = await restarted.eligible_durable(
            repositories.provider_account_health,
            MusicProviderName.TIDAL,
            ("a", "b"),
            now=now + timedelta(seconds=1),
        )
    assert tuple(item.account_id for item in eligible) == ("b",)

    async with database.transaction() as repositories:
        eligible = await restarted.eligible_durable(
            repositories.provider_account_health,
            MusicProviderName.TIDAL,
            ("a", "b"),
            now=now + timedelta(seconds=61),
        )
    assert tuple(item.account_id for item in eligible) == ("a", "b")


@pytest.mark.asyncio
async def test_auth_failed_projection_survives_restart(database) -> None:
    now = datetime(2030, 1, 1, tzinfo=UTC)
    selector = ProviderAccountSelector()
    async with database.transaction() as repositories:
        failed = await selector.record_failure_durable(
            repositories.provider_account_health,
            ProviderAccountHealth(MusicProviderName.DEEZER, "a"),
            auth=True,
            now=now,
        )
        await repositories.provider_account_health.upsert(
            ProviderAccountHealth(MusicProviderName.DEEZER, "b"), now
        )
        assert failed.state is ProviderAccountHealthState.AUTH_FAILED

    async with database.transaction() as repositories:
        eligible = await ProviderAccountSelector().eligible_durable(
            repositories.provider_account_health,
            MusicProviderName.DEEZER,
            ("a", "b"),
            now=now,
        )
    assert tuple(item.account_id for item in eligible) == ("b",)
