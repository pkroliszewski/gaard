from __future__ import annotations

import uvicorn
from argparse import ArgumentParser, Namespace, _SubParsersAction


def register(subparsers: _SubParsersAction[ArgumentParser]) -> None:
    parser = subparsers.add_parser("admin")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.set_defaults(func=run_admin)


def run_admin(args: Namespace) -> None:
    uvicorn.run(
        "gaard_api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )
