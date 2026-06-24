from gaard_client.cli import create_parser


def test_client_cli_parses_start_defaults() -> None:
    args = create_parser().parse_args(["start"])

    assert args.command == "start"
    assert args.host == "127.0.0.1"
    assert args.port == 8001
    assert args.reload is False


def test_client_cli_parses_start_options() -> None:
    args = create_parser().parse_args(
        ["start", "--host", "0.0.0.0", "--port", "9001", "--reload"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 9001
    assert args.reload is True
