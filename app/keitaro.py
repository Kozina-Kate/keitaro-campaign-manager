import asyncio

import httpx

from app.config import Settings
from app.schemas import ReferenceData, ReferenceItem


class KeitaroError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 502):
        super().__init__(message)
        self.code = code
        self.status_code = status_code


class KeitaroClient:
    def __init__(self, settings: Settings, transport=None):
        self.configured = bool(settings.keitaro_api_key.get_secret_value())
        self.http = httpx.AsyncClient(
            base_url=f"{str(settings.keitaro_base_url).rstrip('/')}/admin_api/v1/",
            headers={
                "Api-Key": settings.keitaro_api_key.get_secret_value(),
                "Accept": "application/json",
            },
            timeout=settings.keitaro_timeout_seconds,
            follow_redirects=False,
            transport=transport,
        )

    async def close(self):
        await self.http.aclose()

    async def get_list(self, resource: str, **params) -> list[dict]:
        if not self.configured:
            raise KeitaroError("not_configured", "Не задан KEITARO_API_KEY", 503)
        try:
            response = await self.http.get(resource, params=params)
        except httpx.TimeoutException:
            raise KeitaroError("timeout", "Keitaro не ответил вовремя", 504) from None
        except httpx.RequestError:
            raise KeitaroError(
                "unavailable", "Не удалось связаться с Keitaro"
            ) from None

        if response.status_code in (401, 403):
            raise KeitaroError(
                "access_denied", "API-ключ или права доступа не подходят"
            )
        if response.status_code == 402:
            raise KeitaroError(
                "api_unavailable", "Admin API недоступен в этой редакции"
            )
        if response.status_code != 200:
            raise KeitaroError("upstream_error", "Ошибка ответа Keitaro")
        try:
            payload = response.json()
        except ValueError:
            raise KeitaroError(
                "invalid_response", "Keitaro вернул некорректный JSON"
            ) from None
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise KeitaroError("invalid_response", "Неожиданный формат справочника")
        return payload

    async def reference_data(self) -> ReferenceData:
        domains, groups, sources, offers = await asyncio.gather(
            self.get_list("domains"),
            self.get_list("groups", type="campaigns"),
            self.get_list("traffic_sources"),
            self.get_list("offers"),
        )

        def items(rows):
            try:
                return [ReferenceItem(id=row["id"], name=row["name"]) for row in rows]
            except (KeyError, ValueError, TypeError):
                raise KeitaroError(
                    "invalid_response", "В справочнике отсутствуют ID или название"
                ) from None

        return ReferenceData(
            domains=items(domains),
            groups=items(groups),
            traffic_sources=items(sources),
            offers=items(offers),
        )
