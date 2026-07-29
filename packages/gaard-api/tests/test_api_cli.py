from __future__ import annotations

from gaard_api import cli
from gaard_api.cli_commands import register as register_admin


class FakeEntryPoint:
    def __init__(self, name: str, register_func) -> None:
        self.name = name
        self._register_func = register_func

    def load(self):
        return self._register_func


def test_gaard_cli_ignores_duplicate_command_entry_points(monkeypatch) -> None:
    monkeypatch.setattr(
        cli,
        "entry_points",
        lambda group: [
            FakeEntryPoint("admin", register_admin),
            FakeEntryPoint("admin", register_admin),
        ],
    )

    args = cli.create_parser().parse_args(["admin"])

    assert args.command == "admin"
    assert args.host == "127.0.0.1"
