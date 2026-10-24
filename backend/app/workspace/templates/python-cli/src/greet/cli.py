"""Command line entry point."""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="greet", description="Print a greeting.")
    parser.add_argument("--name", default="World", help="who to greet")
    parser.add_argument("--shout", action="store_true", help="print in upper case")
    return parser


def greeting(name: str, *, shout: bool = False) -> str:
    text = f"Hello, {name}!"
    return text.upper() if shout else text


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    print(greeting(args.name, shout=args.shout))
    return 0
