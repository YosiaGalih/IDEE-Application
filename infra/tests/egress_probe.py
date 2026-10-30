"""Live egress check, run inside a container attached to kk-egress, the way sandbox-manager attaches a task
(spec WP-1.5b; ADR-007, DEPLOYMENT §3). Stdlib only; exit 1 on any failed check.

    docker run --rm --network kk-egress -v "$PWD/infra/tests/egress_probe.py:/probe.py:ro" \\
        --entrypoint python kodekantor/sandbox-python:1 /probe.py [--strict-tls]

Without --strict-tls an allowed host passes once the proxy opens the tunnel. That is enough where a
TLS-intercepting network sits behind the proxy (the cloud dev container). With it, the registry's real
certificate must verify and the registry must answer, as on a CI runner.
"""

from __future__ import annotations

import http.client
import socket
import sys

PROXY = ("egress-proxy", 3128)
ALLOWED = ("pypi.org", "files.pythonhosted.org", "registry.npmjs.org")
REFUSED_CONNECT = (
    ("github.com", 443),  # not on the allowlist
    ("evilpypi.org", 443),  # suffix look-alike
    ("pypi.org.evil.com", 443),  # prefix look-alike
    ("151.101.0.223", 443),  # raw IP, even of a registry CDN
    ("pypi.org", 80),  # allowed host, wrong port
    ("pypi.org", 22),
)


def tunnel(host: str, port: int) -> str:
    """'open' when the proxy accepts CONNECT, else the proxy's refusal."""
    conn = http.client.HTTPConnection(*PROXY, timeout=20)
    conn.set_tunnel(host, port)
    try:
        conn.connect()
        return "open"
    except OSError as exc:
        return str(exc)
    finally:
        conn.close()


def registry_answers(host: str) -> str:
    """TLS through the tunnel with the default trust store; 'ok' when the registry answers below 500."""
    conn = http.client.HTTPSConnection(*PROXY, timeout=30)
    conn.set_tunnel(host, 443)
    try:
        conn.request("GET", "/", headers={"User-Agent": "kodekantor-egress-probe"})
        status = conn.getresponse().status
        return "ok" if status < 500 else f"HTTP {status}"
    except OSError as exc:
        return str(exc)
    finally:
        conn.close()


def plain_http(url: str) -> int:
    conn = http.client.HTTPConnection(*PROXY, timeout=20)
    try:
        conn.request("GET", url)
        return conn.getresponse().status
    finally:
        conn.close()


def direct(host: str, port: int) -> str:
    try:
        socket.create_connection((host, port), timeout=5).close()
        return "connected"
    except OSError as exc:
        return type(exc).__name__


def main(strict_tls: bool) -> int:
    failures = 0

    def check(name: str, ok: bool, detail: str) -> None:
        nonlocal failures
        failures += not ok
        print(f"{'PASS' if ok else 'FAIL'}  {name:42} {detail}")

    for host in ALLOWED:
        if strict_tls:
            result = registry_answers(host)
            check(f"TLS to {host}:443 via proxy", result == "ok", result)
        else:
            result = tunnel(host, 443)
            check(f"CONNECT {host}:443", result == "open", result)
    for host, port in REFUSED_CONNECT:
        result = tunnel(host, port)
        check(f"CONNECT {host}:{port} refused", "403" in result, result)
    status = plain_http("http://pypi.org/simple/")
    check("plain HTTP to an allowed host refused", status == 403, f"HTTP {status}")
    for host, port in (("pypi.org", 443), ("1.1.1.1", 443)):
        result = direct(host, port)
        check(f"no direct route to {host}:{port}", result != "connected", result)
    print(f"egress probe: {failures} failed" if failures else "egress probe: all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(strict_tls="--strict-tls" in sys.argv[1:]))
