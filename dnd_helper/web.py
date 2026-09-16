"""Loopback-only web application. Mutations require a process-local CSRF token."""

import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .adventures import MAX_DOCUMENT_BYTES, author_kit, validate_document
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


class WorldModelInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: str = Field(max_length=100)


class AdventureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(max_length=MAX_DOCUMENT_BYTES)


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
        if (
            request.headers.get("content-length", "0").isdigit()
            and int(request.headers.get("content-length", "0")) > 3_100_000
        ):
            return JSONResponse({"error": "Запрос слишком большой."}, status_code=413)
        if request.method == "POST":
            body = bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body) > 3_100_000:
                    return JSONResponse({"error": "Запрос слишком большой."}, status_code=413)
            request._body = bytes(body)
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
        # FastAPI's default validation body can echo submitted secret field values.
        return JSONResponse({"error": "Неверный формат запроса."}, status_code=422)

    @app.get("/")
    def index():
        html = (STATIC / "index.html").read_text("utf-8").replace("__CSRF__", token)
        return HTMLResponse(html)

    @app.get("/health")
    def health():
        return {"app": "dnd-helper", "version": "0.1.0", "ok": True}

    @app.get("/world")
    def world_page():
        return HTMLResponse((STATIC / "world.html").read_text("utf-8").replace("__CSRF__", token))

    @app.get("/api/world")
    def world_state():
        return service.free_world.view()

    @app.post("/api/world/model")
    def world_model(body: WorldModelInput):
        return service.free_world.set_model(body.model)

    @app.post("/api/world/validate")
    def world_validate(body: AdventureInput):
        from .world_documents import validation_report

        return validation_report(body.text)

    @app.get("/api/world/author-kit")
    def world_author_kit():
        from .world_documents import author_kit as world_kit

        return world_kit()

    @app.get("/api/world/export")
    def world_export():
        from .world_documents import export_journal

        return export_journal(service.free_world.store.read())

    @app.post("/api/world/command")
    def world_command(body: Command):
        if service.free_world.store.read() and body.revision is None:
            raise GameError("Обновите страницу перед командой.")
        return service.free_world.command(body.kind, body.data, body.command_id, body.revision)

    @app.get("/api/codex/status")
    def codex_status():
        return service.free_world.provider.status()

    @app.post("/api/codex/login")
    def codex_login():
        try:
            return service.free_world.provider.login()
        except (OSError, ValueError) as exc:
            raise GameError("Не удалось открыть вход Codex. Выполните codex login в терминале.") from exc

    @app.get("/api/author-kit")
    def kit():
        return author_kit()

    @app.post("/api/adventures/validate")
    def validate_adventure(body: AdventureInput):
        return validate_document(body.text)[0]

    @app.post("/api/adventures/import")
    def import_adventure(body: AdventureInput):
        return service.adventures.install(body.text)

    @app.get("/api/state")
    def state():
        return service.view()

    @app.post("/api/command")
    def command(body: Command):
        allowed = {
            "new",
            "start",
            "pause",
            "submit",
            "choose",
            "clear_actions",
            "edit",
            "discard",
            "request_roll",
            "roll",
            "accept",
            "manual",
            "scene",
            "undo",
            "finish",
        }
        if body.kind not in allowed:
            raise GameError("Неизвестная команда.")
        if body.kind == "submit":
            service.submit(
                body.data.get("actor"),
                str(body.data.get("text", "")),
                intent=body.data.get("intent"),
                target=body.data.get("target"),
                command_id=body.command_id,
                expected=body.revision,
            )
        else:
            service.engine.command(body.kind, body.data, body.command_id, body.revision)
        return service.view()

    @app.post("/api/settings")
    def settings(values: dict):
        try:
            result = service.config.save(values)
        except (ValidationError, TypeError, ValueError) as exc:
            raise GameError("Проверьте настройки: модель, лимит и формат ключей.") from exc
        return result

    @app.post("/api/rewrite")
    def rewrite(body: Command):
        service.rewrite(body.revision)
        return service.view()

    @app.get("/api/export")
    def export():
        import json

        return Response(
            json.dumps(service.export(), ensure_ascii=False, indent=2),
            media_type="application/json",
            headers={"Content-Disposition": 'attachment; filename="dnd-save.json"'},
        )

    @app.post("/api/shutdown")
    def stop():
        if shutdown:
            shutdown()
        return {"ok": True}

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app
