import asyncio

import httpx

from app.config import Settings
from app.schemas import ReferenceData, ReferenceItem


class KeitaroError(Exception):
    def __init__(
        self, code: str, message: str, status_code: int = 502, ambiguous=False
    ):
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.ambiguous = ambiguous


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

    async def request(self, method: str, resource: str, **kwargs):
        if not self.configured:
            raise KeitaroError("not_configured", "Не задан KEITARO_API_KEY", 503)
        try:
            response = await self.http.request(method, resource, **kwargs)
        except httpx.TimeoutException:
            raise KeitaroError(
                "timeout", "Keitaro не ответил вовремя", 504, method != "GET"
            ) from None
        except httpx.RequestError:
            raise KeitaroError(
                "unavailable",
                "Не удалось связаться с Keitaro",
                ambiguous=method != "GET",
            ) from None

        if response.status_code in (401, 403):
            raise KeitaroError(
                "access_denied", "API-ключ или права доступа не подходят"
            )
        if response.status_code == 402:
            raise KeitaroError(
                "api_unavailable", "Admin API недоступен в этой редакции"
            )
        if response.status_code == 404:
            raise KeitaroError("not_found", "Объект не найден в Keitaro", 422)
        if response.status_code in (400, 406, 422):
            raise KeitaroError("rejected", "Keitaro отклонил параметры запроса", 422)
        if response.status_code not in (200, 201):
            raise KeitaroError(
                "upstream_error", "Ошибка ответа Keitaro", ambiguous=method != "GET"
            )
        try:
            payload = response.json()
        except ValueError:
            raise KeitaroError(
                "invalid_response",
                "Keitaro вернул некорректный JSON",
                ambiguous=method != "GET",
            ) from None
        return payload

    async def get_list(self, resource: str, **params) -> list[dict]:
        payload = await self.request("GET", resource, params=params)
        if not isinstance(payload, list) or any(
            not isinstance(item, dict) for item in payload
        ):
            raise KeitaroError("invalid_response", "Неожиданный формат справочника")
        return payload

    async def get_object(self, resource: str) -> dict:
        return self.require_object(await self.request("GET", resource))

    async def post(self, resource: str, payload=None) -> dict:
        return self.require_object(
            await self.request("POST", resource, json=payload), ambiguous=True
        )

    @staticmethod
    def require_object(payload, ambiguous=False):
        if not isinstance(payload, dict) or type(payload.get("id")) is not int:
            raise KeitaroError(
                "invalid_response",
                "В ответе Keitaro отсутствует ID объекта",
                ambiguous=ambiguous,
            )
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
