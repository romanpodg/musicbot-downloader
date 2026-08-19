"""Stage 10.4 production long-polling application entry point."""

from __future__ import annotations

import argparse
import asyncio
import logging
from collections.abc import Sequence

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory

from app.composition import compose_stage9
from app.config import Settings, get_settings
from app.core.exceptions import ConfigurationError
from app.i18n import LocalizationService
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.owner_bootstrap import OwnerBootstrapService
from app.storage import Database
from app.telegram import AiogramTelegramGateway

logger = logging.getLogger(__name__)


async def _schema_revision(database: Database) -> str | None:
    async with database.engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: MigrationContext.configure(
                sync_connection
            ).get_current_revision()
        )


async def require_current_schema(database: Database) -> None:
    expected = ScriptDirectory.from_config(Config("alembic.ini")).get_current_head()
    try:
        current = await _schema_revision(database)
    except Exception as exc:
        raise ConfigurationError() from exc
    if current != expected:
        logger.error(
            "Database schema is not current; run `uv run alembic upgrade head`",
            extra={"current_revision": current, "expected_revision": expected},
        )
        raise ConfigurationError()


async def run_bot(settings: Settings) -> None:
    database = Database(settings.database_url)
    gateway: AiogramTelegramGateway | None = None
    components = None
    try:
        await require_current_schema(database)
        result = await OwnerBootstrapService(database, settings.owner_id).run()
        logger.info("Owner bootstrap result: %s", result.value)
        token, _ = settings.telegram_cache_configuration()
        gateway = AiogramTelegramGateway(token)
        components = await compose_stage9(
            database,
            settings,
            OnTheSpotProvider(),
            gateway=gateway,
        )
        await components.start()
        logger.info("Stage 10.4 Telegram long polling started")
        await components.dispatcher.start_polling(gateway.bot, close_bot_session=False)
    finally:
        if components is not None:
            await components.stop()
        elif gateway is not None:
            await gateway.close()
        await database.dispose()


async def check_runtime(settings: Settings) -> None:
    settings.telegram_cache_configuration()
    LocalizationService(settings.supported_locales, settings.default_locale)
    database = Database(settings.database_url)
    try:
        await require_current_schema(database)
    finally:
        await database.dispose()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Stage 10.4 Telegram downloader bot.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate bot configuration, localization, and DB revision without polling",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings)
    if args.check:
        asyncio.run(check_runtime(settings))
        print("Stage 10.4 runtime configuration is ready.")
        return 0
    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
