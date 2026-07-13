from __future__ import annotations

import os
from argparse import ArgumentParser, ArgumentTypeError, Namespace, _SubParsersAction
from urllib.parse import urlparse

import uvicorn


def normalize_api_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlparse(normalized)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ArgumentTypeError("API URL must be an absolute http:// or https:// URL.")

    return normalized


def add_client_server_arguments(parser: ArgumentParser) -> None:
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument(
        "--api-url",
        "--backend-url",
        dest="api_url",
        type=normalize_api_url,
        default=None,
        help="GAARD API URL used by the client server, e.g. http://localhost:8000.",
    )


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser("client")
    add_client_server_arguments(parser)
    parser.set_defaults(func=run_client)


def run_client(args: Namespace) -> None:
    api_url = getattr(args, "api_url", None)
    if api_url:
        os.environ["GAARD_CLIENT_BACKEND_URL"] = api_url

    uvicorn.run(
        "gaard_client.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
