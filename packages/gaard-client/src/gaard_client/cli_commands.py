import uvicorn

def register(subparsers):
    parser = subparsers.add_parser("client")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument("--reload", action="store_true")
    parser.set_defaults(func=run_client)


def run_client(args):
    uvicorn.run(
        "gaard_client.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )