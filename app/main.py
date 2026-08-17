"""Current application startup sanity entry point (no Telegram runtime yet)."""

from __future__ import annotations

import asyncio
import logging

from app.config import get_settings
from app.i18n import LocalizationService
from app.logging import configure_logging
from app.services.owner_bootstrap import OwnerBootstrapService
from app.storage import Database


async def startup() -> None:
    settings = get_settings()
    configure_logging(settings)
    LocalizationService(settings.supported_locales, settings.default_locale)
    database = Database(settings.database_url)
    try:
        result = await OwnerBootstrapService(database, settings.owner_id).run()
        logging.getLogger(__name__).info("Owner bootstrap result: %s", result.value)
    finally:
        await database.dispose()


def main() -> None:
    asyncio.run(startup())


if __name__ == "__main__":
    main()
