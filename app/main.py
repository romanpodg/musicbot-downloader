"""Production entry point and supervised application lifecycle."""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import make_url

from app.composition import compose_stage9
from app.config import Settings, get_settings
from app.core.exceptions import ConfigurationError
from app.i18n import LocalizationService
from app.internal_api import InternalApiServer, create_internal_api_app
from app.logging import configure_logging
from app.providers.onthespot import OnTheSpotProvider
from app.services.instance_lock import (
    ApplicationInstanceAlreadyRunningError,
    ApplicationInstanceLock,
)
from app.services.owner_bootstrap import OwnerBootstrapService
from app.services.runtime_prerequisites import RuntimePrerequisiteService
from app.storage import Database
from app.telegram import AiogramTelegramGateway

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RuntimeStatus:
    ready: bool = False

    def is_ready(self) -> bool:
        return self.ready


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
            "Database schema is not current; run `alembic upgrade head`",
            extra={"current_revision": current, "expected_revision": expected},
        )
        raise ConfigurationError()


def _preflight(settings: Settings) -> None:
    report = RuntimePrerequisiteService(
        settings.temp_dir,
        ffmpeg_binary=settings.ffmpeg_binary,
        ffprobe_binary=settings.ffprobe_binary,
    ).check()
    if not report.ffmpeg_available or not report.ffprobe_available:
        logger.warning(
            "Media prerequisites are degraded",
            extra={
                "ffmpeg_available": report.ffmpeg_available,
                "ffprobe_available": report.ffprobe_available,
            },
        )


def _log_startup_summary(settings: Settings) -> None:
    logger.info(
        "application_starting database_backend=%s internal_api_enabled=%s "
        "internal_api_bind=%s:%s download_workers=%s/%s upload_workers=%s/%s "
        "temp_dir=%s owner_configured=%s",
        make_url(settings.database_url).get_backend_name(),
        settings.internal_api_enabled,
        settings.internal_api_host,
        settings.internal_api_port,
        settings.download_workers_default,
        settings.download_workers_max,
        settings.upload_workers_default,
        settings.upload_workers_max,
        settings.temp_dir,
        settings.owner_id is not None,
    )


async def _cleanup(label: str, operation: Callable[[], Awaitable[None]]) -> None:
    try:
        await operation()
    except Exception:
        logger.error("Runtime resource cleanup failed", extra={"resource": label})


async def run_bot(settings: Settings) -> None:
    """Start in dependency order and supervise critical top-level services."""

    status = RuntimeStatus()
    _preflight(settings)
    instance_lock = ApplicationInstanceLock.from_database_url(settings.database_url)
    instance_lock.acquire()
    database: Database | None = None
    gateway: AiogramTelegramGateway | None = None
    provider: OnTheSpotProvider | None = None
    components = None
    api_server: InternalApiServer | None = None
    polling_task: asyncio.Task[None] | None = None
    api_wait_task: asyncio.Task[None] | None = None
    component_wait_task: asyncio.Task[None] | None = None
    try:
        _log_startup_summary(settings)
        database = Database(settings.database_url)
        await require_current_schema(database)
        result = await OwnerBootstrapService(database, settings.owner_id).run()
        logger.info("Owner bootstrap result: %s", result.value)

        token, _ = settings.telegram_cache_configuration()
        gateway = AiogramTelegramGateway(token)
        provider = OnTheSpotProvider()
        components = await compose_stage9(database, settings, provider, gateway=gateway)
        await components.start()
        component_waiter = getattr(components, "wait_terminated", None)
        if component_waiter is not None:
            component_wait_task = asyncio.create_task(
                component_waiter(), name="component-supervisor"
            )

        api_configuration = settings.internal_api_configuration()
        if api_configuration is not None:
            host, port, api_token = api_configuration
            username = components.stage8.bot_identity.username
            if username is None:
                raise ConfigurationError()
            api_app = create_internal_api_app(
                api_token=api_token,
                registry=components.deep_links,
                bot_username=username,
                readiness=status.is_ready,
            )
            api_server = InternalApiServer(api_app, host=host, port=port)
            await api_server.start()
            api_wait_task = asyncio.create_task(
                api_server.wait_terminated(), name="internal-api-supervisor"
            )
            logger.info("Internal API listener started on %s:%s", host, port)

        polling_task = asyncio.create_task(
            components.dispatcher.start_polling(
                gateway.bot,
                close_bot_session=False,
                handle_signals=False,
            ),
            name="telegram-polling",
        )
        await asyncio.sleep(0)
        if polling_task.done():
            if polling_task.cancelled():
                raise RuntimeError("Telegram polling was cancelled unexpectedly")
            await polling_task
            raise RuntimeError("Telegram polling stopped unexpectedly")
        status.ready = True
        logger.info("application_started")

        critical_tasks = {polling_task}
        if api_wait_task is not None:
            critical_tasks.add(api_wait_task)
        if component_wait_task is not None:
            critical_tasks.add(component_wait_task)
        done, _ = await asyncio.wait(critical_tasks, return_when=asyncio.FIRST_COMPLETED)
        completed = done.pop()
        if completed.cancelled():
            raise RuntimeError("Critical application service was cancelled unexpectedly")
        await completed
        if completed is polling_task:
            service_name = "Telegram polling"
        elif completed is api_wait_task:
            service_name = "Internal API"
        else:
            service_name = "Application component"
        raise RuntimeError(f"{service_name} stopped unexpectedly")
    finally:
        status.ready = False
        logger.info("application_stopping")
        if polling_task is not None and not polling_task.done():
            polling_task.cancel()
            await asyncio.gather(polling_task, return_exceptions=True)
        if api_wait_task is not None and not api_wait_task.done():
            api_wait_task.cancel()
            await asyncio.gather(api_wait_task, return_exceptions=True)
        if component_wait_task is not None and not component_wait_task.done():
            component_wait_task.cancel()
            await asyncio.gather(component_wait_task, return_exceptions=True)
        if api_server is not None:
            await _cleanup("internal_api", api_server.stop)
        if components is not None:
            await _cleanup("application_components", components.stop)
        else:
            if provider is not None:
                await _cleanup("onthespot_provider", provider.close)
            if gateway is not None:
                await _cleanup("telegram_gateway", gateway.close)
        if database is not None:
            await _cleanup("database", database.dispose)
        instance_lock.release()
        logger.info("application_stopped")


async def check_runtime(settings: Settings) -> None:
    """Perform a network-free, no-bind production deployment preflight."""

    settings.telegram_cache_configuration()
    LocalizationService(settings.supported_locales, settings.default_locale)
    _preflight(settings)
    api_configuration = settings.internal_api_configuration()
    if api_configuration is not None:
        _, _, api_token = api_configuration
        create_internal_api_app(
            api_token=api_token,
            registry=None,
            bot_username=None,
        )
    database = Database(settings.database_url)
    try:
        await require_current_schema(database)
    finally:
        await database.dispose()


async def _run_with_signals(settings: Settings) -> None:
    loop = asyncio.get_running_loop()
    shutdown_requested = asyncio.Event()
    installed: list[signal.Signals] = []
    for name in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(name, shutdown_requested.set)
            installed.append(name)
        except (NotImplementedError, RuntimeError):
            break

    runtime_task = asyncio.create_task(run_bot(settings), name="application-runtime")
    signal_task = asyncio.create_task(shutdown_requested.wait(), name="signal-waiter")
    try:
        done, _ = await asyncio.wait(
            {runtime_task, signal_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if runtime_task in done:
            signal_task.cancel()
            await asyncio.gather(signal_task, return_exceptions=True)
            await runtime_task
            return
        runtime_task.cancel()
        await asyncio.gather(runtime_task, return_exceptions=True)
    finally:
        for name in installed:
            loop.remove_signal_handler(name)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Telegram downloader service.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="validate configuration, local paths, and DB revision without network or listeners",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        settings = get_settings()
    except ConfigurationError:
        print("Application configuration is invalid.", file=sys.stderr)
        return 2
    configure_logging(settings)
    try:
        if args.check:
            asyncio.run(check_runtime(settings))
            print("Runtime configuration is ready.")
            return 0
        asyncio.run(_run_with_signals(settings))
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    except ApplicationInstanceAlreadyRunningError:
        logger.error("application_instance_already_running")
        return 2
    except (ConfigurationError, OSError):
        logger.error("Application startup validation failed")
        return 2
    except Exception:
        logger.error("Critical application service failed")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
