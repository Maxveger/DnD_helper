"""Loopback-only web application for the game studio."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .config import data_directory
from .rules import GameError
from .service import Service

STATIC = Path(__file__).parent / "static"


class Command(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    data: dict[str, Any] = Field(default_factory=dict)
    command_id: str = Field(min_length=1, max_length=100)
    revision: int | None = None


class ModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(max_length=100)


def create_app(directory=None, background=True, shutdown=None):
    service = Service(Path(directory) if directory else data_directory())
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        if background:
            service.start()
        yield
        if background:
            service.stop()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.service = service
    app.state.csrf = token

    @app.middleware("http")
    async def local_access(request, call_next):
        host = request.url.hostname
        if host not in {"127.0.0.1", "localhost", "::1"}:
            return JSONResponse({"error": "Допускается только локальный доступ."}, status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return JSONResponse({"error": "Недопустимый источник запроса."}, status_code=403)
        origin = request.headers.get("origin")
        if origin and origin != str(request.base_url).rstrip("/"):
            return JSONResponse({"error": "Недопустимый источник запроса."}, status_code=403)
        if request.url.path.startswith("/api/") and not secrets.compare_digest(
            request.headers.get("x-dnd-token", ""), token
        ):
            return JSONResponse({"error": "Обновите страницу приложения."}, status_code=403)
        if request.headers.get("content-length", "0").isdigit() and int(
            request.headers.get("content-length", "0")
        ) > 100_000:
            return JSONResponse({"error": "Запрос слишком большой."}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "Referrer-Policy": "no-referrer",
                "X-Frame-Options": "DENY",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @app.exception_handler(GameError)
    async def game_error(request, exc):
        return JSONResponse({"error": str(exc)}, status_code=409)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"error": "Неверный формат запроса."}, status_code=422)

    def studio_html():
        return HTMLResponse((STATIC / "studio.html").read_text("utf-8").replace("__CSRF__", token))

    app.get("/")(studio_html)
    app.get("/studio")(studio_html)

    @app.get("/health")
    def health():
        return {"app": "dnd-helper-studio", "version": "0.2.0", "ok": True}

    @app.get("/api/studio")
    def studio_state():
        return service.studio.view()

    @app.post("/api/studio/model")
    def studio_model(body: ModelInput):
        return service.studio.set_model(body.model)

    @app.post("/api/studio/command")
    def studio_command(body: Command):
        if service.studio.store.read() and body.revision is None:
            raise GameError("Обновите страницу перед командой.")
        return service.studio.command(body.kind, body.data, body.command_id, body.revision)

    @app.get("/api/codex/status")
    def codex_status():
        return service.studio.provider.status()

    @app.post("/api/codex/login")
    def codex_login():
        try:
            return service.studio.provider.login()
        except (OSError, ValueError) as exc:
            raise GameError("Не удалось открыть вход Codex. Выполните codex login в терминале.") from exc

    @app.post("/api/shutdown")
    def stop():
        if shutdown:
            shutdown()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
