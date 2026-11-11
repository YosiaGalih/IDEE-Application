"""Regression tests for the AG-13 WP-3.1e1 findings (.claude/rules/backend.md: a security bug gets its test
in tests/security/): gateway credentials in the stored endpoint (M1, N2), who may turn provider calls back on
(M2), the key hint (L2) and the echoed alias (L3)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from app.core.config import Settings
from app.services import provider_readiness as service
from tests.integration.test_projects import (  # noqa: F401 - fixtures shared with the projects tests
    PROJECTS,
    app,
    clean_state,
    create,
    grant,
    headers,
    make_user,
    signed_in,
    workspace_root,
)
from tests.integration.test_provider_readiness import record, report


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("https://user:tok3n@gateway.example:8443/v1?key=abc#frag", "https://gateway.example:8443/v1"),
        ("https://GATEWAY.example", "https://gateway.example"),
        (None, None),
    ],
)
async def test_the_stored_base_url_carries_no_credentials(
    session: AsyncSession, settings: Settings, raw: str | None, stored: str | None
) -> None:
    row = await record(session, settings, report(), base_url=raw)
    assert row.base_url == stored
    assert service.public_base_url(raw) == stored


async def test_an_overlong_base_url_is_cut_not_a_lost_report(
    session: AsyncSession, settings: Settings
) -> None:
    row = await record(session, settings, report(), base_url="https://gateway.example/" + "p" * 400)
    assert row.base_url is not None and len(row.base_url) <= 300 and row.base_url.endswith("…")
    assert row.readiness_status == "passed"


@pytest.mark.parametrize(
    ("key", "hint"),
    [
        ("sk-ant-" + "api03-" + "k" * 30 + "a1b2", "sk-ant-…a1b2"),
        ("AbCdEfGhIjKlMnOpQrSt", "…QrSt"),  # not an Anthropic key: no prefix shown, only the last four
        ("sk-ant-short1", "…"),
    ],
)
def test_the_hint_shows_the_vendor_prefix_only_for_a_vendor_key(key: str, hint: str) -> None:
    assert service.key_hint(key) == hint


def test_an_unknown_alias_is_not_echoed_back() -> None:
    pasted = "sk-ant-" + "pasted-into-the-alias-" + "x" * 10
    with pytest.raises(ValueError) as raised:
        service.validate_model_policy({pasted: "claude-x"})
    assert pasted not in str(raised.value) and "unknown model alias" in str(raised.value)


async def test_turning_provider_calls_back_on_needs_a_provider_admin(
    app: FastAPI,  # noqa: F811 - the projects app fixture
    engine: AsyncEngine,
) -> None:
    """AG-13 3.1e1 M2, Lead decision 3.1e #10: FR-158 is an admin control (UC-10). Whoever may write the
    project may stop provider calls (the safe direction: the project's own data flow); turning them back on
    needs settings:providers, so an admin's stop cannot be undone by a project writer. A global admin sees
    the project only with a role on it (AC-01: a global role never widens visibility)."""
    await make_user(engine, "dev@example.test", ("developer", "global"))
    developer = await signed_in(app, "dev@example.test", "user-long-passphrase")
    try:
        created = (await create(developer, "Dev Project")).json()["data"]
        url = f"{PROJECTS}/{created['id']}"
        # Sending true while calls are on changes nothing, so it needs nothing (a client sending every key).
        unchanged = await developer.patch(url, json={"settings": {"provider_calls_enabled": True}},
                                          headers=headers(developer))  # fmt: skip
        assert unchanged.status_code == 200, unchanged.text
        off = await developer.patch(url, json={"settings": {"provider_calls_enabled": False}},
                                    headers=headers(developer))  # fmt: skip
        assert off.status_code == 200, off.text
        refused = await developer.patch(url, json={"settings": {"provider_calls_enabled": True}},
                                        headers=headers(developer))  # fmt: skip
        assert refused.status_code == 403, refused.text
        mixed = await developer.patch(
            url, json={"settings": {"auto_remediation": True, "provider_calls_enabled": True}},
            headers=headers(developer),
        )  # fmt: skip
        assert mixed.status_code == 403  # nothing of a refused request is applied
        admin_id = await make_user(engine, "admin@example.test", ("admin", "global"))
        admin = await signed_in(app, "admin@example.test", "user-long-passphrase")
        try:
            assert (await admin.get(url)).status_code == 404  # not visible without a role on the project
            await grant(engine, admin_id, "readonly", f"project:{created['id']}")
            on = await admin.patch(
                url, json={"settings": {"provider_calls_enabled": True}}, headers=headers(admin)
            )
            assert on.status_code == 200, on.text
        finally:
            await admin.aclose()
        again = (await developer.get(url)).json()["data"]["settings"]
        assert again == {"provider_calls_enabled": True}
    finally:
        await developer.aclose()


def test_a_base_url_with_a_token_in_its_path_or_params_is_not_kept_as_is() -> None:
    """AG-13 3.1e1 N2: path parameters are dropped and a key-shaped token in the path is redacted."""
    token = "sk-ant-" + "in-the-path-" + "p" * 20
    assert service.public_base_url("https://gw.example/v1;apikey=abc") == "https://gw.example/v1"
    stored = service.public_base_url(f"https://gw.example/{token}/v1")
    assert stored is not None and token not in stored and stored.startswith("https://gw.example/")


async def test_an_invalid_port_still_records_the_report(session: AsyncSession, settings: Settings) -> None:
    """AG-13 3.1e1 N2: a base URL urlsplit cannot read (port 99999) must not lose the report after calls."""
    row = await record(session, settings, report(), base_url="https://gw.example:99999/v1")
    assert row.readiness_status == "passed" and row.base_url == "[invalid base url]"
