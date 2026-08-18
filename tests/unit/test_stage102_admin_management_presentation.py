from __future__ import annotations

import pytest

from app.i18n import LocalizationService
from app.services.admin_management import (
    AdministratorPage,
    ManagedUser,
    PromotionCandidatePage,
)
from app.telegram.admin_management_presentation import (
    MAX_ADMIN_CALLBACK_PAGE,
    MAX_INTERNAL_USER_ID,
    AdminManagementCallbackAction,
    AdminManagementPresentation,
    encode_admin_management_callback,
    parse_admin_management_callback,
)


def test_administrator_management_callback_codec_is_bounded_and_strict() -> None:
    for action in AdminManagementCallbackAction:
        kwargs = (
            {}
            if action
            in {
                AdminManagementCallbackAction.LIST,
                AdminManagementCallbackAction.CANDIDATES,
            }
            else {"target_user_id": MAX_INTERNAL_USER_ID}
        )
        encoded = encode_admin_management_callback(action, page=MAX_ADMIN_CALLBACK_PAGE, **kwargs)
        assert len(encoded.encode()) <= 64
        parsed = parse_admin_management_callback(encoded)
        assert parsed is not None
        assert parsed.action is action
        assert parsed.page == MAX_ADMIN_CALLBACK_PAGE
        assert parsed.target_user_id == kwargs.get("target_user_id")

    for malformed in (
        None,
        "",
        "adm2:l",
        "adm2:l:-1",
        "adm2:l:1000001",
        "adm2:l:99999999999999999999",
        "adm2:l:0:1",
        "adm2:p:0:0",
        "adm2:p:-1:0",
        "adm2:p:1:-1",
        "adm2:p:not-an-id:0",
        "adm2:unknown:1:0",
        "adm1:refresh",
    ):
        assert parse_admin_management_callback(malformed) is None

    with pytest.raises(ValueError):
        encode_admin_management_callback(AdminManagementCallbackAction.LIST, page=-1)
    with pytest.raises(ValueError):
        encode_admin_management_callback(
            AdminManagementCallbackAction.PROMOTE_COMMIT, target_user_id=None
        )


def test_management_pages_render_compact_localized_safe_identity_and_navigation() -> None:
    presentation = AdminManagementPresentation(LocalizationService(("en", "ru"), "en"))
    owner = ManagedUser(1, 1001, "owner", "Owner")
    users = tuple(
        ManagedUser(index, 2000 + index, None if index == 2 else f"юзер{index}\n", None)
        for index in range(2, 10)
    )
    admin_page = AdministratorPage(owner, users, 10_000, 1, 3)
    candidate_page = PromotionCandidatePage(users, 10_000, 1, 3)

    for locale in ("en", "ru"):
        admin_text = presentation.administrators_text(admin_page, locale)
        candidate_text = presentation.candidates_text(candidate_page, locale)
        assert len(admin_text) < 4096
        assert len(candidate_text) < 4096
        assert "@None" not in admin_text + candidate_text
        assert "1001" in admin_text
        for keyboard in (
            presentation.administrators_keyboard(admin_page, locale),
            presentation.candidates_keyboard(candidate_page, locale),
        ):
            callbacks = [button.callback_data for row in keyboard.inline_keyboard for button in row]
            assert all(value is not None and len(value.encode()) <= 64 for value in callbacks)

    assert "Administrators" in presentation.administrators_text(admin_page, "en")
    assert "Page 2 / 3" in presentation.administrators_text(admin_page, "en")
    assert "@юзер3" in presentation.identity_text(users[1], "en")
    assert "\n\n" not in presentation.identity_text(users[1], "en")


def test_management_empty_states_explain_existing_user_requirement() -> None:
    presentation = AdminManagementPresentation(LocalizationService(("en", "ru"), "en"))
    owner = ManagedUser(1, 1001, None, None)
    admin_page = AdministratorPage(owner, (), 0, 0, 1)
    candidate_page = PromotionCandidatePage((), 0, 0, 1)

    assert "No administrators" in presentation.administrators_text(admin_page, "en")
    candidate_text = presentation.candidates_text(candidate_page, "en")
    assert "No users" in candidate_text
    assert "must interact" in candidate_text
