from contextlib import asynccontextmanager
from pathlib import Path
from uuid import UUID

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.campaigns import CampaignCreator, CreationConflict
from app.config import Settings
from app.database import build_database
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Operation
from app.schemas import CampaignCreate, CreationResult, ReferenceData


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = build_database(settings.database_url)
    client = KeitaroClient(settings, transport=transport)
    creator = CampaignCreator(client, sessions)

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
                session.scalar(select(func.count()).select_from(Operation))
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

    return app
