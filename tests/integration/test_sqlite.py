from __future__ import annotations

import pytest

from app.storage import Database
from app.storage.database import scalar_pragma


@pytest.mark.asyncio
async def test_sqlite_connection_settings(database: Database) -> None:
    assert str(await scalar_pragma(database.engine, "journal_mode")).lower() == "wal"
    assert int(await scalar_pragma(database.engine, "foreign_keys")) == 1
    assert int(await scalar_pragma(database.engine, "busy_timeout")) == 5000
