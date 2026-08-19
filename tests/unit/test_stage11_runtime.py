from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.main import check_runtime, run_bot


def _settings(tmp_path) -> Settings:  # type: ignore[no-untyped-def]
    return Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'runtime.db').as_posix()}",
        bot_token="123456:TEST_TOKEN",
        telegram_cache_chat_id=-100123,
        internal_api_enabled=True,
        internal_api_host="127.0.0.1",
        internal_api_port=8081,
        internal_api_token="x" * 40,
    )


async def test_check_runtime_composes_internal_api_without_starting_server(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    with (
        patch("app.main.require_current_schema", new_callable=AsyncMock),
        patch("app.main.create_internal_api_app", return_value=object()) as create_app,
        patch("app.main.InternalApiServer") as server,
    ):
        await check_runtime(settings)
    create_app.assert_called_once_with(
        api_token="x" * 40,
        registry=None,
        bot_username=None,
    )
    server.assert_not_called()


async def test_run_bot_starts_and_stops_embedded_api_in_shared_lifecycle(tmp_path) -> None:  # type: ignore[no-untyped-def]
    settings = _settings(tmp_path)
    dispatcher = SimpleNamespace(start_polling=AsyncMock())
    components = SimpleNamespace(
        stage8=SimpleNamespace(
            bot_identity=SimpleNamespace(username="stage11_bot", telegram_bot_id=100)
        ),
        deep_links=object(),
        dispatcher=dispatcher,
        start=AsyncMock(),
        stop=AsyncMock(),
    )
    gateway = SimpleNamespace(bot=object(), close=AsyncMock())
    server_instance = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    with (
        patch("app.main.require_current_schema", new_callable=AsyncMock),
        patch(
            "app.main.OwnerBootstrapService.run",
            new_callable=AsyncMock,
            return_value=SimpleNamespace(value="UNCHANGED"),
        ),
        patch("app.main.AiogramTelegramGateway", return_value=gateway),
        patch("app.main.OnTheSpotProvider", return_value=object()),
        patch("app.main.compose_stage9", new_callable=AsyncMock, return_value=components),
        patch("app.main.create_internal_api_app", return_value=object()) as create_app,
        patch("app.main.InternalApiServer", return_value=server_instance) as server,
    ):
        await run_bot(settings)
    components.start.assert_awaited_once()
    dispatcher.start_polling.assert_awaited_once()
    server.assert_called_once()
    server_instance.start.assert_awaited_once()
    server_instance.stop.assert_awaited_once()
    components.stop.assert_awaited_once()
    create_app.assert_called_once()
