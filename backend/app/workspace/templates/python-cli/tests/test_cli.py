import pytest

from greet.cli import greeting, main


def test_greeting() -> None:
    assert greeting("Ana") == "Hello, Ana!"
    assert greeting("Ana", shout=True) == "HELLO, ANA!"


def test_main_prints(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--name", "Budi"]) == 0
    assert capsys.readouterr().out == "Hello, Budi!\n"
