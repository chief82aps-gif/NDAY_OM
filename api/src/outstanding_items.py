"""
Outstanding-items aggregator — added 2026-07-23 for the day-of Route
Assignment DM gate (rostering.py's send_day_of_dms()). Answers one
question: does this driver have anything of their own left to
acknowledge before they should get today's route details?

Deliberately narrow. Of the sources manager_accountability.py's
discipline_tracker() already aggregates for the manager-facing sign-off
dashboard, only two represent something the DRIVER themselves still has
to do:
  - DvicCounselingRecord.ack_status == "pending" — a DVIC safety notice
    they haven't tapped Acknowledge on yet.
  - AttendanceEvent.signature_name IS NULL — an attendance write-up
    logged on their behalf (e.g. dispatch recording a no-show) that they
    never signed via the self-service /callout page.
Everything else discipline_tracker() surfaces (unsigned manager/HR
countersignatures, crash-report approval stages) is a MANAGER's or HR's
outstanding action, not the driver's — the driver has already done
their part on those, so they don't belong here.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy.orm import Session

from api.src.database import DvicCounselingRecord, AttendanceEvent
from api.src.driver_identity import _tokens


def get_outstanding_items(driver_name: str, roster_id: Optional[int], db: Session) -> list[dict]:
    """Returns [] when nothing is pending — the common case, and the two
    queries below are each indexed/filtered, not full-table scans."""
    items: list[dict] = []

    # DVIC — no reliable transporter_id bridge exists from a day-of
    # DailyRouteAssignment row to DvicCounselingRecord (confirmed:
    # transporter_id isn't populated on this ingest path), so match by
    # name instead, same token-overlap approach used throughout the
    # driver-identity refactor.
    name_tokens = _tokens(driver_name)
    if name_tokens:
        for record in db.query(DvicCounselingRecord).filter(DvicCounselingRecord.ack_status == "pending").all():
            if len(name_tokens & _tokens(record.transporter_name)) >= 2:
                items.append({
                    "type": "dvic",
                    "id": record.id,
                    "transporter_id": record.transporter_id,
                    "week": record.last_week,
                    "stage": record.stage,
                    "label": f"Safety Notice — Stage {record.stage}",
                })

    # Attendance — driver never signed their own write-up.
    query = db.query(AttendanceEvent).filter(AttendanceEvent.signature_name.is_(None))
    if roster_id is not None:
        query = query.filter(AttendanceEvent.roster_id == roster_id)
    else:
        query = query.filter(AttendanceEvent.driver_name == driver_name)
    for event in query.all():
        items.append({
            "type": "attendance",
            "id": event.id,
            "label": "Attendance Write-Up",
            "event_type": event.event_type,
            "event_date": event.event_date.isoformat() if event.event_date else None,
        })

    return items
