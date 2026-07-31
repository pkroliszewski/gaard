from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from gaard_core.errors import GaardError


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(GaardError)
    async def handle_gaard_error(request: Request, exc: GaardError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "code": exc.code,
                    "message": exc.message,
                }
            },
        )