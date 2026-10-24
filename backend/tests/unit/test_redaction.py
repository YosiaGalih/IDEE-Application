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


@pytest.mark.parametrize(
    "key",
    [
        "password",
        "github_token",
        "SECRET_KEY",
        "client_secret",
        "api_key",
        "Authorization",
        "kk_session_cookie",
    ],
)
def test_values_under_sensitive_keys_are_redacted(redactor: Redactor, key: str) -> None:
    out = redactor.redact_obj({key: "plainvalue123", "nested": [{key: 42}]})
    assert out[key] == REDACTED
    assert out["nested"][0][key] == REDACTED


@pytest.mark.parametrize(
    "key", ["token_count", "max_tokens", "input_tokens", "password_policy", "secret_santa"]
)
def test_ordinary_keys_are_kept(redactor: Redactor, key: str) -> None:
    assert redactor.redact_obj({key: 3}) == {key: 3}


def test_url_credentials_with_empty_user(redactor: Redactor) -> None:
    assert "pw1longer" not in redactor.redact("redis://:pw1longer@redis:6379/0")


def test_non_string_values_are_stringified_and_redacted(redactor: Redactor) -> None:
    out = redactor.redact_obj(
        {
            "error": ConnectionError("postgresql://u:pw123456@h/db"),
            "raw": b"Authorization: Bearer abc.def.ghi",
        },
        stringify=True,
    )
    assert "pw123456" not in out["error"]
    assert "abc.def.ghi" not in out["raw"]


@pytest.mark.parametrize(
    "text",
    [
        'curl -H "Authorization: Bearer abc.def.ghi" https://x.test',
        "curl -H 'Cookie: kk_session=abc.def.ghi' https://x.test",
        '{"Authorization": "Bearer abc.def.ghi"}',
        "{'x-api-key': 'abc.def.ghi'}",
        '{\\"password\\": \\"abc.def.ghi\\"}',
        "token: Bearer abc.def.ghi",
        'password: "abc def.ghi"',
        "clone https://glpat-abc.def.ghi@gitlab.test/x.git",
    ],
)
def test_reviewer_reported_pattern_gaps(redactor: Redactor, text: str) -> None:
    out = redactor.redact(text)
    assert "abc.def.ghi" not in out and "def.ghi" not in out, out


def test_truncated_pem_without_end_marker(redactor: Redactor) -> None:
    out = redactor.redact("key:\n-----BEGIN PRIVATE KEY-----\nMIIEvQIBADANBgkqhkiG9w0BAQEFAASC\nMIIEvQIBADA")
    assert "MIIEvQIBADANBgkqhkiG9w0BAQEFAASC" not in out


def test_ssh_style_user_is_not_shredded(redactor: Redactor) -> None:
    assert redactor.redact("ssh://git@github.com/org/repo.git") == "ssh://git@github.com/org/repo.git"


@pytest.mark.parametrize(
    "line",
    [
        "GITHUB_TOKEN=abc12345xyz",
        "SECRET_KEY=abc12345xyz",
        "ANTHROPIC_API_KEY=abc12345xyz",
        "DATABASE_PASSWORD=abc12345xyz",
        "access_token=abc12345xyz",
        "client_secret: abc12345xyz",
        '{"refresh_token": "abc12345xyz"}',
    ],
)
def test_compound_sensitive_names(redactor: Redactor, line: str) -> None:
    assert "abc12345xyz" not in redactor.redact(line)


@pytest.mark.parametrize(
    "key",
    [
        "accessToken",
        "clientSecret",
        "privateKey",
        "api-key",
        "x-api-key",
        "passwords",
        "cookies",
        "jwt",
        "bearer",
    ],
)
def test_camel_hyphen_and_plural_sensitive_keys(redactor: Redactor, key: str) -> None:
    assert redactor.redact_obj({key: "plainvalue123"})[key] == REDACTED


def test_bare_jwt_is_redacted(redactor: Redactor) -> None:
    jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
    assert "dozjgNryP4J3jVmNHl0w5N" not in redactor.redact(f"token {jwt} end")


def test_secret_in_dict_key_is_redacted(redactor: Redactor) -> None:
    out = redactor.redact_obj({FAKE_ANTHROPIC: 1})
    assert FAKE_ANTHROPIC not in repr(out)
