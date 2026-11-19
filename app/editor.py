import time

from sqlalchemy import select

from app.keitaro import KeitaroError
from app.models import EditorOffer, EditorStream
from app.schemas import CAMPAIGN_PREFIX


class EditorError(Exception):
    def __init__(self, message, status_code=409):
        self.message = message
        self.status_code = status_code


def fingerprint(remote):
    """Только поля, влияющие на редактируемый список; не сохраняем секреты API."""
    try:
        offers = sorted(
            [
                {
                    "offer_id": int(row["offer_id"]),
                    "share": int(row["share"]),
                    "state": row.get("state", "active"),
                }
                for row in remote.get("offers", [])
            ],
            key=lambda row: row["offer_id"],
        )
        if len({row["offer_id"] for row in offers}) != len(offers):
            raise ValueError
        if any(not 0 <= row["share"] <= 100 for row in offers):
            raise ValueError
        return {"schema": remote.get("schema"), "offers": offers}
    except (KeyError, TypeError, ValueError):
        raise KeitaroError("invalid_response", "Некорректный список офферов") from None


def dirty(offers):
    return any(
        row.draft_new
        or (row.active, row.share, row.pinned)
        != (row.base_active, row.base_share, row.base_pinned)
        for row in offers
    )


def distribute(offers):
    active = sorted((row for row in offers if row.active), key=lambda row: row.offer_id)
    for row in offers:
        if not row.active:
            row.share = 0
            row.pinned = False
    if not active:
        return
    pinned = [row for row in active if row.pinned]
    free = [row for row in active if not row.pinned]
    remaining = 100 - sum(row.share for row in pinned)
    if remaining < 0:
        raise EditorError("Сумма закреплённых долей не может превышать 100%", 422)
    if not free and remaining:
        raise EditorError(
            "Сумма долей должна быть 100%. Открепите хотя бы один оффер.", 422
        )
    if free:
        share, remainder = divmod(remaining, len(free))
        for index, row in enumerate(free):
            row.share = share + (index < remainder)


class CampaignEditor:
    def __init__(self, client, sessions, timeout):
        self.client = client
        self.sessions = sessions
        self.timeout = timeout

    @staticmethod
    def rows(session, stream_id):
        return list(
            session.scalars(
                select(EditorOffer)
                .where(EditorOffer.stream_id == stream_id)
                .order_by(EditorOffer.offer_id)
            )
        )

    def view(self, session, stream):
        rows = self.rows(session, stream.id)
        changed = dirty(rows)
        total = sum(row.share for row in rows if row.active)
        return {
            "id": stream.id,
            "campaign_id": stream.campaign_id,
            "name": stream.name,
            "schema": stream.schema,
            "position": stream.position,
            "revision": stream.revision,
            "status": stream.status,
            "dirty": changed,
            "editable": stream.editable and stream.remote_exists,
            "remote_exists": stream.remote_exists,
            "total_share": total,
            "can_publish": changed
            and total == 100
            and stream.status == "ready"
            and stream.editable
            and stream.remote_exists,
            "offers": [
                {
                    "offer_id": row.offer_id,
                    "name": row.name,
                    "share": row.share,
                    "pinned": row.pinned,
                    "removed": not row.active,
                    "draft_new": row.draft_new,
                }
                for row in rows
            ],
        }

    def load(self, session, stream_id, revision=None, allow_locked=False):
        stream = session.get(EditorStream, stream_id)
        if stream is None:
            raise EditorError("Сначала загрузите потоки кампании", 404)
        if revision is not None and stream.revision != revision:
            raise EditorError(
                "Версия устарела. Обновите сохранённое состояние редактора."
            )
        if not allow_locked and stream.status in ("publishing", "uncertain"):
            raise EditorError("Исход публикации нужно проверить перед редактированием.")
        return stream

    async def campaign(self, campaign_id):
        campaign = await self.client.get_object(f"campaigns/{campaign_id}")
        if not campaign.get("name", "").startswith(CAMPAIGN_PREFIX):
            raise EditorError(
                f"В тестовом редакторе доступны кампании {CAMPAIGN_PREFIX}…", 403
            )
        return campaign

    def cached(self, campaign_id):
        with self.sessions() as session:
            streams = session.scalars(
                select(EditorStream)
                .where(EditorStream.campaign_id == campaign_id)
                .order_by(EditorStream.position, EditorStream.id)
            )
            return {
                "campaign_id": campaign_id,
                "streams": [self.view(session, stream) for stream in streams],
            }

    async def fetch(self, campaign_id):
        campaign = await self.campaign(campaign_id)
        remote = await self.client.get_list(f"campaigns/{campaign_id}/streams")
        names = {row["id"]: row["name"] for row in await self.client.get_list("offers")}
        with self.sessions() as session:
            existing = list(
                session.scalars(
                    select(EditorStream).where(EditorStream.campaign_id == campaign_id)
                )
            )
            for stream in existing:
                if dirty(self.rows(session, stream.id)) or stream.status in (
                    "publishing",
                    "uncertain",
                ):
                    raise EditorError(
                        "Есть черновик или неопределённая публикация. "
                        "Сначала отмените или проверьте её."
                    )
            found = set()
            for record in remote:
                stream_id = record["id"]
                found.add(stream_id)
                stream = session.get(EditorStream, stream_id)
                if stream and stream.campaign_id != campaign_id:
                    raise EditorError("Поток сменил кампанию. Требуется проверка.")
                if stream is None:
                    stream = EditorStream(id=stream_id, campaign_id=campaign_id)
                    session.add(stream)
                else:
                    stream.revision += 1
                stream.name = record["name"]
                stream.schema = record.get("schema", "")
                stream.position = record.get("position", 0)
                stream.remote_exists = True
                # Потоки без офферов отображаются без разбора фильтров и действия.
                stream.editable = stream.schema == "landings"
                stream.snapshot = fingerprint(record)
                stream.status = "ready"
                stream.pending_offers = None
                stream.publish_started = None
                session.flush()
                rows = {row.offer_id: row for row in self.rows(session, stream_id)}
                incoming = {row["offer_id"]: row for row in stream.snapshot["offers"]}
                for offer_id, value in incoming.items():
                    if offer_id not in rows:
                        row = EditorOffer(
                            stream_id=stream_id,
                            offer_id=offer_id,
                            name=names.get(offer_id, f"Оффер #{offer_id}"),
                            pinned=False,
                            base_pinned=False,
                            draft_new=False,
                        )
                        session.add(row)
                        rows[offer_id] = row
                    row = rows[offer_id]
                    row.name = names.get(offer_id, row.name)
                    was_active = row.base_active
                    row.active = row.base_active = value["state"] == "active"
                    row.share = row.base_share = value["share"] if row.active else 0
                    if not row.active or not was_active:
                        row.pinned = row.base_pinned = False
                    row.draft_new = False
                for offer_id, row in rows.items():
                    if offer_id not in incoming:
                        row.active = row.base_active = False
                        row.share = row.base_share = 0
                        row.pinned = row.base_pinned = False
            for stream in existing:
                if stream.id not in found:
                    stream.remote_exists = False
                    stream.revision += 1
            session.commit()
        result = self.cached(campaign_id)
        result["campaign_name"] = campaign["name"]
        return result

    async def edit(self, stream_id, action, data):
        offer = None
        if action in ("add", "restore"):
            if data.offer_id is None:
                raise EditorError("Не указан ID оффера", 422)
            offer = await self.client.get_object(f"offers/{data.offer_id}")
            if offer.get("state") != "active":
                raise EditorError("Оффер неактивен в Keitaro", 422)
        with self.sessions() as session:
            stream = self.load(session, stream_id, data.revision)
            if not stream.editable or not stream.remote_exists:
                raise EditorError(
                    "Этот поток недоступен для редактирования офферов", 422
                )
            rows = self.rows(session, stream_id)
            row = next((row for row in rows if row.offer_id == data.offer_id), None)
            if action == "cancel":
                for item in rows:
                    if item.draft_new:
                        session.delete(item)
                    else:
                        item.active, item.share, item.pinned = (
                            item.base_active,
                            item.base_share,
                            item.base_pinned,
                        )
                stream.status = "ready"
                stream.pending_offers = None
                stream.publish_started = None
            else:
                if stream.status == "conflict":
                    raise EditorError(
                        "Отмените черновик и загрузите изменения из Keitaro."
                    )
                if action == "add":
                    if row:
                        raise EditorError(
                            "Оффер уже известен. Для архивного используйте «Вернуть»."
                        )
                    row = EditorOffer(
                        stream_id=stream_id,
                        offer_id=data.offer_id,
                        name=offer.get("name", f"Оффер #{data.offer_id}"),
                        active=True,
                        share=0,
                        pinned=False,
                        base_active=False,
                        base_share=0,
                        base_pinned=False,
                        draft_new=True,
                    )
                    session.add(row)
                    rows.append(row)
                elif row is None:
                    raise EditorError("Оффер не найден в потоке", 404)
                elif action == "remove":
                    row.active = False
                elif action == "restore":
                    row.active = True
                    row.pinned = False
                elif action == "pin":
                    if not row.active or data.share is None:
                        raise EditorError(
                            "Для активного оффера укажите долю 0–100%", 422
                        )
                    row.pinned, row.share = True, data.share
                elif action == "unpin":
                    row.pinned = False
                distribute(rows)
            stream.revision += 1
            session.commit()
            return self.view(session, stream)

    @staticmethod
    def draft_payload(rows):
        return [
            {"offer_id": row.offer_id, "share": row.share, "state": "active"}
            for row in sorted(rows, key=lambda row: row.offer_id)
            if row.active
        ]

    def finish(self, session, stream, snapshot):
        for row in self.rows(session, stream.id):
            row.base_active, row.base_share, row.base_pinned = (
                row.active,
                row.share,
                row.pinned,
            )
            row.draft_new = False
        stream.snapshot = snapshot
        stream.status = "ready"
        stream.pending_offers = None
        stream.publish_started = None
        stream.revision += 1
        session.commit()
        return self.view(session, stream)

    async def publish(self, stream_id, revision):
        with self.sessions() as session:
            stream = self.load(session, stream_id, revision)
            if (
                stream.status == "conflict"
                or not stream.editable
                or not stream.remote_exists
            ):
                raise EditorError("Сначала загрузите актуальный поток из Keitaro.")
            rows = self.rows(session, stream_id)
            if not dirty(rows):
                return self.view(session, stream)
            intended = self.draft_payload(rows)
            if not intended or sum(row["share"] for row in intended) != 100:
                raise EditorError(
                    "В потоке нужен активный оффер, сумма долей — 100%.", 422
                )
            stream.status = "publishing"
            stream.pending_offers = intended
            stream.publish_started = time.time()
            session.commit()
            claimed_revision = stream.revision
            campaign_id, baseline = stream.campaign_id, stream.snapshot
        try:
            await self.campaign(campaign_id)
            remote = await self.client.get_object(f"streams/{stream_id}")
            if (
                remote.get("campaign_id") != campaign_id
                or fingerprint(remote) != baseline
            ):
                with self.sessions() as session:
                    stream = self.load(
                        session, stream_id, claimed_revision, allow_locked=True
                    )
                    stream.status = "conflict"
                    stream.pending_offers = None
                    session.commit()
                raise EditorError(
                    "Офферы изменились в Keitaro. Черновик сохранён; "
                    "отмените его и выполните Fetch."
                )
            # PUT меняет только offers; фильтры, лендинги и действие не отправляются.
            await self.client.request(
                "PUT", f"streams/{stream_id}", json={"offers": intended}
            )
            remote = await self.client.get_object(f"streams/{stream_id}")
            expected = {"schema": baseline["schema"], "offers": intended}
            if fingerprint(remote) != expected:
                raise KeitaroError(
                    "verification_failed", "Keitaro вернул другой список офферов"
                )
        except EditorError:
            with self.sessions() as session:
                stream = self.load(session, stream_id, allow_locked=True)
                if (
                    stream.revision == claimed_revision
                    and stream.status == "publishing"
                ):
                    stream.status = "ready"
                    stream.pending_offers = None
                    stream.publish_started = None
                    session.commit()
            raise
        except KeitaroError:
            with self.sessions() as session:
                stream = self.load(
                    session, stream_id, claimed_revision, allow_locked=True
                )
                stream.status = "uncertain"
                session.commit()
            raise
        with self.sessions() as session:
            stream = self.load(session, stream_id, claimed_revision, allow_locked=True)
            return self.finish(session, stream, fingerprint(remote))

    async def reconcile(self, stream_id, revision):
        with self.sessions() as session:
            stream = self.load(session, stream_id, revision, allow_locked=True)
            if (
                stream.status == "publishing"
                and time.time() - stream.publish_started < self.timeout + 5
            ):
                raise EditorError("Публикация ещё выполняется. Проверьте статус позже.")
            if stream.status not in ("publishing", "uncertain"):
                return self.view(session, stream)
            pending, baseline = stream.pending_offers, stream.snapshot
            remote = await self.client.get_object(f"streams/{stream_id}")
            current = fingerprint(remote)
            if remote.get("campaign_id") != stream.campaign_id:
                stream.status = "conflict"
                stream.pending_offers = None
                stream.publish_started = None
                stream.revision += 1
                session.commit()
                return self.view(session, stream)
            if current == {"schema": baseline["schema"], "offers": pending}:
                return self.finish(session, stream, current)
            stream.status = "ready" if current == baseline else "conflict"
            stream.pending_offers = None
            stream.publish_started = None
            stream.revision += 1
            session.commit()
            return self.view(session, stream)
