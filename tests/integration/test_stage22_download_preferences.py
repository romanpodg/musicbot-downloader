from __future__ import annotations

import pytest

from app.core.enums import DeliveryMode, FormatPreference, QualityPreference
from app.services.download_preferences import UserDownloadPreferencesService


@pytest.mark.asyncio
async def test_missing_row_defaults_and_atomic_upsert(database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(22001)
    service = UserDownloadPreferencesService(database)
    defaults = await service.get_for_user(user.id)
    assert defaults.quality is QualityPreference.BEST_AVAILABLE
    async with database.transaction() as repositories:
        assert await repositories.download_preferences.get(user.id) is None
    await service.update_quality(user.id, QualityPreference.LOSSLESS)
    await service.update_delivery_mode(user.id, DeliveryMode.DOCUMENT)
    loaded = await service.get_for_user(user.id)
    assert loaded.quality is QualityPreference.LOSSLESS
    assert loaded.delivery_mode is DeliveryMode.DOCUMENT
    async with database.transaction() as repositories:
        record = await repositories.download_preferences.get(user.id)
        assert record is not None
        assert record.quality is QualityPreference.LOSSLESS


@pytest.mark.asyncio
async def test_reset_defaults_is_durable_and_canonical(database) -> None:
    async with database.transaction() as repositories:
        user = await repositories.users.create_user(22002)
    service = UserDownloadPreferencesService(database)
    await service.update(
        user.id,
        quality=QualityPreference.LOSSLESS,
        format=FormatPreference.FLAC,
        embed_cover=False,
    )
    reset = await service.reset(user.id)
    assert reset.quality is QualityPreference.BEST_AVAILABLE
    assert reset.format is FormatPreference.ORIGINAL
    assert reset.embed_cover is True
    recreated = UserDownloadPreferencesService(database)
    loaded = await recreated.get_for_user(user.id)
    assert loaded == reset
