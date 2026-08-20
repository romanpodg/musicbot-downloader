"""Append-only high-value operational and security audit events."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, Enum, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.enums import (
    OperationalAuditActorKind,
    OperationalAuditEventType,
    OperationalAuditTargetKind,
)
from app.storage.models.base import Base, UTCDateTime, utc_now


def _enum(enum_type: type, name: str, length: int) -> Enum:
    return Enum(
        enum_type,
        values_callable=lambda values: [member.value for member in values],
        name=name,
        native_enum=False,
        length=length,
        create_constraint=False,
    )


class OperationalAuditEvent(Base):
    __tablename__ = "operational_audit_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('ADMIN_PROMOTED', 'ADMIN_DEMOTED', "
            "'WORKER_DESIRED_CHANGED', 'DEEP_LINK_REGISTERED', 'DEEP_LINK_REVOKED', "
            "'CRASH_RECOVERY_COMPLETED', 'MANUAL_RECOVERY_EXECUTED', "
            "'MANUAL_ARTIFACT_CLEANUP_EXECUTED', 'SQLITE_BACKUP_CREATED')",
            name="ck_operational_audit_event_type",
        ),
        CheckConstraint(
            "actor_kind IN ('TELEGRAM_USER', 'INTERNAL_API', 'LOCAL_OPERATOR', 'SYSTEM')",
            name="ck_operational_audit_actor_kind",
        ),
        CheckConstraint(
            "target_kind IS NULL OR target_kind IN ('USER', 'WORKER_POOL', 'DEEP_LINK', "
            "'RECOVERY', 'ARTIFACT_CLEANUP', 'DATABASE_BACKUP')",
            name="ck_operational_audit_target_kind",
        ),
        CheckConstraint(
            "(actor_kind = 'TELEGRAM_USER' AND actor_user_id IS NOT NULL) OR "
            "(actor_kind != 'TELEGRAM_USER' AND actor_user_id IS NULL)",
            name="ck_operational_audit_actor_identity",
        ),
        CheckConstraint(
            "details_json IS NULL OR length(details_json) <= 4096",
            name="ck_operational_audit_details_length",
        ),
        Index("ix_operational_audit_occurred", "occurred_at"),
        Index("ix_operational_audit_event_occurred", "event_type", "occurred_at"),
        Index("ix_operational_audit_actor_occurred", "actor_user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(UTCDateTime(), default=utc_now, nullable=False)
    event_type: Mapped[OperationalAuditEventType] = mapped_column(
        _enum(OperationalAuditEventType, "operationalauditeventtype", 48), nullable=False
    )
    actor_kind: Mapped[OperationalAuditActorKind] = mapped_column(
        _enum(OperationalAuditActorKind, "operationalauditactorkind", 24), nullable=False
    )
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    target_kind: Mapped[OperationalAuditTargetKind | None] = mapped_column(
        _enum(OperationalAuditTargetKind, "operationalaudittargetkind", 32)
    )
    target_id: Mapped[str | None] = mapped_column(String(64))
    request_id: Mapped[str | None] = mapped_column(String(128))
    details_json: Mapped[str | None] = mapped_column(Text)
