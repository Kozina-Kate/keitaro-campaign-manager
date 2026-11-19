from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Operation(Base):
    """Локальный журнал; настройки кампаний остаются в Keitaro."""

    __tablename__ = "operations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'partial')",
            name="ck_operations_status",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    campaign_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    campaign_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_code: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_id: Mapped[str | None] = mapped_column(
        String(36), unique=True, nullable=True
    )
    request_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


class EditorStream(Base):
    __tablename__ = "editor_streams"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=False)
    campaign_id: Mapped[int] = mapped_column(index=True)
    name: Mapped[str] = mapped_column(String(255))
    schema: Mapped[str] = mapped_column(String(40))
    position: Mapped[int] = mapped_column(default=0)
    editable: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_exists: Mapped[bool] = mapped_column(Boolean, default=True)
    snapshot: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="ready")
    pending_offers: Mapped[list | None] = mapped_column(JSON, nullable=True)
    publish_started: Mapped[float | None] = mapped_column(Float, nullable=True)
    revision: Mapped[int] = mapped_column(default=1)
    __mapper_args__ = {"version_id_col": revision}


class EditorOffer(Base):
    __tablename__ = "editor_offers"
    __table_args__ = (
        CheckConstraint("share >= 0 AND share <= 100", name="ck_editor_offer_share"),
        CheckConstraint(
            "base_share >= 0 AND base_share <= 100", name="ck_editor_offer_base_share"
        ),
    )

    stream_id: Mapped[int] = mapped_column(
        ForeignKey("editor_streams.id"), primary_key=True
    )
    offer_id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(500))
    active: Mapped[bool] = mapped_column(Boolean)
    share: Mapped[int] = mapped_column(Integer)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    base_active: Mapped[bool] = mapped_column(Boolean)
    base_share: Mapped[int] = mapped_column(Integer)
    base_pinned: Mapped[bool] = mapped_column(Boolean, default=False)
    draft_new: Mapped[bool] = mapped_column(Boolean, default=False)
