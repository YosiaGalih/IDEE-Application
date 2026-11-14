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
    # a command-line flag: leading dashes are not part of the name either (Lead ruling, flags);
    # "--no-password" is no_password, a password key by the predicate (no special case)
    "--password", "-password", "--api-key", "-api-key", "--client-secret", "--clientSecret", "--no-password",
]  # fmt: skip
# The tail is not the last word: these stay readable (Goal 3).
PLAIN_KEYS = [
    "token_count", "max_tokens", "password_policy", "secret_santa", "tokenizer", "sessionId", "cookieConsent",
    "authorizationUrl", "passwordHint", "tokensUsed", "_token_count", "__max_tokens",
    "--token-count", "--max-tokens", "--password-file", "--session-id", "-password_policy",
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
        "__x_" + "_".join(words), "--" + "-".join(words), "-" + "_".join(words),
        "--" + words[0] + pascal[len(words[0]):],
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


# --- Command-line flags (Lead ruling on Task 1 concern 2): a sensitive key after one or two dashes ---

FLAG_SECRETS = [
    ("mysql --password={v}", "mysql --password="),
    ("curl --token {v}", "curl --token "),
    ("cli -password={v}", "cli -password="),
    ("--api-key={v}", "--api-key="),
    ("docker login --password {v}", "docker login --password "),
    ("tool --api-key {v} --verbose", "tool --api-key "),
    ("tool --client-secret {v}", "tool --client-secret "),
    ("tool --access-token {v}", "tool --access-token "),
    ("tool --clientSecret {v}", "tool --clientSecret "),
    ("tool -password {v}", "tool -password "),
    ("tool --DB_PASSWORD {v}", "tool --DB_PASSWORD "),
    ("tool --password: {v}", "tool --password: "),
    ('tool --password "{v}"', "tool --password "),
    ("tool --password '{v} more'", "tool --password "),
    ("tool --token\t{v}", "tool --token\t"),
    ('cmd="--password={v}"', 'cmd="--password='),
    ("PGOPTIONS=--password={v}", "PGOPTIONS=--password="),
    ("-password = {v}", "-password = "),  # a removed diff line; redacted once (exact: below)
]


@pytest.mark.parametrize(("template", "kept"), FLAG_SECRETS, ids=[t for t, _ in FLAG_SECRETS])
def test_a_flag_hides_its_value(redactor: Redactor, template: str, kept: str) -> None:
    text = template.format(v=SECRET_VALUE)
    out = redactor.redact(text)
    assert SECRET_VALUE not in out, out
    assert out.startswith(kept), out  # the flag itself stays readable
    assert "credential_assignment" in {f.rule for f in redactor.find(text)}


@pytest.mark.parametrize(
    "text",
    [
        "tool --token-count 5",
        "tool --max-tokens 4096",
        "tool --password-file ./p.txt",
        "tool --session-id 3",
        "-password_policy = strict",
        # A whitespace separator makes a value only after a flag: bare prose and code stay as they are.
        f"the token {SECRET_VALUE}",
        f"api-key {SECRET_VALUE}",
        f"clientSecret {SECRET_VALUE}",
        "tool --password",
        "tool --password\t",
    ],
)
def test_a_flag_that_is_not_secret_stays_readable(redactor: Redactor, text: str) -> None:
    assert redactor.redact(text) == text


def test_no_password_is_a_password_flag_by_the_predicate(redactor: Redactor) -> None:
    # "--no-password" normalises to no_password, whose tail is "password": the predicate calls it sensitive,
    # so its next token is redacted like any flag's, even when that token is another flag. No special case.
    assert is_sensitive_key("--no-password")
    assert redactor.redact("mysql --no-password --force") == f"mysql --no-password {REDACTED}"
    assert redactor.redact("mysql --no-password") == "mysql --no-password"
    assert redactor.redact("mysql --no-password=true") == f"mysql --no-password={REDACTED}"


# --- Value forms (WP-3.1f2c R-4, R-5, R-6; Lead decisions 3.1f2c #6, #7, #8) ---

V = SECRET_VALUE


@pytest.mark.parametrize(
    "text",
    [
        f'password = "{V}',
        f"password = '{V}",
        f'password: "{V}\nmore\nlines',
        f'PASSWORD="line1\n{V}"',  # closed on a later line: still one value (#6)
        # The same in the other spellings (spec, Test): JSON, escaped JSON, a flag, an argv list.
        f'{{"password": "{V}',
        f'{{\\"password\\": \\"{V}',
        f'tool --password "a b {V}"',
        f"tool --password '{V}",
        f'["mysql", "--password", "{V}',
        f'password = "{V}\\',  # a lone backslash at the very end
    ],
)
def test_an_unclosed_quote_is_redacted_to_the_end(redactor: Redactor, text: str) -> None:
    assert V not in redactor.redact(text)


def test_a_quote_closed_on_a_later_line_ends_the_value_there(redactor: Redactor) -> None:
    assert redactor.redact(f'PASSWORD="line1\n{V}"\nnext = 1') == f"PASSWORD={REDACTED}\nnext = 1"
    assert redactor.redact(f'x = 1\npassword = "{V}\nmore') == f"x = 1\npassword = {REDACTED}"


def test_netrc_never_takes_a_separator_as_the_value(redactor: Redactor) -> None:
    out = Redactor().redact(f'password = "{V}')
    assert "password [REDACTED]" not in out and V not in out, out
    assert redactor.redact(f"password = {V}") == f"password = {REDACTED}"
    assert redactor.redact(f"password : {V}") == f"password : {REDACTED}"
    assert V not in redactor.redact(f"machine h login u password {V}")


def test_netrc_value_stays_on_its_line(redactor: Redactor) -> None:
    assert redactor.redact(f"password\n{V}") == f"password\n{V}"
    assert redactor.redact(f"machine h login u password {V}") == f"machine h login u password {REDACTED}"
    assert redactor.redact(f"machine h login u password\t{V}") == f"machine h login u password\t{REDACTED}"


@pytest.mark.parametrize(
    "text",
    [
        f'{{"password": "ab\\"{V}"}}',
        f'password = """{V}"""',
        f"password = '''{V}'''",
        f'password = """{V}',  # never closed
        f"password = '''{V}",
        f'password = """\nline1\n{V}\n"""',
        f'{{\\"password\\": \\"ab\\\\\\"{V}\\"}}',  # escaped JSON whose value holds an escaped quote
        f"secret: 'it\\'s {V}'",
    ],
)
def test_escaped_and_triple_quotes(redactor: Redactor, text: str) -> None:
    assert V not in redactor.redact(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f'password = """{V}""" and more', f"password = {REDACTED} and more"),
        (f'{{"password": "ab\\"{V}", "next": "kept"}}', f'{{"password": {REDACTED}, "next": "kept"}}'),
        (
            f'{{\\"password\\": \\"ab\\\\\\"{V}\\", \\"next\\": 1}}',
            f'{{\\"password\\": {REDACTED}, \\"next\\": 1}}',
        ),
    ],
)
def test_a_quoted_value_ends_at_its_own_closing_quote(redactor: Redactor, text: str, expected: str) -> None:
    assert redactor.redact(text) == expected


def test_an_escaped_backslash_closes_the_string(redactor: Redactor) -> None:
    # 'secret: "a\\" ' + V: the double backslash is one escaped backslash, so the quote after it closes the
    # string before the space and V. Escapes are not over-read.
    out = redactor.redact('secret: "a\\\\" ' + V)
    assert V in out
    assert out == f"secret: {REDACTED} {V}"


@pytest.mark.parametrize(
    "text",
    [
        "ghp_" + "A1b2C3d4E5" * 4 + f'password="{V}"',
        "sk-ant-api03-" + "A1b2C3d4" * 6 + f"password={V}",
        f'password : password:\t"{V}"',
        "ghp_" + "A1b2C3d4E5" * 4 + f"Password: '{V}",
        f"password: x= '{V}'",
        f'token: Bearer "{V}"',
    ],
)
def test_a_key_swallowed_by_a_token_still_hides_its_value(redactor: Redactor, text: str) -> None:
    assert V not in redactor.redact(text)


def test_a_value_after_a_token_is_redacted_once(redactor: Redactor) -> None:
    assert redactor.redact("ghp_" + "A1b2C3d4E5" * 4 + f'password="{V}"') == f"{REDACTED}={REDACTED}"
    # A token in a list or a JSON value is followed by no assignment: nothing more is removed.
    token = "ghp_" + "a" * 36
    assert redactor.redact(f'["{token}", "kept"]') == f'["{REDACTED}", "kept"]'
    assert redactor.redact(f'{{"a": "{token}", "b": "kept"}}') == f'{{"a": "{REDACTED}", "b": "kept"}}'


def test_a_stray_apostrophe_after_a_key_redacts_one_word() -> None:
    out = Redactor().redact("the token: it's here")
    assert "'s here" in out
    assert out == f"the token: {REDACTED}'s here"


def test_find_names_the_new_rules() -> None:
    rules = [f.rule for f in Redactor().find("ghp_" + "A1b2C3d4E5" * 4 + f'password="{V}"')]
    assert "github_token" in rules and "credential_assignment" in rules


def test_find_reports_a_value_after_a_multi_line_token_on_the_line_it_is_on() -> None:
    pem = "\n".join(["-----BEGIN RSA PRIVATE KEY-----", "MIIE", "-----END RSA PRIVATE KEY-----"])
    found = Redactor().find(f"a\n{pem}: {V}\nb = 1\n")
    assert [(f.line, f.rule) for f in found] == [(2, "private_key"), (4, "credential_assignment")]


# --- Command lines (Lead rulings on Task 1: argv lists, quoted flags, flag values on one line) ---


@pytest.mark.parametrize(
    "text",
    [
        f'["mysql", "--password", "{V}"]',
        f"['curl', '--token', '{V}']",
        f'("--api-key", "{V}")',
        f'"--password" "{V}"',
        f"'--password' {V}",
        f"\"--password\" '{V}'",
        f'[\\"mysql\\", \\"--password\\", \\"{V}\\"]',
        f'[\n  "mysql",\n  "--password",\n  "{V}"\n]',
        f'tool --password "a b {V}"',
        f"tool --password '{V}",
    ],
)
def test_an_argv_list_hides_the_flag_value(redactor: Redactor, text: str) -> None:
    assert V not in redactor.redact(text)
    assert "credential_assignment" in {f.rule for f in redactor.find(text)}


def test_an_argv_list_keeps_everything_but_the_value(redactor: Redactor) -> None:
    assert redactor.redact(f'["mysql", "--password", "{V}", "-h", "db"]') == (
        f'["mysql", "--password", {REDACTED}, "-h", "db"]'
    )
    assert redactor.redact('["tool", "--password-file", "p.txt"]') == '["tool", "--password-file", "p.txt"]'


def test_redact_obj_drops_the_item_after_a_secret_flag() -> None:
    redactor = Redactor()
    assert redactor.redact_obj(["mysql", "--password", V]) == ["mysql", "--password", REDACTED]
    assert redactor.redact_obj(("mysql", "--password", V)) == ("mysql", "--password", REDACTED)
    assert redactor.redact_obj(["--password-file", "p.txt"]) == ["--password-file", "p.txt"]
    assert V not in repr(redactor.redact_obj([f"--password={V}", "next"]))
    assert redactor.redact_obj([f"--password={V}", "next"])[1] == "next"
    assert redactor.redact_obj({"argv": ["curl", "--token", V, "-v"]}) == {
        "argv": ["curl", "--token", REDACTED, "-v"]
    }
    # Only a flag: a list of words is not a command line.
    assert redactor.redact_obj(["the", "token", "was", "used"]) == ["the", "token", "was", "used"]


def test_a_secret_flag_is_never_another_secret_flags_value(redactor: Redactor) -> None:
    # "--no-password" takes the next word (Lead ruling #15), but never a secret flag: that flag's own value
    # would be left raw after it.
    assert redactor.redact(f"mysql --no-password --password {V}") == (
        f"mysql --no-password --password {REDACTED}"
    )
    assert V not in redactor.redact(f'["--no-password", "--token", "{V}"]')
    assert redactor.redact_obj(["--no-password", "--password", V]) == [
        "--no-password",
        "--password",
        REDACTED,
    ]
    assert redactor.redact_obj(["--token", "--token"]) == ["--token", "--token"]


def test_a_flag_value_is_one_word_on_its_line(redactor: Redactor) -> None:
    # argparse help: the metavar after a flag is its "value"; the help text after it stays readable.
    assert redactor.redact("--token TOKEN    The API token") == f"--token {REDACTED}    The API token"
    assert redactor.redact("  --github-token TOKEN\n      GitHub token for the API") == (
        f"  --github-token {REDACTED}\n      GitHub token for the API"
    )
    assert redactor.redact("tool --token bearer\n--verbose") == f"tool --token {REDACTED}\n--verbose"
    # A scheme word and its credential share a line in key=value too.
    assert redactor.redact("token: Bearer\nnext_line") == f"token: {REDACTED}\nnext_line"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f"-password = {V}", f"-password = {REDACTED}"),
        (f"--api-key = {V}", f"--api-key = {REDACTED}"),
        (f"--api-key : {V}", f"--api-key : {REDACTED}"),
        (f'"--password" = "{V}"', f'"--password" = {REDACTED}'),
    ],
)
def test_a_flag_with_a_spaced_separator_is_redacted_once(
    redactor: Redactor, text: str, expected: str
) -> None:
    assert redactor.redact(text) == expected


# --- Well-known secret environment names that end in no tail (Lead ruling on Task 1) ---


@pytest.mark.parametrize("name", ["PGPASSWORD", "MYSQL_PWD", "REDISCLI_AUTH", "pgpassword", "MySQL_Pwd"])
def test_well_known_secret_env_names(redactor: Redactor, name: str) -> None:
    assert is_sensitive_key(name)
    assert V not in redactor.redact(f"{name}={V}")
    assert V not in redactor.redact(f'export {name}="{V}"')
    assert redactor.redact_obj({name: V}) == {name: REDACTED}


@pytest.mark.parametrize("text", ["pwd = /home/u", "PGPASSFILE=/x", "MYSQL_PWD_FILE=/x", "cd $(pwd)"])
def test_names_near_a_known_secret_name_stay_readable(redactor: Redactor, text: str) -> None:
    assert redactor.redact(text) == text


def test_known_secret_names_are_exact_names_in_both_layers() -> None:
    from app.core.redaction import KNOWN_SECRET_NAMES

    assert KNOWN_SECRET_NAMES == frozenset({"pgpassword", "mysql_pwd", "rediscli_auth"})
    near = ["PGPASSWORD", "--pgpassword", "_MYSQL_PWD", "pgpasswords", "MYSQL_PWD_FILE", "x_pgpassword"]
    keys = [*KNOWN_SECRET_NAMES, *(name.upper() for name in KNOWN_SECRET_NAMES), *near, "mysql-pwd", "pwd"]
    for flags in (0, re.IGNORECASE):
        wrong = [k for k in keys if bool(re.fullmatch(SECRET_KEY_PATTERN, k, flags)) != is_sensitive_key(k)]
        assert not wrong, (flags, wrong)


@pytest.mark.parametrize(
    "text",
    [
        f"password = r'{V}'",
        f'SECRET_KEY = b"{V}"',
        f"Login(user='bob', password=b'{V}')",  # a bytes field in a repr (logging, stringify=True)
        f'token = rb"{V}',
        f"--password b'{V}'",
    ],
)
def test_a_string_prefix_is_part_of_the_quoted_value(redactor: Redactor, text: str) -> None:
    assert V not in redactor.redact(text)
