import argparse
from importlib.metadata import entry_points


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gaard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    registered_commands: set[str] = set()

    for ep in entry_points(group="gaard.commands"):
        if ep.name in registered_commands:
            continue

        command_func = ep.load()
        command_func(subparsers)
        registered_commands.add(ep.name)

    return parser


def main() -> None:
    parser = create_parser()

    args = parser.parse_args()

    args.func(args)
