"""Redaction patterns — SECURITY_MODEL §6.2, ADR-030, appendix T12, M.2 (AC-15).

Black-box: through ``Redactor`` (``redact``, ``redact_with_stop``, ``find``, ``redact_obj``),
``is_sensitive_key`` and ``SECRET_KEY_PATTERN`` only. Tests that read the module's private patterns are in
``tests/unit/test_redaction_internals.py``.
"""

from __future__ import annotations

import json
import random
import re

import pytest

from app.core.redaction import (
    REDACTED,
    SECRET_KEY_PATTERN,
    SENSITIVE_KEY_TAIL,
    Redactor,
    is_sensitive_key,
)
from tests.timing import best_of

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


def test_longest_literal() -> None:
    """WP-3.1f2c D2-I1 (Lead decision #11): the delta stream holds back at least this many characters less
    one, so a configured literal cut at the end of a streamed text is never sent in part. A literal too short
    to be redacted does not count."""
    assert Redactor().longest_literal == 0
    assert Redactor(["short"]).longest_literal == 0
    assert Redactor(["short", "x" * 9, "y" * 1200]).longest_literal == 1200


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
    redactor = Redactor(["kk-test-secret-literal-0123456789"])
    elapsed, _ = best_of(
        lambda: (redactor.redact("a" * 131_072), redactor.redact("x9+." * 32_768)), under=1.0
    )
    assert elapsed < 1.0, f"{elapsed:.2f} s"


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
    # mixed spellings main's split (a hump only after a lower-case letter or digit) reads as a tail: either
    # split makes a key sensitive (AG-13 F-3, final review of 74bf8d4)
    "APIkey", "PASSword", "PASSWORDs", "SECRETs", "COOKIEs", "dbPASSword", "SECretKey", "dbSecretKEy",
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


# AG-13 F-3 (final review of 74bf8d4): main's is_sensitive_key split a name at a hump after a lower-case
# letter or digit only, so a mixed spelling of a tail (APIkey, PASSword, dbPASSword, PASSWORDs) was one
# sensitive word.
_MAIN_SENSITIVE_NAME = re.compile(rf"(?i)^(?:[a-z0-9]+[_-])*(?:{SENSITIVE_KEY_TAIL})$")


def _main_is_sensitive(name: str) -> bool:
    """main's (8251c84) is_sensitive_key, as it was."""
    return bool(_MAIN_SENSITIVE_NAME.match(re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", name).replace("-", "_")))


def _mixed_spellings() -> list[str]:
    """AG-13's key corpus (poc_predicate.py, 2 645 keys): each tail in mixed spellings, after common
    prefixes."""
    prefixes = ["", "db", "DB", "my", "API", "x", "X", "SMTP", "user", "Aws", "aws_", "x-", "_", "--", "HTTP"]
    keys: set[str] = set()
    for tail in SENSITIVE_KEY_TAIL.replace("s?", "").split("|"):
        word = tail.replace("_", "")
        half = len(word) // 2
        variants = {
            tail, tail.upper(), tail.title().replace("_", ""), word, word.upper(), word.capitalize(),
            tail + "s", (tail + "s").upper(), word[:half].upper() + word[half:], word[:3].upper() + word[3:],
            word.upper()[:-1] + word[-1:], word.upper() + "s",
        }  # fmt: skip
        for variant in variants:
            for prefix in prefixes:
                keys.add(prefix + variant)
                if prefix and prefix[-1].isalpha():
                    keys.add(prefix + variant[0].upper() + variant[1:])
    return sorted(keys)


def test_no_key_main_called_sensitive_is_readable(redactor: Redactor) -> None:
    corpus = _mixed_spellings()
    assert len(corpus) == 2_645
    lost = [key for key in corpus if _main_is_sensitive(key) and not is_sensitive_key(key)]
    assert not lost, lost
    for flags in (0, re.IGNORECASE):  # free text agrees, as for every other key
        wrong = [k for k in corpus if bool(re.fullmatch(SECRET_KEY_PATTERN, k, flags)) != is_sensitive_key(k)]
        assert not wrong, (flags, wrong)
    for key in ["APIkey", "PASSword", "PASSWORDs", "SECRETs", "COOKIEs", "dbPASSword"]:
        assert redactor.redact_obj({key: SECRET_VALUE}) == {key: REDACTED}, key
        assert SECRET_VALUE not in redactor.redact(f"{key}={SECRET_VALUE}"), key


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


# AG-13 F-1 (final review of 74bf8d4): single-quoted shell, YAML, INI and PowerShell strings read no "\" as an
# escape. A value whose escape-aware close is on a later line ends at a plain close on its own line.
BACKSLASH_BEFORE_A_CLOSE = {
    "shell single quotes": (
        f"export DB_PASSWORD='aB3\\'\nexport GITHUB_TOKEN='{V}'\n",
        f"export DB_PASSWORD={REDACTED}\nexport GITHUB_TOKEN={REDACTED}\n",
    ),
    "yaml single quotes": (
        f"db:\n  password: 'p4ss\\'\n  api_key: '{V}'\n",
        f"db:\n  password: {REDACTED}\n  api_key: {REDACTED}\n",
    ),
    "double quotes": (f'PASSWORD="ab\\"\nTOKEN="{V}"\n', f"PASSWORD={REDACTED}\nTOKEN={REDACTED}\n"),
    "windows path in ini": (
        f"[db]\npassword = 'C:\\keys\\'\nsecret = '{V}'\n",
        f"[db]\npassword = {REDACTED}\nsecret = {REDACTED}\n",
    ),
    # The escape-aware close on the value's own line still wins (R-6, #8).
    "escaped quote closed on its line": (f'{{"password": "ab\\"{V}"}}', f'{{"password": {REDACTED}}}'),
    "escaped quote and a space, closed on its line": (
        f'{{"password": "ab\\" {V}"}}',
        f'{{"password": {REDACTED}}}',
    ),
    # No escape-aware close anywhere: the quote is never closed, the value runs to the end of the text (#6).
    "never closed after an escaped quote": (f'password="\\"{V}', f"password={REDACTED}"),
    "never closed, single quotes": (f"token='\\'{V}\nmore", f"token={REDACTED}"),
    "never closed, a space after the escaped quote": (f'password="\\" {V}', f"password={REDACTED}"),
    # A plain close glued to a word is no end of a value: the escape-aware close on a later line is taken.
    "plain close glued to a word": (f'Bearer="\\"{V}\n" next', f"Bearer={REDACTED} next"),
}


@pytest.mark.parametrize("form", list(BACKSLASH_BEFORE_A_CLOSE))
def test_a_backslash_before_a_close_on_its_line_ends_the_value_there(redactor: Redactor, form: str) -> None:
    text, expected = BACKSLASH_BEFORE_A_CLOSE[form]
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


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # A key the new key forms recognise used to take the next key as its bare value (redacted on main).
        (f'self._password = secret = "{V}"', f"self._password = secret = {REDACTED}"),
        (f"clientSecret = password = '{V}'", f"clientSecret = password = {REDACTED}"),
        (f"api-key: password {V}", f"api-key: password {REDACTED}"),
        # Raw on main too.
        (f"password: token: {V}", f"password: token: {REDACTED}"),
        (f"password=token= {V}", f"password=token= {REDACTED}"),
        # A flag's bare value and netrc's.
        (f"tool --token password {V}", f"tool --token password {REDACTED}"),
        (f"machine h password password {V}", f"machine h password password {REDACTED}"),
        # A word that is no key, or a key with no value of its own, is still the value.
        (f"password=hunter2 {V}", f"password={REDACTED} {V}"),
        ("--password password", f"--password {REDACTED}"),
        ("token: password", f"token: {REDACTED}"),
        # A word that reads as a key and its separator, with no value of its own after it (a secret glued to
        # "-cookie=" at the end of the text), or with its value glued to it in the same word: the word is the
        # value, taken whole, so nothing of it stays.
        (f"apikey:{V}-cookie=", f"apikey:{REDACTED}"),
        (f"token: {V}-secret: ;", f"token: {REDACTED} ;"),
        ("password: token: ;", f"password: {REDACTED} ;"),
        (f"apikey:{V}-cookie=o", f"apikey:{REDACTED}"),
        (f"password=token={V}", f"password={REDACTED}"),
        # Only a tail alone is refused: a key with words before its tail may be a secret glued to a tail word,
        # which a match of its own would keep (the AG-13 grammar fuzz against main, seed 1). Glued to its
        # separator, such a key takes the value after the spaces with it, so neither stays raw.
        (f"password {V}Cookie: s", f"password {REDACTED} {REDACTED}"),  # the camel key takes "s" first
        (f"credential:{V}_secret: Q", f"credential:{REDACTED}"),
        (f"apikey:{V}-cookie= o", f"apikey:{REDACTED}"),
        (f"token: x-apikey: {V}", f"token: {REDACTED}"),
        ("xoxb-" + f"GJj206odyX=B_PASSWORD: {V}", f"{REDACTED}={REDACTED}"),  # after a token (#7)
        # netrc's key after dashes is netrc's key too (_NETRC reads a "-" before it).
        (f"_password=-Password {V}", f"_password=-Password {REDACTED}"),
    ],
    ids=[
        "private-name",
        "camel",
        "kebab-netrc",
        "key-colon",
        "key-equals",
        "flag",
        "netrc",
        "no-key",
        "flag-word",
        "key-alone",
        "key-without-a-value-at-the-end",
        "key-without-a-value",
        "tail-without-a-value",
        "key-and-value-in-one-word",
        "glued-key-equals",
        "secret-before-a-camel-tail",
        "secret-before-a-snake-tail",
        "secret-before-a-kebab-tail",
        "compound-key-takes-its-value",
        "compound-upper-key-takes-its-value",
        "netrc-after-a-dash",
    ],
)
def test_a_value_never_takes_a_key_with_its_own_value(redactor: Redactor, text: str, expected: str) -> None:
    """AG-13 F-2 (final review of 74bf8d4): a bare value (also a flag's and netrc's) never takes a word that
    is itself a secret key followed by its separator, or netrc's key followed by a word: that key gets its
    own match, so its value is redacted."""
    assert redactor.redact(text) == expected


def test_a_value_after_a_token_is_redacted_once(redactor: Redactor) -> None:
    assert redactor.redact("ghp_" + "A1b2C3d4E5" * 4 + f'password="{V}"') == f"{REDACTED}={REDACTED}"
    # A token in a list or a JSON value is followed by no assignment: nothing more is removed.
    token = "ghp_" + "a" * 36
    assert redactor.redact(f'["{token}", "kept"]') == f'["{REDACTED}", "kept"]'
    assert redactor.redact(f'{{"a": "{token}", "b": "kept"}}') == f'{{"a": "{REDACTED}", "b": "kept"}}'


@pytest.mark.parametrize(
    "text",
    ['password=abc:b"VVVVVVVVVV"', "token=x=rb'VVVVVVVVVV'", 'password=abc:B"VV VV"', "secret=a:u'VV'"],
)
def test_a_bare_value_ending_in_a_separator_takes_a_prefixed_string(redactor: Redactor, text: str) -> None:
    """Review of 50610b5 (#7): the string after a bare value that ends in ":" or "=" is swallowed also when a
    string prefix (b"…", rb'…') is glued to the separator."""
    out = redactor.redact(text)
    assert "V" not in out, out


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


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["mysql", "--password", 123456], ["mysql", "--password", REDACTED]),
        (["--password", [V]], ["--password", REDACTED]),
        (("--token", (V,)), ("--token", REDACTED)),
        (["--password", {"value": V}], ["--password", REDACTED]),
        (["--password", b"hunter2hunter2"], ["--password", REDACTED]),
        # A flag that ends in "=" with nothing after it: its value is the next item.
        (["--password=", V], ["--password=", REDACTED]),
        # None is no value; neither is what follows it.
        (["--password", None, "next"], ["--password", None, "next"]),
        ([f"--password={V}", "next"], [f"--password={REDACTED}", "next"]),
    ],
    ids=["int", "list", "tuple", "dict", "bytes", "flag=", "none", "flag=value"],
)
def test_redact_obj_drops_any_value_after_a_secret_flag(argv: object, expected: object) -> None:
    """Silent-failure review MEDIUM-1 (final review of 74bf8d4): after a secret flag any item but None is
    dropped whole, a nested container too, also with ``stringify=False``. ToolGateway refuses an R3 input that
    ``redact_obj`` changes (``secret_in_input``)."""
    assert Redactor().redact_obj(argv) == expected


def test_redact_obj_reads_a_bytes_key() -> None:
    """Silent-failure review LOW-1: a bytes key is read as text (UTF-8, errors replaced) for the check; the
    key itself is kept as it is."""
    redactor = Redactor()
    assert redactor.redact_obj({b"password": V, b"note": "kept"}) == {b"password": REDACTED, b"note": "kept"}
    assert redactor.redact_obj({b"\xffpassword": V}) == {b"\xffpassword": V}  # U+FFFD is part of the name
    assert redactor.redact_obj({b"api_key": None, 7: V}) == {b"api_key": None, 7: V}


def test_a_secret_flag_as_a_value_takes_the_word_after_it(redactor: Redactor) -> None:
    # "--no-password" takes the next word (Lead ruling #15). When that word is itself a secret flag, its own
    # value follows it: both go (Lead ruling on the Task 2 review, Important 1).
    assert redactor.redact(f"mysql --no-password --password {V}") == f"mysql --no-password {REDACTED}"
    assert redactor.redact(f"tool --skip-password --token {V} --verbose") == (
        f"tool --skip-password {REDACTED} --verbose"
    )
    assert V not in redactor.redact(f'["--no-password", "--token", "{V}"]')
    assert V not in redactor.redact(f'"--no-password" "--token" "{V}"')
    assert redactor.redact_obj(["--no-password", "--password", V]) == ["--no-password", REDACTED, REDACTED]
    assert redactor.redact_obj(["--no-password", "--password", V, "-v"]) == [
        "--no-password",
        REDACTED,
        REDACTED,
        "-v",
    ]
    assert redactor.redact_obj(["--token", "--token"]) == ["--token", REDACTED]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("mysql --password -MyPassword", f"mysql --password {REDACTED}"),  # redacted at 70f88a1
        ("password -abcSecret", f"password {REDACTED}"),  # netrc; redacted at ad6303b
        ("machine h login u password -abcSecret extra", f"machine h login u password {REDACTED}"),
        ('["mysql", "--password", "-MyPassword"]', f'["mysql", "--password", {REDACTED}]'),
        ("'--password' -MyPassword", f"'--password' {REDACTED}"),
    ],
)
def test_a_secret_that_looks_like_a_secret_flag_is_still_redacted(
    redactor: Redactor, text: str, expected: str
) -> None:
    # A password that starts with "-" and ends in a tail word reads as a secret flag. Taken as a value, it is
    # redacted, never skipped (Task 2 review, Important 1).
    assert redactor.redact(text) == expected
    assert redactor.redact_obj(["mysql", "--password", "-MyPassword"]) == ["mysql", "--password", REDACTED]


@pytest.mark.parametrize(
    "text",
    [
        'parser.add_argument("--token", help="The API token")',
        'parser.add_argument("--password", required=True)',
        '@click.option("--password", prompt=True, hide_input=True)',
        'cmd = ["--api-key", api_key]',
        'cmd = ["--api-key", api_key, "--verbose"]',
    ],
)
def test_code_that_defines_a_secret_flag_stays_readable(redactor: Redactor, text: str) -> None:
    # After a comma an argv value is a quoted string; a bare word there is code (Task 2 review, Important 2).
    assert redactor.redact(text) == text


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (f'["mysql", "--password", "{V}"]', f'["mysql", "--password", {REDACTED}]'),
        (f'"--password" {V}', f'"--password" {REDACTED}'),
        (f"'--password' {V} --verbose", f"'--password' {REDACTED} --verbose"),
        (f'"--password" "{V}"', f'"--password" {REDACTED}'),
    ],
)
def test_a_quoted_flag_takes_a_quoted_item_or_a_shell_word(
    redactor: Redactor, text: str, expected: str
) -> None:
    assert redactor.redact(text) == expected


def test_a_docstring_after_a_secret_parameter_stays_readable(redactor: Redactor) -> None:
    # The #7 swallow takes a quoted string that immediately follows, on the same line (Task 2 review,
    # Important 3).
    text = 'def connect(host, password=None):\n    """Open a connection to the database."""\n'
    out = redactor.redact(text)
    assert '"""Open a connection to the database."""' in out, out
    assert V not in redactor.redact(f'password : password:\t"{V}"')
    assert V not in redactor.redact(f'password : password:  "{V}"')


def test_a_flag_value_is_one_word_on_its_line(redactor: Redactor) -> None:
    # argparse help: the metavar after a flag is its "value"; the help text after it stays readable.
    assert redactor.redact("--token TOKEN    The API token") == f"--token {REDACTED}    The API token"
    assert redactor.redact("  --github-token TOKEN\n      GitHub token for the API") == (
        f"  --github-token {REDACTED}\n      GitHub token for the API"
    )
    assert redactor.redact("tool --token bearer\n--verbose") == f"tool --token {REDACTED}\n--verbose"
    # A scheme word and its credential share a line in key=value too.
    assert redactor.redact("token: Bearer\nnext_line") == f"token: {REDACTED}\nnext_line"


# AG-13 F-8 (final review of 74bf8d4): ToolGateway._content_text json-dumps a dict output before redacting it,
# and logging's stringify uses repr: a line break or tab before a key is then "\n", "\r" or "\t", glued to it.
ESCAPED_BREAK_OUTPUTS = {
    "key=value": {"stdout": f"connecting\npassword={V}\n"},
    "flag": {"stdout": f"x\n--token {V}\n"},
    "camel key, quoted": {"stdout": f'x\nclientSecret: "{V}"\n'},
    "tab before a key": {"content": f"[db]\n\tsecret = '{V}'\n"},
    "netrc after CRLF": {"stdout": f"machine h login u\r\npassword {V}\r\n"},
}


@pytest.mark.parametrize("output", list(ESCAPED_BREAK_OUTPUTS))
def test_a_key_after_an_escaped_line_break_is_redacted(redactor: Redactor, output: str) -> None:
    value = ESCAPED_BREAK_OUTPUTS[output]
    for text in (json.dumps(value), repr(value)):
        out = redactor.redact(text)
        assert V not in out, out
        assert "credential_assignment" in {finding.rule for finding in redactor.find(text)}


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


KNOWN_NAMES = ["pgpassword", "mysql_pwd", "rediscli_auth"]  # tests/unit/test_redaction_internals.py pins them


def test_known_secret_names_are_exact_names_in_both_layers() -> None:
    near = ["PGPASSWORD", "--pgpassword", "_MYSQL_PWD", "pgpasswords", "MYSQL_PWD_FILE", "x_pgpassword"]
    keys = [*KNOWN_NAMES, *(name.upper() for name in KNOWN_NAMES), *near, "mysql-pwd", "pwd"]
    assert all(is_sensitive_key(name) for name in KNOWN_NAMES)
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


@pytest.mark.parametrize(
    "text",
    [
        'Authorization: Token token="V"',
        'Cookie: session="V"; theme=dark',
        'x-api-key: password="V"',
        "Authorization: Token token='V'",
        'Proxy-Authorization: Digest username="u", response="V"',
    ],
)
def test_a_header_value_keeps_its_quoted_parts(redactor: Redactor, text: str) -> None:
    out = redactor.redact(text)
    assert "V" not in out, out
    assert out.split(":", 1)[0] == text.split(":", 1)[0]


def test_a_quoted_header_value_ends_at_its_closing_quote(redactor: Redactor) -> None:
    out = redactor.redact('{"Authorization": "Bearer V", "next": "kept"}')
    assert "V" not in out, out
    assert '"next": "kept"' in out, out
    escaped = redactor.redact(r"{\"Authorization\": \"Bearer V\", \"next\": \"kept\"}")
    assert "Bearer V" not in escaped and "kept" in escaped, escaped


def test_a_header_value_stays_on_its_line(redactor: Redactor) -> None:
    """Outside a parameter's quoted value a header value keeps to its line (a parameter's quote, after "=",
    may close on a later line: test_a_header_parameter_quote_closes_later_or_never)."""
    out = redactor.redact('Cookie: a b"c\nnext line "kept"')
    assert "next line" in out and "kept" in out, out
    assert "next line" in redactor.redact("Cookie: abc\nnext line")


@pytest.mark.parametrize(
    "text",
    [
        'Cookie: session="VVVVVVVVVV',
        'Authorization: Token token="VVVVVVVVVV',
        'Cookie: session="VV\nVV"',
        "Proxy-Authorization: Digest response='VV\nVV' qop=auth",
        'x-api-key: a=1; password="VVVVVVVVVV',
    ],
)
def test_a_header_parameter_quote_closes_later_or_never(redactor: Redactor, text: str) -> None:
    """Review of 50610b5 (D2-I2, #12): a quote that opens a header parameter's value is read as #6 reads a
    key's quoted value. One never closed takes the rest of the text, so a header value cut by max_tokens never
    goes out raw at flush; one closed on a later line is taken whole."""
    out = redactor.redact(text)
    assert "V" not in out, out
    assert out.startswith(text.split(":", 1)[0] + ": "), out


@pytest.mark.parametrize(
    "text",
    [
        'curl -H "Authorization: Basic dXNlcjpwYXNz=" https://api.example.com/x\nThen run "make test" now.',
        'curl -H "Cookie: sid=abc==" https://api.example.com/x\nThen run "make test".',
    ],
)
def test_a_quote_after_base64_padding_closes_the_shell_word(redactor: Redactor, text: str) -> None:
    """Lead correction (review of 0733969): "=" then a quote opens a parameter's value only when a character
    that is neither a space nor a quote follows; after base64 padding the quote closes the shell word."""
    out = redactor.redact(text)
    assert 'https://api.example.com/x\nThen run "make test"' in out, out
    assert "dXNlcjpwYXNz" not in out and "abc" not in out, out


def test_an_empty_header_parameter_takes_nothing_after_it(redactor: Redactor) -> None:
    out = redactor.redact('Cookie: token=""\nnext "line" kept')
    assert out.endswith('\nnext "line" kept'), out


def test_a_header_parameter_quote_closed_on_a_later_line_ends_the_value_there(redactor: Redactor) -> None:
    """As #6 for a key's quoted value: the value ends with its line after the closing quote, and the next line
    stays readable."""
    out = redactor.redact('Cookie: session="VV\nVV"; theme=dark\nnext line')
    assert "V" not in out and out.endswith("\nnext line"), out


def test_a_quote_that_closes_a_shell_word_keeps_what_follows(redactor: Redactor) -> None:
    """The quote after a header value in curl -H "..." closes the shell word, not a parameter: it is not
    taken to the end of the text, so the URL stays."""
    out = redactor.redact('curl -H "Authorization: Bearer V" https://api.example.com/x')
    assert "V" not in out.replace("Authorization", ""), out
    assert out.endswith('" https://api.example.com/x'), out


_PGP = "-----BEGIN PGP PRIVATE KEY BLOCK-----\nV\n-----END PGP PRIVATE KEY BLOCK-----"


@pytest.mark.parametrize(
    "text",
    [
        _PGP,
        "-----BEGIN PGP PRIVATE KEY BLOCK-----\nV",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nV\n-----END OPENSSH PRIVATE KEY-----",
        "-----BEGIN ENCRYPTED PRIVATE KEY-----\nV\n-----END ENCRYPTED PRIVATE KEY-----",
        "-----BEGIN EC2 PRIVATE KEY-----\nV",
    ],
)
def test_every_private_key_block_is_redacted(redactor: Redactor, text: str) -> None:
    out = redactor.redact(f"before\n{text}")
    assert out.startswith("before\n") and "V" not in out.removeprefix("before\n"), out
    assert [finding.rule for finding in redactor.find(text)] == ["private_key"]


# --- redact_with_stop: the delta stream's redaction (reviews of 45c2ec7 and 08fa05e) ---

# Characters a case-insensitive pattern folds onto ASCII letters: long s, Kelvin sign, dotless i, dotted I.
LONG_S, KELVIN, DOTLESS_I, DOTTED_I = "\u017f", "\u212a", "\u0131", "\u0130"
FUZZ_PIECES = [
    "password", "PASSWORD", f"pa{LONG_S}{LONG_S}word", "db_password", "dbPassword", "clientSecret",
    "secretKey", "SECRET_KEY", "x-api-key", "apiKey", "token", "toKen", "tokens", "token_count", "sessionId",
    "userSession", "Cookie", "set-cookie", "Authorization", "jwt", "bearer", "ticket", "credentials",
    "passwd", "PGPASSWORD",
    "MYSQL_PWD", "rediscli_auth", "_password", "--password", "-api-key", "--no-password", f"{LONG_S}ecret",
    "monkey", "oauth", "author", "S3SecretKey", "db2Password", "x", "note",
    "=", " = ", ": ", ":", " ", "\t", "\n", "\r\n", '": "', '="', "='", '\\": \\"', ", ", "&", "?", "-",
    "_", "[", '"', "'", "\\", "V", "hunter2", '"abc"', "'abc'", '"""t', "b'xy'", 'r"raw"', "Bearer abc",
    "-MyPassword", "[REDACTED]", "[redacted]=v", "ghp_" + "a1B2" * 9, "sk-ant-api03-" + "A" * 12,
    "AKIA" + "ABCDEFGHIJKLMNOP", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmN",
    "-eyJabcde.eyJfghij.klmnopqrstuv", "https://user:pw@host/x", "ftp://" + "Pq9" * 4 + "@h", "://",
    "-----BEGIN RSA PRIVATE KEY-----\nMII", KELVIN, DOTLESS_I, DOTTED_I, "\u00e9",
]  # fmt: skip


def test_redact_with_stop_changes_no_output() -> None:
    """Review round 3 of 45c2ec7: the delta stream's redaction is redact's: the same rules, in one order."""
    rnd = random.Random(53)  # noqa: S311 - a reproducible fuzz, not a secret
    pieces = [*FUZZ_PIECES, 'Cookie: a="x', 'z"', "password Cookie:", "password=q=", '"--password", ']
    for literals in ([], ['Pa55"word9']):
        redactor = Redactor(literals)
        for _ in range(3_000):
            text = "".join(rnd.choice(pieces) for _ in range(rnd.randint(1, 16)))
            assert redactor.redact_with_stop(text)[0] == redactor.redact(text), text


@pytest.mark.parametrize(
    ("text", "stop"),
    [
        ('Cookie: a b"q', len("Cookie: ")),  # nothing after the header rule changed it: the marker itself
        ('x: 1\nCookie: a="x\ny" z"q', len("x: 1\nCookie: ")),  # carried onto a later line by a parameter
        ('password: "Cookie: a="x\ny" z"q', 0),  # a key's quoted value paired the stop: its last line
        ('note\npassword Cookie:a="x\ny" z" q', len("note\n")),  # netrc took the marker: its last line
        ('Cookie: a b"q\nnext line', None),  # the line ended: the value is final
        ('token = "abc', None),  # no header
        ("Cookie: a b c", None),  # the value runs to the end of its line
    ],
)
def test_redact_with_stop_finds_where_a_header_value_stopped(
    redactor: Redactor, text: str, stop: int | None
) -> None:
    redacted, at = redactor.redact_with_stop(text)
    assert at == stop, (redacted, at)
    if at is not None:
        assert "q" not in redacted[:at], redacted
