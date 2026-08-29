from __future__ import annotations

import pytest

from app.application.ux.flows.navigation import UxMenu
from app.application.ux.services.errors import UxErrorMessage, UxErrorService
from app.application.ux.services.progress import UxProgressService
from app.application.ux.services.state import UserUxStateService, UxState
from app.core.exceptions import DatabaseError, ProviderUnavailable
from app.core.telegram_context import TelegramChatType, TelegramContext
from app.i18n import LocalizationService
from app.telegram.callbacks import encode_ux_callback, parse_ux_callback
from app.telegram.keyboards import UxKeyboardFactory
from app.telegram.messages import UxMessage, UxMessageService


def test_stage14_callback_codec_is_versioned_and_strict() -> None:
    encoded = encode_ux_callback("menu", "section", "search")
    assert encoded == "ux1:menu:section:search"
    assert parse_ux_callback(encoded) is not None
    assert parse_ux_callback(encoded).identifier == "search"  # type: ignore[union-attr]
    assert parse_ux_callback("ux1:menu:open") is not None
    for invalid in (None, "menu:open", "ux1:MENU:open", "ux1:menu", "ux1:menu:open:"):
        assert parse_ux_callback(invalid) is None


def test_stage14_keyboard_factory_reuses_validated_callbacks() -> None:
    factory = UxKeyboardFactory(LocalizationService(("en", "ru"), "en"))
    main = factory.main_menu("en")
    callbacks = [button.callback_data for row in main.inline_keyboard for button in row]
    assert callbacks == [
        "ux1:menu:section:search",
        "ux1:menu:section:account",
        "ux1:menu:section:providers",
        "ux1:menu:section:settings",
    ]
    assert factory.for_menu("en", UxMenu.MAIN) == main
    assert (
        factory.cancel("en").inline_keyboard[0][0].callback_data == "ux1:operation:cancel:current"
    )


def test_stage14_message_service_uses_localized_category_names() -> None:
    messages = UxMessageService(LocalizationService(("en", "ru"), "en"))
    assert "Music Bot" in messages.get(UxMessage.WELCOME, "en")
    assert "операция" in messages.get(UxMessage.OPERATION_FAILED, "ru").lower()
    with pytest.raises(ValueError, match="unknown UX message"):
        messages.get("not-a-message", "en")


def test_stage14_state_transitions_and_progress_foundation() -> None:
    states = UserUxStateService()
    progress = UxProgressService(states)
    assert states.current(42) is UxState.IDLE
    assert states.transition(42, UxState.MENU) is UxState.MENU
    assert progress.update(user_id=42, state=UxState.PROCESSING).state is UxState.PROCESSING
    assert states.transition(42, UxState.IDLE) is UxState.IDLE
    with pytest.raises(ValueError, match="invalid UX transition"):
        states.transition(42, UxState.DOWNLOADING)


def test_stage20_state_is_scoped_to_the_full_telegram_context() -> None:
    states = UserUxStateService()
    group_a = TelegramContext(42, -1001, TelegramChatType.GROUP)
    group_b = TelegramContext(42, -1002, TelegramChatType.GROUP)
    private = TelegramContext(42, 42, TelegramChatType.PRIVATE)

    states.transition(group_a, UxState.SEARCH_INPUT)
    assert states.current(group_a) is UxState.SEARCH_INPUT
    assert states.current(group_b) is UxState.IDLE
    assert states.current(private) is UxState.IDLE
    states.transition(group_a, UxState.SEARCHING)
    assert states.current(group_b) is UxState.IDLE
    assert states.current(private) is UxState.IDLE


def test_stage14_error_service_hides_internal_error_details() -> None:
    errors = UxErrorService()
    assert errors.message_name(ValueError("bad input")) is UxErrorMessage.INVALID_REQUEST
    assert (
        errors.message_name(DatabaseError("SQL password=secret")) is UxErrorMessage.OPERATION_FAILED
    )
    assert (
        errors.message_name(ProviderUnavailable("provider internals"))
        is UxErrorMessage.OPERATION_FAILED
    )
