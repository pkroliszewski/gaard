import argparse
from collections.abc import Sequence

from gaard_api.cli_commands import run_admin


def create_parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the GAARD API and admin application.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the API server.")
    start_parser.add_argument("--host", default="127.0.0.1")
    start_parser.add_argument("--port", type=int, default=8000)
    start_parser.add_argument("--reload", action="store_true")
    start_parser.set_defaults(func=run_admin)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    args.func(args)
