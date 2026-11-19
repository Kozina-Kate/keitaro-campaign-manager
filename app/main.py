from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm.exc import StaleDataError

from app.campaigns import CampaignCreator, CreationConflict
from app.config import Settings
from app.database import build_database
from app.editor import CampaignEditor, EditorError
from app.keitaro import KeitaroClient, KeitaroError
from app.models import EditorOffer, EditorStream, Operation
from app.schemas import CampaignCreate, CreationResult, EditorChange, ReferenceData


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = build_database(settings.database_url)
    client = KeitaroClient(settings, transport=transport)
    creator = CampaignCreator(client, sessions)
    editor = CampaignEditor(client, sessions, settings.keitaro_timeout_seconds)

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            await client.close()
            engine.dispose()

    app = FastAPI(title="Keitaro Campaign Manager", version="0.1.0", lifespan=lifespan)
    static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(static / "index.html")

    @app.get("/editor", include_in_schema=False)
    def editor_page():
        return FileResponse(static / "editor.html")

    @app.exception_handler(EditorError)
    async def editor_error_handler(request, error):
        return JSONResponse(
            status_code=error.status_code,
            content={
                "detail": {
                    "code": "editor_conflict"
                    if error.status_code == 409
                    else "editor_error",
                    "message": error.message,
                }
            },
        )

    @app.exception_handler(StaleDataError)
    async def stale_editor_handler(request, error):
        return JSONResponse(
            status_code=409,
            content={
                "detail": {
                    "code": "stale_revision",
                    "message": "Поток изменён в другой вкладке. "
                    "Обновите состояние редактора.",
                }
            },
        )

    @app.exception_handler(CreationConflict)
    async def creation_error_handler(request, error):
        return JSONResponse(
            status_code=error.status_code, content=error.result.model_dump()
        )

    @app.exception_handler(SQLAlchemyError)
    async def database_error_handler(request, error):
        return JSONResponse(
            status_code=503,
            content={
                "detail": {
                    "code": "database_unavailable",
                    "message": "База недоступна. Проверьте миграции и статус операции.",
                }
            },
        )

    @app.exception_handler(KeitaroError)
    async def keitaro_error_handler(request, error):
        # Не передаём клиенту заголовки, ключ или сырое тело ответа Keitaro.
        return JSONResponse(
            status_code=error.status_code,
            content={"detail": {"code": error.code, "message": str(error)}},
        )

    @app.get("/health")
    def health():
        try:
            with sessions() as session:
                # LIMIT 0 проверяет все нужные столбцы без чтения записей.
                for model in (Operation, EditorStream, EditorOffer):
                    session.execute(select(model).limit(0))
        except SQLAlchemyError:
            return JSONResponse(
                status_code=503,
                content={"status": "error", "detail": "Проверьте миграции базы данных"},
            )
        return {"status": "ok"}

    @app.get("/api/reference-data", response_model=ReferenceData)
    async def reference_data():
        return await client.reference_data()

    @app.post("/api/campaigns", response_model=CreationResult, status_code=201)
    async def create_campaign(data: CampaignCreate):
        return await creator.create(data)

    @app.get("/api/operations/{request_id}", response_model=CreationResult)
    def operation_status(request_id: UUID):
        result = creator.get_result(request_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Операция не найдена")
        return result

    @app.post("/api/operations/{request_id}/verify", response_model=CreationResult)
    async def verify_operation(request_id: UUID):
        result = await creator.reconcile(request_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Операция не найдена")
        return result

    @app.get("/api/editor/offers")
    async def search_offers(q: str = Query(default="", max_length=100)):
        rows = await client.get_list("offers")
        query = q.strip().casefold()
        return [
            {"id": row["id"], "name": row["name"]}
            for row in rows
            if row.get("state") == "active"
            and (query in str(row["id"]) or query in row["name"].casefold())
        ][:30]

    @app.get("/api/editor/campaigns/{campaign_id}")
    def cached_editor(campaign_id: int):
        return editor.cached(campaign_id)

    @app.post("/api/editor/campaigns/{campaign_id}/fetch")
    async def fetch_editor(campaign_id: int):
        return await editor.fetch(campaign_id)

    @app.post("/api/editor/streams/{stream_id}/{action}")
    async def change_stream(
        stream_id: int,
        action: Literal[
            "add", "remove", "restore", "pin", "unpin", "cancel", "publish", "reconcile"
        ],
        data: EditorChange,
    ):
        if action == "publish":
            return await editor.publish(stream_id, data.revision)
        if action == "reconcile":
            return await editor.reconcile(stream_id, data.revision)
        return await editor.edit(stream_id, action, data)

    return app
