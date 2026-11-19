from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.keitaro import KeitaroClient, KeitaroError
from app.models import Operation
from app.schemas import CampaignCreate, CreationResult


class CreationConflict(Exception):
    def __init__(self, result: CreationResult, status_code=409):
        self.result = result
        self.status_code = status_code


def operation_result(operation: Operation) -> CreationResult:
    messages = {
        "pending": "Операция выполняется. Повторное создание не запущено.",
        "succeeded": "Кампания создана, оба потока проверены и включены.",
        "failed": "Создание не выполнено. Проверьте данные перед новой попыткой.",
        "partial": (
            "Операция завершилась не полностью или её исход неизвестен. "
            "Проверьте кампанию в Keitaro перед созданием новой."
        ),
    }
    return CreationResult(
        request_id=operation.request_id,
        status=operation.status,
        campaign_id=operation.campaign_id,
        campaign_name=operation.campaign_name,
        error_code=operation.error_code,
        message=messages[operation.status],
        **(operation.result or {}),
    )


class CampaignCreator:
    def __init__(self, client: KeitaroClient, sessions):
        self.client = client
        self.sessions = sessions

    def get_result(self, request_id: UUID) -> CreationResult | None:
        with self.sessions() as session:
            operation = session.scalar(
                select(Operation).where(Operation.request_id == str(request_id))
            )
            return operation_result(operation) if operation else None

    async def reconcile(self, request_id: UUID) -> CreationResult | None:
        """Подтвердить неопределённый результат, выполняя в Keitaro только GET."""
        with self.sessions() as session:
            operation = session.scalar(
                select(Operation).where(Operation.request_id == str(request_id))
            )
            if operation is None:
                return None
            if (
                operation.status not in ("partial", "pending")
                or not operation.campaign_id
            ):
                return operation_result(operation)
            data = CampaignCreate(request_id=request_id, **operation.request_payload)
            try:
                await self.verify(data, operation.campaign_id, "active")
            except KeitaroError:
                return operation_result(operation)
            operation.status = "succeeded"
            operation.error_code = None
            session.commit()
            return operation_result(operation)

    async def create(self, data: CampaignCreate) -> CreationResult:
        request_id = str(data.request_id)
        payload = data.model_dump(mode="json", exclude={"request_id"})
        with self.sessions() as session:
            operation = Operation(
                kind="create_campaign",
                request_id=request_id,
                request_payload=payload,
                campaign_name=data.name,
                status="pending",
            )
            session.add(operation)
            try:
                session.commit()
            except IntegrityError:
                session.rollback()
                existing = session.scalar(
                    select(Operation).where(Operation.request_id == request_id)
                )
                if existing is None:
                    raise
                result = operation_result(existing)
                if existing.request_payload != payload:
                    result.message = (
                        "Этот request_id уже использован для других данных."
                    )
                    raise CreationConflict(result) from None
                if existing.status == "succeeded":
                    return result
                raise CreationConflict(result) from None

            try:
                references = await self.client.reference_data()
                for field, rows in (
                    ("domain_id", references.domains),
                    ("group_id", references.groups),
                    ("traffic_source_id", references.traffic_sources),
                    ("offer_id", references.offers),
                ):
                    if getattr(data, field) not in {row.id for row in rows}:
                        raise KeitaroError(
                            "invalid_reference", f"Недоступное значение {field}", 422
                        )
                domain = await self.client.get_object(f"domains/{data.domain_id}")
                offer = await self.client.get_object(f"offers/{data.offer_id}")
                if domain.get("state") != "active" or offer.get("state") != "active":
                    raise KeitaroError(
                        "inactive_reference", "Домен или оффер неактивен", 422
                    )
                alias = f"tk-{data.request_id.hex}"
                campaign = await self.client.post(
                    "campaigns",
                    {
                        "name": data.name,
                        "alias": alias,
                        "type": "position",
                        "state": "disabled",
                        "domain_id": data.domain_id,
                        "group_id": data.group_id,
                        "traffic_source_id": data.traffic_source_id,
                    },
                )
                operation.campaign_id = campaign["id"]
                origin = str(self.client.http.base_url).split("/admin_api/")[0]
                scheme = "https" if domain.get("is_ssl") else "http"
                operation.result = {
                    "admin_url": f"{origin}/admin/#!/campaigns/{campaign['id']}",
                    "campaign_url": f"{scheme}://{domain['name']}/{alias}",
                    "stream_ids": [],
                }
                session.commit()

                definitions = [
                    {
                        "name": f"Geo {data.country_code} → Google",
                        "type": "regular",
                        "schema": "redirect",
                        "action_type": "http",
                        "action_payload": "https://www.google.com/",
                        "position": 1,
                        "filters": [
                            {
                                "name": "country",
                                "mode": "accept",
                                "payload": [data.country_code],
                            }
                        ],
                    },
                    {
                        "name": "Остальные → оффер",
                        "type": "regular",
                        "schema": "landings",
                        "action_type": "http",
                        "position": 2,
                        "filters": [],
                        "landings": [],
                        "offers": [
                            {"offer_id": data.offer_id, "share": 100, "state": "active"}
                        ],
                    },
                ]
                for definition in definitions:
                    stream = await self.client.post(
                        "streams",
                        {
                            **definition,
                            "campaign_id": campaign["id"],
                            "state": "active",
                            "collect_clicks": True,
                            "filter_or": False,
                        },
                    )
                    operation.result = {
                        **operation.result,
                        "stream_ids": [*operation.result["stream_ids"], stream["id"]],
                    }
                    session.commit()

                await self.verify(data, campaign["id"], "disabled")
                await self.client.request("POST", f"campaigns/{campaign['id']}/enable")
                await self.verify(data, campaign["id"], "active")
                operation.status = "succeeded"
                session.commit()
                return operation_result(operation)
            except KeitaroError as error:
                operation.status = (
                    "partial" if operation.campaign_id or error.ambiguous else "failed"
                )
                operation.error_code = error.code
                session.commit()
                result = operation_result(operation)
                result.message += f" {error}"
                raise CreationConflict(
                    result, 502 if operation.status == "partial" else error.status_code
                ) from None

    async def verify(self, data: CampaignCreate, campaign_id: int, state: str):
        campaign = await self.client.get_object(f"campaigns/{campaign_id}")
        streams = await self.client.get_list(f"campaigns/{campaign_id}/streams")
        streams.sort(key=lambda row: row.get("position", 0))
        valid = (
            campaign.get("name") == data.name
            and campaign.get("type") == "position"
            and campaign.get("state") == state
            and campaign.get("domain_id") == data.domain_id
            and str(campaign.get("group_id")) == str(data.group_id)
            and campaign.get("traffic_source_id") == data.traffic_source_id
            and len(streams) == 2
        )
        if valid:
            geo, offer = streams
            valid = (
                geo.get("position") == 1
                and offer.get("position") == 2
                and geo.get("type") == offer.get("type") == "regular"
                and geo.get("state") == offer.get("state") == "active"
                and geo.get("schema") == "redirect"
                and geo.get("action_type") == "http"
                and geo.get("action_payload") == "https://www.google.com/"
                and len(geo.get("filters", [])) == 1
                and geo["filters"][0].get("name") == "country"
                and geo["filters"][0].get("mode") == "accept"
                and geo["filters"][0].get("payload") == [data.country_code]
                and not geo.get("filter_or", False)
                and offer.get("schema") == "landings"
                and not offer.get("filters")
                and not offer.get("landings")
                and len(offer.get("offers", [])) == 1
                and offer["offers"][0].get("offer_id") == data.offer_id
                and offer["offers"][0].get("share") == 100
                and offer["offers"][0].get("state") == "active"
            )
        if not valid:
            raise KeitaroError(
                "verification_failed", "Настройки кампании не прошли проверку"
            )
