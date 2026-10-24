"""Safe paths and access policy (SECURITY_MODEL T2, ARCHITECTURE §7, FR-025, FR-028)."""

from __future__ import annotations

import pytest

from app.workspace.policy import Access, Policy

ALL = tuple(Access)
SECRET_DENIED = (Access.USER_WRITE, Access.AGENT_READ, Access.AGENT_WRITE)


def parts(path: str) -> tuple[str, ...]:
    return tuple(path.split("/"))


def expect(path: str, denied: tuple[Access, ...]) -> list[tuple[str, Access, bool]]:
    return [(path, access, access in denied) for access in ALL]


POLICY_CASES = [
    *expect(".git/config", ALL),
    *expect("sub/.git/HEAD", ALL),
    *[
        case
        for secret in (
            ".env",
            "app/.env.local",
            "certs/server.pem",
            "x.key",
            "id_rsa",
            "id_ed25519.pub",
            "keystore.jks",
            ".npmrc",
            ".netrc",
            ".git-credentials",
            ".pypirc",
            "ID_RSA",
        )
        for case in expect(secret, SECRET_DENIED)
    ],
    *expect(".agentignore", (Access.AGENT_WRITE,)),
    *expect("node_modules/a/index.js", (Access.AGENT_READ,)),
    *expect("dist/app.js", (Access.AGENT_READ,)),
    *expect(".venv/lib/x.py", (Access.AGENT_READ,)),
    *expect("__pycache__/m.pyc", (Access.AGENT_READ,)),
    *expect("src/main.py", ()),
]


@pytest.mark.parametrize(("path", "access", "denied"), POLICY_CASES)
def test_policy_matrix(path: str, access: Access, denied: bool) -> None:
    assert Policy.from_agentignore(None).denied(parts(path), access) is denied


AGENTIGNORE = "secrets/\n*.sqlite\n/build-cache\ndocs/**/private.md\n!secrets/ok.txt\n# c\n"


@pytest.mark.parametrize(
    ("path", "matched"),
    [
        ("secrets/a.txt", True),
        ("secrets/ok.txt", True),  # negation is not supported: it cannot re-open a denied path
        ("data/db.sqlite", True),
        ("build-cache/x", True),
        ("docs/a/b/private.md", True),
        ("docs/private.md", True),
        ("x/build-cache/y", False),  # anchored pattern
        ("src/secrets.py", False),
    ],
)
def test_agentignore_patterns(path: str, matched: bool) -> None:
    policy = Policy.from_agentignore(AGENTIGNORE)
    assert policy.denied(parts(path), Access.AGENT_READ) is matched
    assert policy.denied(parts(path), Access.AGENT_WRITE) is matched
    assert policy.denied(parts(path), Access.USER_WRITE) is matched
    assert policy.denied(parts(path), Access.USER_READ) is False


def test_agentignore_ignores_blank_comment_and_negation_lines() -> None:
    policy = Policy.from_agentignore("\n   \n# *.py\n!*.md\n")
    assert not policy.denied(("a.py",), Access.AGENT_READ)
    assert not policy.denied(("a.md",), Access.AGENT_READ)
