import argparse
from collections.abc import Sequence

from gaard_client.cli_commands import add_client_server_arguments, run_client


def create_parser(prog: str = "gaard-client") -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=prog,
        description="Run the GAARD community client.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Start the client server.")
    add_client_server_arguments(start_parser)
    start_parser.set_defaults(func=run_client)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    args.func(args)
