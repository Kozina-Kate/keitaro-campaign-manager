from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Integer, String, Text
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
