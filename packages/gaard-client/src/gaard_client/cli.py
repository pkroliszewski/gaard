import argparse
from collections.abc import Sequence

from gaard_client.cli_commands import run_client


def create_parser(prog: str = "gaard-client") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the GAARD community client.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the client server.")
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--port", type=int, default=8001)
    start_parser.add_argument("--reload", action="store_true")
    start_parser.set_defaults(func=run_client)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    args.func(args)
