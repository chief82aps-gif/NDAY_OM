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

import os
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from api.src.database import DvicCounselingRecord, AttendanceEvent, DailyRouteAssignment, EodSurveyResponse
from api.src.driver_identity import _tokens
from api.src.feature_flags import get_flag

# Hard off-switch, default false — added 2026-07-26 within hours of the
# missed_eod_survey check below going live. That check depends entirely on
# EOD survey completion actually working, which was NOT true at the time
# (0% completion for 3 straight days) — gating route DMs on the output of
# an already-broken system meant nearly the whole scheduled roster got
# stuck behind a holding message the same day this shipped. Do not turn on
# until EOD survey delivery/completion is confirmed genuinely working end
# to end, not just deployed. Live-checked via get_flag() (feature_flags.py)
# so it can be toggled from the admin page without a redeploy.


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

    # Missed EOD survey — added 2026-07-26, explicit direction: "explain
    # why you missed it" is just completing that overdue survey, not a
    # separate form. Checked over the last 7 days (not just yesterday) so
    # a multi-day gap doesn't silently stop resurfacing once today's route
    # DM would otherwise dedup past it. Only counts a day the driver
    # actually had a route (no assignment = nothing to have submitted).
    if roster_id is not None and get_flag("MISSED_EOD_GATE_ACTIVE"):
        from api.src.routes.eod_survey import _issue_eod_token

        today = date.today()
        for days_back in range(1, 8):
            check_date = today - timedelta(days=days_back)
            had_route = db.query(DailyRouteAssignment).filter(
                DailyRouteAssignment.assignment_date == check_date,
                DailyRouteAssignment.roster_id == roster_id,
            ).first()
            if not had_route:
                continue
            submitted = db.query(EodSurveyResponse).filter(
                EodSurveyResponse.roster_id == roster_id,
                EodSurveyResponse.survey_date == check_date,
            ).first()
            if submitted:
                continue
            items.append({
                "type": "missed_eod_survey",
                "id": had_route.id,
                "survey_date": check_date.isoformat(),
                "label": f"Missed End of Day Survey — {check_date.strftime('%A, %b %-d')}",
                "eod_token": _issue_eod_token(roster_id, None, driver_name),
            })

    return items
