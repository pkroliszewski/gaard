import argparse
from importlib.metadata import entry_points


def main() -> None:
    parser = argparse.ArgumentParser(prog="gaard")
    subparsers = parser.add_subparsers(dest="command", required=True)

    commands = {}

    for ep in entry_points(group="gaard.commands"):
        command_func = ep.load()
        commands[ep.name] = command_func
        command_func(subparsers)

    args = parser.parse_args()

    if args.command in commands:
        args.func(args)