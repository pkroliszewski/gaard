from argparse import ArgumentParser, Namespace
from typing import Any

import pytest

from gaard_client import cli_commands
from gaard_client.cli import create_parser
from gaard_client.cli_commands import register, run_client


def test_client_cli_parses_start_defaults() -> None:
    args = create_parser().parse_args(["start"])

    assert args.command == "start"
    assert args.host == "127.0.0.1"
    assert args.port == 8001
    assert args.reload is False
    assert args.api_url is None


def test_client_cli_parses_start_options() -> None:
    args = create_parser().parse_args(
        [
            "start",
            "--host",
            "0.0.0.0",
            "--port",
            "9001",
            "--reload",
            "--api-url",
            "http://api.example/",
        ]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9001
    assert args.reload is True
    assert args.api_url == "http://api.example"


def test_gaard_client_command_parses_api_url_alias() -> None:
    parser = ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    register(subparsers)

    args = parser.parse_args(["client", "--backend-url", "https://api.example/v1/"])

    assert args.command == "client"
    assert args.api_url == "https://api.example/v1"


def test_client_cli_rejects_relative_api_url() -> None:
    with pytest.raises(SystemExit):
        create_parser().parse_args(["start", "--api-url", "localhost:8000"])


def test_run_client_sets_backend_url_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_run(app: str, **kwargs: Any) -> None:
        captured["app"] = app
        captured["kwargs"] = kwargs

    monkeypatch.delenv("GAARD_CLIENT_BACKEND_URL", raising=False)
    monkeypatch.setattr(cli_commands.uvicorn, "run", fake_run)

    run_client(
        Namespace(
            host="127.0.0.1",
            port=9001,
            reload=True,
            api_url="http://api.example",
        )
    )

    assert captured == {
        "app": "gaard_client.main:app",
        "kwargs": {
            "host": "127.0.0.1",
            "port": 9001,
            "reload": True,
        },
    }
    assert cli_commands.os.environ["GAARD_CLIENT_BACKEND_URL"] == "http://api.example"
