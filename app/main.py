from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from app.config import Settings
from app.database import build_database
from app.keitaro import KeitaroClient, KeitaroError
from app.models import Operation
from app.schemas import ReferenceData


def create_app(settings: Settings | None = None, transport=None) -> FastAPI:
    settings = settings or Settings()
    engine, sessions = build_database(settings.database_url)
    client = KeitaroClient(settings, transport=transport)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await client.close()
        engine.dispose()

    app = FastAPI(title="Keitaro Campaign Manager", version="0.1.0", lifespan=lifespan)

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

    return app
