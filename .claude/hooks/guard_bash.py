#!/usr/bin/env python3
"""PreToolUse guard for Bash commands run by Claude Code in this repo.

Blocks the few shell actions that are irreversible or leak secrets
(SECURITY_MODEL §6, RUNBOOK §9, docs/agents/delivery-roster.md rule 5).
Exit code 2 blocks the call and shows the reason to the agent.
"""

from __future__ import annotations

import json
import re
import shlex
import sys

SECRET_FILE = re.compile(
    r"(?:^|/)(?:\.env(?:\.(?!example$)[\w.-]+)?|[\w.-]+\.pem|[\w.-]+\.key|id_rsa[\w.-]*)$"
)
READERS = {"cat", "less", "more", "head", "tail", "bat", "xxd", "od", "strings", "base64", "scp", "curl"}


def reasons(command: str) -> list[str]:
    found: list[str] = []
    for segment in re.split(r"&&|\|\||;|\|", command):
        try:
            argv = shlex.split(segment)
        except ValueError:
            argv = segment.split()
        if not argv:
            continue
        prog = argv[0].rsplit("/", 1)[-1]

        if prog == "git" and "push" in argv:
            args = argv[argv.index("push") + 1 :]
            forced = any(a in ("--force", "-f") or (a.startswith("-") and not a.startswith("--") and "f" in a) for a in args)
            plus_refspec = any(a.startswith("+") for a in args)
            if forced or plus_refspec:
                found.append("git push --force/-f/+refspec rewrites shared history; use --force-with-lease on your own branch only after asking the user.")
        if prog == "git" and "reset" in argv and "--hard" in argv and any(a.startswith(("origin/", "main")) for a in argv):
            found.append("git reset --hard onto a shared ref discards work; ask the user first.")
        if prog == "docker" and "prune" in argv and "--volumes" in argv:
            found.append("docker ... prune --volumes deletes database and workspace volumes (RUNBOOK §9).")
        if prog in READERS and any(SECRET_FILE.search(a) for a in argv[1:]):
            found.append("Reading secret files (.env, *.pem, *.key) is not allowed; use .env.example or ask the operator.")
    return found


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    blocked = reasons(command)
    if blocked:
        print("Blocked by .claude/hooks/guard_bash.py:\n- " + "\n- ".join(blocked), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
