"""Redaction patterns — SECURITY_MODEL §6.2, ADR-030, appendix T12, M.2 (AC-15)."""

from __future__ import annotations

import pytest

from app.core.redaction import REDACTED, Redactor

FAKE_ANTHROPIC = "sk-ant-api03-" + "A" * 40
FAKE_ADMIN = "sk-ant-admin01-" + "B" * 44


@pytest.fixture
def redactor() -> Redactor:
    return Redactor()


@pytest.mark.parametrize(
    "secret",
    [
        FAKE_ANTHROPIC,
        FAKE_ADMIN,
        "ghp_" + "a" * 36,
        "ghs_" + "b" * 36,
        "ghu_" + "c" * 36,
        "github_pat_" + "d" * 60,
        "AKIA" + "ABCDEFGHIJKLMNOP",
        "xoxb-" + "1234567890-abcdefghij",
    ],
)
def test_token_prefixes_are_redacted_anywhere_in_text(redactor: Redactor, secret: str) -> None:
    text = f'{{"key": "{secret}", "url": "https://x.test/?q={secret}"}}'
    out = redactor.redact(text)
    assert secret not in out
    assert REDACTED in out


def test_admin_key_is_redacted_as_a_whole(redactor: Redactor) -> None:
    out = redactor.redact(f"Authorization-less body {FAKE_ADMIN} end")
    assert "admin01" not in out
    assert out == f"Authorization-less body {REDACTED} end"


def test_pem_private_key_block(redactor: Redactor) -> None:
    pem = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEAwx\nabc123\n-----END RSA PRIVATE KEY-----"
    out = redactor.redact(f"before\n{pem}\nafter")
    assert "MIIEpAIBAAKCAQEAwx" not in out
    assert out.startswith("before\n") and out.endswith("\nafter")


def test_url_credentials(redactor: Redactor) -> None:
    out = redactor.redact("clone https://alice:hunter22@git.example.com/repo.git now")
    assert "hunter22" not in out
    assert "alice" not in out
    assert "git.example.com/repo.git" in out


@pytest.mark.parametrize(
    "line",
    [
        "Authorization: Bearer abc.def.ghi",
        "authorization: bearer abc.def.ghi",
        "Cookie: kk_session=s3cr3tv4lue; kk_csrf=zzz",
        "Set-Cookie: kk_session=s3cr3tv4lue; HttpOnly",
        "X-Api-Key: s3cr3tv4lue",
    ],
)
def test_sensitive_headers(redactor: Redactor, line: str) -> None:
    out = redactor.redact(line)
    assert "s3cr3tv4lue" not in out and "abc.def.ghi" not in out
    assert out.split(":", 1)[0] == line.split(":", 1)[0]


@pytest.mark.parametrize(
    "line",
    [
        "password=hunter22",
        "PASSWORD=hunter22",
        "passwd: hunter22",
        "token=hunter22",
        "secret = hunter22",
        "api_key: hunter22",
        "access_key=hunter22",
        '"password": "hunter22"',
        "machine github.com login bob password hunter22",
        "https://app.test/terminal/ws?ticket=hunter22&x=1",
    ],
)
def test_sensitive_key_values_any_case(redactor: Redactor, line: str) -> None:
    assert "hunter22" not in redactor.redact(line)


@pytest.mark.parametrize(
    "line",
    [
        "token_count = 3",
        "secret_santa = 'yes'",
        "def tokenize(text): return text.split()",
        "password_policy_min_length: 12",
        "the access_key_id column",
    ],
)
def test_ordinary_code_is_not_redacted(redactor: Redactor, line: str) -> None:
    assert redactor.redact(line) == line


def test_literal_configured_secrets_are_redacted() -> None:
    redactor = Redactor(literals=["n0t-a-pattern-but-configured", "short"])
    out = redactor.redact("value n0t-a-pattern-but-configured and short")
    assert "n0t-a-pattern-but-configured" not in out
    # Literals shorter than 8 characters are ignored to avoid shredding ordinary words.
    assert "short" in out


def test_redact_obj_walks_nested_structures() -> None:
    redactor = Redactor(literals=["configured-secret-value"])
    obj = {"a": ["x", {"b": f"k={FAKE_ANTHROPIC}"}], "c": ("configured-secret-value", 3), "d": None}
    out = redactor.redact_obj(obj)
    assert FAKE_ANTHROPIC not in repr(out)
    assert "configured-secret-value" not in repr(out)
    assert out["c"][1] == 3 and out["d"] is None
