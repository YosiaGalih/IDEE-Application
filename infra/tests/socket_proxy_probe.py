"""ADR-026 (3) on the running stack (AG-13 PR #23 M2): from where sandbox-manager sits, the socket proxy answers
what the manager needs and refuses the rest with 403. Run inside the sandbox-manager container (clean-install):

    docker compose -f infra/compose.yml --env-file "$KK_ENV_FILE" exec -T sandbox-manager python - < infra/tests/socket_proxy_probe.py

Only GET/POST requests that the proxy decides on by path; none of them changes anything on the host (the denied
POSTs never reach Docker, and the allowed requests are reads). Stdlib only: the manager image has no test tools.
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("PROBE_PROXY_URL") or os.environ["DOCKER_HOST"].replace("tcp://", "http://", 1)

ALLOWED = [("GET", "/_ping"), ("GET", "/version"), ("GET", "/containers/json"), ("GET", "/images/json")]
DENIED = [
    ("GET", "/info"),
    ("GET", "/volumes"),
    ("GET", "/networks"),
    ("GET", "/events"),
    ("GET", "/system/df"),
    ("GET", "/secrets"),
    ("GET", "/plugins"),
    ("POST", "/build"),
    ("POST", "/exec/0123456789ab/start"),
    ("POST", "/swarm/init"),
    ("POST", "/volumes/create"),
    ("POST", "/networks/create"),
]


def status(method: str, path: str) -> int:
    request = urllib.request.Request(BASE + path, method=method, data=b"{}" if method == "POST" else None)
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:  # noqa: S310 - fixed internal URL
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code


def main() -> int:
    failures = []
    for method, path in ALLOWED:
        got = status(method, path)
        print(f"allow {method} {path}: {got}")
        if got != 200:
            failures.append(f"{method} {path} answered {got}, expected 200")
    for method, path in DENIED:
        got = status(method, path)
        print(f"deny  {method} {path}: {got}")
        if got != 403:
            failures.append(f"{method} {path} answered {got}, expected 403")
    for failure in failures:
        print(f"::error::socket proxy: {failure}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
