from __future__ import annotations

import pytest

from app.core.models import WorkerPoolSnapshot
from app.i18n import LocalizationService
from app.services.runtime_worker_control import WorkerControlSnapshot, WorkerPoolType
from app.telegram.worker_control_presentation import (
    CALLBACK_LIMIT_BYTES,
    WorkerCallback,
    WorkerCallbackAction,
    WorkerControlPresentation,
    encode_worker_callback,
    parse_worker_callback,
)


def _snapshot(download_desired: int, download_actual: int) -> WorkerControlSnapshot:
    return WorkerControlSnapshot(
        download=WorkerPoolSnapshot(download_desired, download_actual, 2, 8),
        upload=WorkerPoolSnapshot(4, 6, 3, 10),
    )


def test_worker_callback_codec_round_trips_and_stays_bounded() -> None:
    supported = (
        (WorkerCallbackAction.OVERVIEW, None),
        (WorkerCallbackAction.BACK, None),
        (WorkerCallbackAction.DETAIL, WorkerPoolType.DOWNLOAD),
        (WorkerCallbackAction.DETAIL, WorkerPoolType.UPLOAD),
        (WorkerCallbackAction.INCREASE, WorkerPoolType.DOWNLOAD),
        (WorkerCallbackAction.DECREASE, WorkerPoolType.DOWNLOAD),
        (WorkerCallbackAction.RESET, WorkerPoolType.DOWNLOAD),
        (WorkerCallbackAction.INCREASE, WorkerPoolType.UPLOAD),
        (WorkerCallbackAction.DECREASE, WorkerPoolType.UPLOAD),
        (WorkerCallbackAction.RESET, WorkerPoolType.UPLOAD),
    )
    for action, pool in supported:
        encoded = encode_worker_callback(action, pool)
        assert len(encoded.encode()) <= CALLBACK_LIMIT_BYTES
        assert parse_worker_callback(encoded) == WorkerCallback(action, pool)

    assert encode_worker_callback(WorkerCallbackAction.OVERVIEW) == "adm3:w"
    assert (
        encode_worker_callback(WorkerCallbackAction.INCREASE, WorkerPoolType.DOWNLOAD) == "adm3:d:+"
    )


@pytest.mark.parametrize(
    "value",
    (
        None,
        "",
        "adm3",
        "adm3:",
        "adm3:x",
        "adm3:x:+",
        "adm3:d:x",
        "adm3:d:+:4",
        "adm3:w:extra",
        "adm3:" + "x" * 65,
    ),
)
def test_worker_callback_codec_rejects_malformed_values(value: str | None) -> None:
    assert parse_worker_callback(value) is None


def test_worker_presentation_renders_truthful_mismatches_in_both_locales() -> None:
    presentation = WorkerControlPresentation(LocalizationService(("en", "ru"), "en"))

    for locale in ("en", "ru"):
        for desired, actual in ((3, 3), (5, 3), (2, 5), (1, 1), (8, 8), (4_231_999, 3)):
            snapshot = _snapshot(desired, actual)
            overview = presentation.overview_text(snapshot, locale)
            download = presentation.detail_text(WorkerPoolType.DOWNLOAD, snapshot, locale)
            upload = presentation.detail_text(WorkerPoolType.UPLOAD, snapshot, locale)
            assert f"{desired:,}" in overview
            assert f"{actual:,}" in download
            assert len(overview) < 4096
            assert len(download) < 4096
            assert len(upload) < 4096

        callbacks = [
            button.callback_data
            for row in presentation.overview_keyboard(locale).inline_keyboard
            for button in row
        ]
        assert callbacks == ["adm3:d", "adm3:u", "adm3:w", "adm3:b"]
        detail_callbacks = [
            button.callback_data
            for row in presentation.detail_keyboard(WorkerPoolType.DOWNLOAD, locale).inline_keyboard
            for button in row
        ]
        assert detail_callbacks == ["adm3:d:-", "adm3:d:+", "adm3:d:r", "adm3:d", "adm3:w"]

    english = presentation.detail_text(WorkerPoolType.DOWNLOAD, _snapshot(5, 3), "en")
    assert "Desired: 5" in english
    assert "Actual: 3" in english
    assert "does not interrupt active jobs" in english
