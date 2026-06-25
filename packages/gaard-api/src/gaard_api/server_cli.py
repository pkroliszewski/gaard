import argparse
from collections.abc import Sequence
from pathlib import Path

from gaard_api.cli_commands import run_admin
from gaard_api.example_database import (
    install_medical_poc_example_database,
    sqlite_database_url,
)


def run_install_example_database(args: argparse.Namespace) -> None:
    try:
        database_path = install_medical_poc_example_database(
            Path(args.output),
            overwrite=not args.no_overwrite,
        )
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc

    absolute_path = database_path.resolve()
    print(f"Medical POC example database created: {absolute_path}")
    print(f"Datasource URL: {sqlite_database_url(absolute_path)}")
    print("Metadata datasource connector: default")


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

    example_db_parser = subparsers.add_parser(
        "install-example-database",
        aliases=["create-example-database"],
        help="Create the bundled Medical POC SQLite example database.",
    )
    example_db_parser.add_argument(
        "--output",
        default="examples/medical-poc/demo.db",
        help="Output SQLite database path. Defaults to examples/medical-poc/demo.db.",
    )
    example_db_parser.add_argument(
        "--no-overwrite",
        action="store_true",
        help="Fail if the output database already exists.",
    )
    example_db_parser.set_defaults(func=run_install_example_database)

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = create_parser().parse_args(argv)
    args.func(args)
