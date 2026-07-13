"""
DSP Scorecard Weekly module.

- Ingests the Amazon Delivery Excellence Scorecard PDF from ops_ingest dispatcher
- Auto-posts a rich summary + dispute guide to #nday-mgt on ingest
- Wednesday 12:30–5 PM PST: reminder every 30 min until scorecard is uploaded
- Admin endpoints: list snapshots, full metric detail per week
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File
from sqlalchemy.orm import Session

from api.src.database import (
    SessionLocal, get_db,
    DspScorecardWeeklySnapshot, DspScorecardWeeklyMetric,
)
from api.src.ingest.dsp_scorecard_weekly import parse_dsp_scorecard, STANDING_RANK

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dsp-scorecard-weekly", tags=["dsp-scorecard-weekly"])

# ─── constants ────────────────────────────────────────────────────────────────
NDAY_MGT_CHANNEL = os.getenv("NDAY_MGT_CHANNEL", "C0BCYAW7QP3")

_REMINDER_KEY = "dsp_scorecard_weekly_reminder"


def _load_reminder_state() -> dict:
    from api.src.database import get_reminder_state
    db = SessionLocal()
    try:
        raw = get_reminder_state(db, _REMINDER_KEY)
    finally:
        db.close()
    return {
        "last_reminded_at": datetime.fromisoformat(raw["last_reminded_at"]) if raw.get("last_reminded_at") else None,
        "reminder_count": raw.get("reminder_count", 0),
        "resolved_week": raw.get("resolved_week"),
    }


def _save_reminder_state(state: dict) -> None:
    from api.src.database import set_reminder_state
    db = SessionLocal()
    try:
        set_reminder_state(db, _REMINDER_KEY, {
            "last_reminded_at": state["last_reminded_at"].isoformat() if state.get("last_reminded_at") else None,
            "reminder_count": state.get("reminder_count", 0),
            "resolved_week": state.get("resolved_week"),
        })
    finally:
        db.close()


# ─── helpers ──────────────────────────────────────────────────────────────────

def _slack_client():
    from slack_sdk import WebClient
    return WebClient(token=os.environ["SLACK_BOT_TOKEN"])


def _current_week_label() -> str:
    """Return ISO-style week for today, e.g. '2026-W26'."""
    today = date.today()
    iso = today.isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def _standing_emoji(standing: Optional[str]) -> str:
    rank = STANDING_RANK.get(standing or "", -1)
    return {4: "🌟", 3: "✅", 2: "🟡", 1: "🟠", 0: "🔴"}.get(rank, "⬜")


def _build_slack_summary(snap: DspScorecardWeeklySnapshot) -> str:
    """Build a readable Slack summary with dispute guidance."""
    lines: list[str] = []

    score_str = f"{snap.overall_score:.1f}" if snap.overall_score is not None else "?"
    lines.append(
        f"*DSP Scorecard — {snap.week}*  ·  "
        f"{_standing_emoji(snap.overall_standing)} *{snap.overall_standing}  {score_str}*"
    )
    lines.append("")

    # Category grid
    cats = [
        ("Safety & Compliance", snap.safety_standing),
        ("Delivery Quality",    snap.delivery_quality_standing),
        ("Pickup Quality",      snap.pickup_quality_standing),
        ("Team & Fleet",        snap.team_fleet_standing),
    ]
    for label, standing in cats:
        if standing:
            lines.append(f"{_standing_emoji(standing)}  {label}: *{standing}*")
    lines.append("")

    # Below-Fantastic metrics
    below_metrics = [m for m in snap.metrics if STANDING_RANK.get(m.standing or "", 0) < STANDING_RANK["Fantastic"]]
    if below_metrics:
        lines.append("*Areas Below Fantastic:*")
        for m in sorted(below_metrics, key=lambda x: STANDING_RANK.get(x.standing or "", 0)):
            val = f"{m.value_numeric}" if m.value_numeric is not None else "—"
            weight = f"({m.weight_pct}%)" if m.weight_pct else ""
            lines.append(f"  {_standing_emoji(m.standing)}  {m.label}: *{m.standing}*  —  {val} {weight}".rstrip())
    else:
        lines.append("✅ All metrics at Fantastic — clean week!")
    lines.append("")

    # Focus areas from Amazon
    if snap.focus_areas:
        lines.append("*Amazon Focus Areas (per scorecard):*")
        for i, fa in enumerate(snap.focus_areas, 1):
            lines.append(f"  {i}. {fa}")
        lines.append("")

    # Dispute opportunities
    dispute_metrics = [m for m in snap.metrics if m.is_disputable and m.dispute_note]
    if dispute_metrics:
        lines.append("*🔎 Dispute Opportunities:*")
        for m in dispute_metrics:
            lines.append(f"  • *{m.label}* ({m.standing}):  {m.dispute_note}")
        lines.append("")

    # DC DPMO adjustment note
    if snap.dc_adjustment_note:
        lines.append(f"_ℹ️  {snap.dc_adjustment_note}_")
        lines.append("")

    lines.append("_Use the DSP Scorecard module in the dashboard for the full metric table._")
    return "\n".join(lines)


def _post_summary_to_slack(snap: DspScorecardWeeklySnapshot) -> None:
    client = _slack_client()
    text = _build_slack_summary(snap)
    client.chat_postMessage(channel=NDAY_MGT_CHANNEL, text=text, mrkdwn=True)
    snap.slack_posted = True


# ─── ingest logic ─────────────────────────────────────────────────────────────

def _store_scorecard(
    content: bytes,
    filename: str,
    slack_file_id: Optional[str],
    db: Session,
) -> DspScorecardWeeklySnapshot:
    summary, metrics = parse_dsp_scorecard(content, filename)
    week = summary["week"]

    existing = db.query(DspScorecardWeeklySnapshot).filter_by(week=week).first()
    if existing:
        # Re-ingest: delete old metrics, refresh snapshot
        db.delete(existing)
        db.flush()

    snap = DspScorecardWeeklySnapshot(
        week=week,
        source_file=filename,
        slack_file_id=slack_file_id,
        overall_score=summary["overall_score"],
        overall_standing=summary["overall_standing"],
        safety_standing=summary["category_standings"].get("safety"),
        delivery_quality_standing=summary["category_standings"].get("delivery_quality"),
        pickup_quality_standing=summary["category_standings"].get("pickup_quality"),
        team_fleet_standing=summary["category_standings"].get("team_fleet"),
        focus_areas=summary["focus_areas"],
        dc_adjustment_note=summary["dc_adjustment_note"],
    )
    db.add(snap)
    db.flush()

    for m in metrics:
        db.add(DspScorecardWeeklyMetric(
            snapshot_id=snap.id,
            week=week,
            slug=m["slug"],
            label=m["label"],
            category=m["category"],
            value_numeric=m["value_numeric"],
            standing=m["standing"],
            weight_pct=m["weight_pct"],
            is_disputable=m["is_disputable"],
            dispute_note=m["dispute_note"],
        ))

    db.commit()
    db.refresh(snap)
    return snap


# ─── reminder loop ────────────────────────────────────────────────────────────

def run_dsp_scorecard_reminder() -> None:
    """Called every 60s from main.py. Fires Wednesday 12:30–17:00 PST every 30 min."""
    import zoneinfo
    tz = zoneinfo.ZoneInfo("America/Los_Angeles")
    now = datetime.now(tz)

    if now.weekday() != 2:   # 2 = Wednesday
        return
    if now.hour < 12 or (now.hour == 12 and now.minute < 30):
        return
    if now.hour >= 17:
        return

    state = _load_reminder_state()

    # Throttle: only fire every 30 min
    last = state.get("last_reminded_at")
    if last and (datetime.now() - last).total_seconds() < 1800:
        return

    # Check if this week's scorecard is already in the DB
    week = _current_week_label()
    if state.get("resolved_week") == week:
        return

    db = SessionLocal()
    try:
        snap = db.query(DspScorecardWeeklySnapshot).filter_by(week=week).first()
        if snap:
            state["resolved_week"] = week
            _save_reminder_state(state)
            return
    finally:
        db.close()

    # Fire reminder
    client = _slack_client()
    count = state["reminder_count"] + 1
    state["reminder_count"] = count
    state["last_reminded_at"] = datetime.now()
    _save_reminder_state(state)

    if count == 1:
        msg = (
            "📊 *DSP Scorecard reminder:* Amazon's SLA was noon PST — "
            f"the *{week}* scorecard has not been uploaded yet.\n"
            "Drop the PDF in <#C0BE4ALL1EX> with the word _scorecard_ and it will auto-ingest."
        )
    elif count <= 3:
        msg = (
            f"📊 *Scorecard reminder #{count}:* {week} scorecard still not uploaded. "
            "Please drop the PDF in <#C0BE4ALL1EX>."
        )
    else:
        msg = (
            f"🚨 *Scorecard overdue — reminder #{count}:* "
            f"The {week} DSP Scorecard is past Amazon's noon PST SLA. "
            "Disputes window is closing — upload the PDF to <#C0BE4ALL1EX> now."
        )

    try:
        client.chat_postMessage(channel=NDAY_MGT_CHANNEL, text=msg)
        logger.info("DSP scorecard reminder #%d sent for %s", count, week)
    except Exception as exc:
        logger.warning("DSP scorecard reminder send failed: %s", exc)


# ─── endpoints ────────────────────────────────────────────────────────────────

@router.post("/ingest-slack")
def ingest_from_slack(
    payload: dict,
    db: Session = Depends(get_db),
):
    """Called by the ops_ingest dispatcher with raw file bytes + metadata."""
    content: bytes = payload["content"]
    filename: str = payload["filename"]
    slack_file_id: Optional[str] = payload.get("slack_file_id")

    try:
        snap = _store_scorecard(content, filename, slack_file_id, db)
        _post_summary_to_slack(snap)
        return {"status": "ok", "week": snap.week, "overall": snap.overall_standing}
    except Exception as exc:
        logger.exception("DSP scorecard ingest failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.post("/ingest-upload")
async def ingest_upload(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    content = await file.read()
    try:
        snap = _store_scorecard(content, file.filename or "scorecard.pdf", None, db)
        _post_summary_to_slack(snap)
        return {"status": "ok", "week": snap.week, "overall": snap.overall_standing}
    except Exception as exc:
        logger.exception("DSP scorecard upload ingest failed")
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/weeks")
def list_weeks(db: Session = Depends(get_db)):
    snaps = (
        db.query(DspScorecardWeeklySnapshot)
        .order_by(DspScorecardWeeklySnapshot.week.desc())
        .all()
    )
    return [
        {
            "week":              s.week,
            "overall_score":     float(s.overall_score) if s.overall_score else None,
            "overall_standing":  s.overall_standing,
            "imported_at":       s.imported_at.isoformat() if s.imported_at else None,
            "slack_posted":      s.slack_posted,
        }
        for s in snaps
    ]


@router.get("/week/{week}")
def get_week(week: str, db: Session = Depends(get_db)):
    snap = db.query(DspScorecardWeeklySnapshot).filter_by(week=week).first()
    if not snap:
        raise HTTPException(status_code=404, detail=f"No scorecard for {week}")
    metrics = [
        {
            "slug":          m.slug,
            "label":         m.label,
            "category":      m.category,
            "value_numeric": float(m.value_numeric) if m.value_numeric is not None else None,
            "standing":      m.standing,
            "weight_pct":    float(m.weight_pct) if m.weight_pct is not None else None,
            "is_disputable": m.is_disputable,
            "dispute_note":  m.dispute_note,
        }
        for m in snap.metrics
    ]
    return {
        "week":                       snap.week,
        "source_file":                snap.source_file,
        "imported_at":                snap.imported_at.isoformat() if snap.imported_at else None,
        "overall_score":              float(snap.overall_score) if snap.overall_score else None,
        "overall_standing":           snap.overall_standing,
        "safety_standing":            snap.safety_standing,
        "delivery_quality_standing":  snap.delivery_quality_standing,
        "pickup_quality_standing":    snap.pickup_quality_standing,
        "team_fleet_standing":        snap.team_fleet_standing,
        "focus_areas":                snap.focus_areas,
        "dc_adjustment_note":         snap.dc_adjustment_note,
        "slack_posted":               snap.slack_posted,
        "metrics":                    metrics,
    }


@router.post("/week/{week}/repost-slack")
def repost_slack(week: str, db: Session = Depends(get_db)):
    snap = db.query(DspScorecardWeeklySnapshot).filter_by(week=week).first()
    if not snap:
        raise HTTPException(status_code=404, detail=f"No scorecard for {week}")
    snap.metrics  # force-load relationship
    try:
        _post_summary_to_slack(snap)
        db.commit()
        return {"status": "ok", "week": week}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))
