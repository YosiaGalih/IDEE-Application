"""A password reset token never reaches a log or a Referer (T5, SECURITY_MODEL §6.1; AG-13 review WP-1.4b).

The link is `/reset-password/<token>`. The page replaces its URL at once, but the first request and the
assets it loads before that carry the token: in the request line, and in Referer. Stdlib only; the live
check (nginx and Caddy in containers) is in the tracker, WP-1.4b.

Known limit (AG-13 delta review, LOW): the mask matches the raw `$request_uri`, so a link someone rewrote
(`/%72eset-password/<token>`, `//reset-password/<token>`) is logged as sent. `$uri` cannot be used instead:
`try_files` turns it into `/index.html` before the line is written. The server only issues the canonical
form; the full fix is the token in the URL fragment, an AG-13 condition before any link delivery merges.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = (ROOT / "frontend" / "nginx" / "default.conf").read_text()
CADDY = (ROOT / "infra" / "caddy" / "Caddyfile").read_text()


def _code(text: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in text.splitlines())


class WebAccessLog(unittest.TestCase):
    def test_server_logs_with_the_redacted_format(self) -> None:
        self.assertRegex(_code(NGINX), r"server\s*\{[^}]*access_log\s+/var/log/nginx/access\.log\s+kk_redacted;")

    def test_format_has_no_raw_request_line_and_no_referer(self) -> None:
        match = re.search(r"log_format\s+kk_redacted\s+(.*?);", _code(NGINX), re.S)
        assert match is not None
        fields = match.group(1)
        self.assertNotIn("$http_referer", fields)
        self.assertNotRegex(fields, r"\$request\b")  # $request is the raw line with the token
        self.assertNotIn("$request_uri", fields)
        self.assertNotIn("$uri", fields)
        self.assertIn("$kk_logged_uri", fields)

    def test_reset_paths_are_masked_whatever_the_case(self) -> None:
        match = re.search(r"map\s+\$request_uri\s+\$kk_logged_uri\s*\{(.*?)\}", _code(NGINX), re.S)
        assert match is not None
        self.assertRegex(match.group(1), r'"~\*\^/reset-password/"\s+"/reset-password/\[redacted\]";')
        self.assertRegex(match.group(1), r"default\s+\$request_uri;")


class ResetPageReferrer(unittest.TestCase):
    def test_reset_page_sends_no_referrer(self) -> None:
        code = _code(CADDY)
        self.assertRegex(code, r"@reset_password\s+path\s+/reset-password\s+/reset-password/\*")
        self.assertRegex(code, r'header\s+@reset_password\s+Referrer-Policy\s+"no-referrer"')

    def test_global_policy_is_only_a_default(self) -> None:
        # The snippet's header block is deferred (it deletes Server): a plain value would overwrite the
        # reset page's stricter one when the response is written.
        code = _code(CADDY)
        self.assertRegex(code, r'\?Referrer-Policy\s+"same-origin"')
        self.assertNotRegex(code, r'(?<![?\w])Referrer-Policy\s+"same-origin"')


if __name__ == "__main__":
    unittest.main()
