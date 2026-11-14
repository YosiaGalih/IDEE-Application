"""Redaction patterns — SECURITY_MODEL §6.2, ADR-030, appendix T12, M.2 (AC-15)."""

from __future__ import annotations

import re

import pytest

from app.core.redaction import (
    REDACTED,
    SECRET_KEY_PATTERN,
    SENSITIVE_KEY_TAIL,
    Redactor,
    is_sensitive_key,
)

FAKE_ANTHROPIC = "sk-ant-api03-" + "A" * 40
FAKE_ADMIN = "sk-ant-admin01-" + "B" * 44
# jwt.io's public example token (not a credential), split so that no source line is a whole token.
JWT_EXAMPLE = (
    "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiIxMjM0NTY3ODkwIn0." + "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"
)


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
    # Synthetic fixture, assembled from parts so secret scanners do not flag it.
    pem = "\n".join(
        ["-----BEGIN RSA PRIVATE KEY-----", "MIIEpAIBAAKCAQEAwx", "abc123", "-----END RSA PRIVATE KEY-----"]
    )
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
        'curl -H "Authorization: Bearer abc.def.ghi" https://x.test',  # gitleaks:allow (synthetic fixture)
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
    # Synthetic fixture, assembled from parts so secret scanners do not flag it.
    jwt = ".".join(
        ["eyJhbGciOiJIUzI1NiJ9", "eyJzdWIiOiIxMjM0NTY3ODkwIn0", "dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U"]
    )
    assert "dozjgNryP4J3jVmNHl0w5N" not in redactor.redact(f"token {jwt} end")


def test_secret_in_dict_key_is_redacted(redactor: Redactor) -> None:
    out = redactor.redact_obj({FAKE_ANTHROPIC: 1})
    assert FAKE_ANTHROPIC not in repr(out)


def test_argon2_hash_is_redacted(redactor: Redactor) -> None:
    # A password hash is a credential verifier: it never leaves the users table (SECURITY_MODEL §8).
    digest = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$RdescudvJCsgt3ub+b+dWRWJTmaaJObG"
    out = redactor.redact(f"params ('a@b.test', '{digest}', 'x')")
    assert "RdescudvJCsgt3ub" not in out
    assert "c29tZXNhbHQ" not in out


def test_long_word_runs_redact_in_linear_time() -> None:
    # A tool can return a long run of letters (minified code, a base64 blob): the URL-credential pattern
    # must not retry its scheme from every position of the run (WP-3.1d1: 64 KB took 2.8 s before).
    import time

    redactor = Redactor(["kk-test-secret-literal-0123456789"])
    started = time.perf_counter()
    redactor.redact("a" * 131_072)
    redactor.redact("x9+." * 32_768)
    assert time.perf_counter() - started < 1.0


def test_url_credentials_inside_a_word_run_are_still_redacted() -> None:
    redactor = Redactor()
    assert (
        redactor.redact("prefixhttps://user:hunter2@example.test/x")
        == "prefixhttps://[REDACTED]@example.test/x"
    )
    assert redactor.redact("git+ssh://deploy:hunter2@host") == "git+ssh://[REDACTED]@host"


# --- Redactor.find (security.scan_secrets, spec 3.1d2a Goal 11): the redaction patterns, as findings ---


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        (FAKE_ANTHROPIC, "anthropic_key"),
        (FAKE_ADMIN, "anthropic_admin_key"),
        ("ghp_" + "a" * 36, "github_token"),
        ("github_pat_" + "d" * 60, "github_pat"),
        ("AKIA" + "ABCDEFGHIJKLMNOP", "aws_access_key"),
        ("xoxb-" + "1234567890-abcdefghij", "slack_token"),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIE\n-----END RSA PRIVATE KEY-----", "private_key"),
        ("clone https://alice:hunter22@git.example.com/r.git", "url_credentials"),
        ("X-Api-Key: s3cr3tv4lue", "credential_header"),
        ("password=hunter22", "credential_assignment"),
        ("machine github.com login bob password hunter22", "credential_assignment"),
        (
            JWT_EXAMPLE,
            "jwt",
        ),
        ("$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNo", "password_hash"),
    ],
)
def test_find_reports_what_redaction_would_remove(text: str, rule: str) -> None:
    redactor = Redactor()
    found = redactor.find(f"first line\n{text}\n")
    assert rule in {f.rule for f in found}, found
    assert all(f.line == 2 for f in found)
    assert redactor.redact(text) != text  # the same patterns: what is found is also redacted


def test_find_never_returns_the_secret_and_ordinary_code_is_clean() -> None:
    redactor = Redactor()
    found = redactor.find(f"a = 1\nkey = '{FAKE_ANTHROPIC}'\ntoken_count = 3\n")
    assert [(f.line, f.rule) for f in found] == [(2, "anthropic_key")]
    assert FAKE_ANTHROPIC not in repr(found)
    assert redactor.find("token_count = 3\ndef tokenize(text): return text.split()\n") == []


# --- One key predicate for free text and structure (WP-3.1f2c R-1; Lead decisions 3.1f2c #2, #3, #14) ---

SECRET_VALUE = "Zq7" * 20
SECRET_KEYS = [
    # snake, UPPER, kebab
    "password", "db_password", "DB_PASSWORD", "api-key", "x-api-key",
    # camel and Pascal (AG-13 R-1)
    "clientSecret", "ClientSecret", "accessToken", "refreshToken", "secretKey", "privateKey", "dbPassword",
    "awsSecretAccessKey", "apiKey", "APIKey",
    # an acronym before the tail word (#14); "session" stays a tail (#3)
    "SMTPPassword", "DBPassword", "JWTSecret", "AWSSecretKey", "APIToken", "ACCESSToken", "userSession",
    "HTTPSession",
    # a private name: leading underscores are not part of the name (Lead ruling on Task 1 concern 2)
    "_password", "__secret", "_clientSecret", "_accessToken", "__DB_PASSWORD",
]  # fmt: skip
# The tail is not the last word: these stay readable (Goal 3).
PLAIN_KEYS = [
    "token_count", "max_tokens", "password_policy", "secret_santa", "tokenizer", "sessionId", "cookieConsent",
    "authorizationUrl", "passwordHint", "tokensUsed", "_token_count", "__max_tokens",
]  # fmt: skip
# Every spelling AG-13 tried: assignment, JSON, escaped JSON, query string, YAML; and an attribute assignment.
FORMS = [
    '{k} = "{v}"', "{k}={v}", '"{k}": "{v}"', '\\"{k}\\": \\"{v}\\"', "?{k}={v}&x=1", "{k}: '{v}'",
    'self.{k} = "{v}"',
]  # fmt: skip


@pytest.mark.parametrize("form", FORMS)
@pytest.mark.parametrize("key", [*SECRET_KEYS, *PLAIN_KEYS])
def test_free_text_and_structure_agree_on_every_key(redactor: Redactor, key: str, form: str) -> None:
    assert is_sensitive_key(key) == (key in SECRET_KEYS)  # the structure layer's verdict, as intended
    out = redactor.redact(form.format(k=key, v=SECRET_VALUE))
    assert (SECRET_VALUE not in out) == is_sensitive_key(key), out


def _tails() -> list[list[str]]:
    """Every tail of SENSITIVE_KEY_TAIL, plurals spelled out, as its words: "secret_key" -> [secret, key]."""
    words: list[list[str]] = []
    for tail in SENSITIVE_KEY_TAIL.split("|"):
        stem = tail.removesuffix("s?")
        words.append(stem.split("_"))
        if stem != tail:  # "secrets?": the plural too
            words.append((stem + "s").split("_"))
    return words


def _spellings(words: list[str]) -> list[str]:
    """The spellings a developer writes a secret key in, each ending in the tail."""
    pascal = "".join(word.capitalize() for word in words)
    return [
        "_".join(words), "_".join(words).upper(), "-".join(words), words[0] + pascal[len(words[0]):], pascal,
        "db_" + "_".join(words), "DB_" + "_".join(words).upper(), "x-" + "-".join(words), "dbAuth" + pascal,
        "DB" + pascal, "db2" + pascal, "db" + words[0].upper() + pascal[len(words[0]):], "my_app" + pascal,
        "_" + "_".join(words), "__" + words[0] + pascal[len(words[0]):], "_" + pascal,
        "__x_" + "_".join(words),
    ]  # fmt: skip


@pytest.mark.parametrize("words", _tails(), ids="_".join)
def test_free_text_and_structure_agree_on_every_tail_in_every_spelling(
    redactor: Redactor, words: list[str]
) -> None:
    secret = _spellings(words)
    # The same spellings with a word after the tail: not a secret key, in either layer (Goal 3).
    plain = [
        *(key + "Hint" for key in secret),
        *(key + "_hint" for key in secret),
        *(key + "-id" for key in secret),
    ]
    assert all(is_sensitive_key(key) for key in secret), [key for key in secret if not is_sensitive_key(key)]
    assert not any(is_sensitive_key(key) for key in plain), [key for key in plain if is_sensitive_key(key)]
    disagree = [
        text
        for key in [*secret, *plain]
        for text in (form.format(k=key, v=SECRET_VALUE) for form in FORMS)
        if (SECRET_VALUE not in redactor.redact(text)) != is_sensitive_key(key)
    ]
    assert not disagree, disagree


def test_the_secret_key_pattern_is_the_predicate_whatever_the_enclosing_flags() -> None:
    # _KEY_VALUE (and the delta stream's key check) embed SECRET_KEY_PATTERN in a case-insensitive pattern:
    # the pattern carries its own case rules, so a camel hump is a hump there too.
    # Without a hump or a separator there is no tail word: "DBPASSWORD" is one word, as in is_sensitive_key.
    case_decides = ["DBPASSWORD", "SECRETKEY", "secretkey", "dbpassword"]
    keys = [*SECRET_KEYS, *PLAIN_KEYS, *case_decides, *(k for w in _tails() for k in _spellings(w))]
    for flags in (0, re.IGNORECASE):
        wrong = [k for k in keys if bool(re.fullmatch(SECRET_KEY_PATTERN, k, flags)) != is_sensitive_key(k)]
        assert not wrong, (flags, wrong)


@pytest.mark.parametrize(
    "key", ["SMTPPassword", "JWTSecret", "AWSSecretKey", "APIToken", "ACCESSToken", "HTTPSession"]
)
def test_acronym_keys_are_sensitive(redactor: Redactor, key: str) -> None:
    assert is_sensitive_key(key)
    assert redactor.redact_obj({key: "v"}) == {key: REDACTED}


@pytest.mark.parametrize(
    ("text", "plain_key"),
    [
        (f'note="password={SECRET_VALUE}"', "note"),
        (f"url=https://x.test/?token={SECRET_VALUE}", "url"),
        (f"msg: 'clientSecret: \"{SECRET_VALUE}\"'", "msg"),
    ],
    ids=["note", "url", "msg"],
)
def test_a_plain_pair_never_swallows_a_secret_pair(redactor: Redactor, text: str, plain_key: str) -> None:
    # Keys are generated from the tails, not "any identifier, then the predicate": a non-secret pair matched
    # whole would hide the secret pair inside its value from every later match (Lead decision 3.1f2c #2).
    out = redactor.redact(text)
    assert SECRET_VALUE not in out, out
    assert out.startswith(plain_key), out


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"foo_password={SECRET_VALUE}", f"foo_password={REDACTED}"),  # the whole name, never from its "_"
        (f"self._password={SECRET_VALUE}", f"self._password={REDACTED}"),
        (f"obj._accessToken={SECRET_VALUE}", f"obj._accessToken={REDACTED}"),
        (f"__secret={SECRET_VALUE}", f"__secret={REDACTED}"),
        (f"self._token_count={SECRET_VALUE}", f"self._token_count={SECRET_VALUE}"),
    ],
    ids=["foo_password", "self._password", "obj._accessToken", "__secret", "self._token_count"],
)
def test_a_private_name_is_the_name_after_its_underscores(
    redactor: Redactor, text: str, expected: str
) -> None:
    assert redactor.redact(text) == expected
