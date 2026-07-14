"""
Import real SSN last-4 PINs and Slack user IDs into driver_roster.

Usage:
    python scripts/import_ssn_slack.py \
        --ssn   "C:/Users/chief/Downloads/SSN (1).xlsx" \
        --slack "C:/Users/chief/Downloads/nday-team-room_slack_members (1).xlsx" \
        --db-url "postgresql://..." \
        [--dry-run]

Without --db-url uses DATABASE_URL env var (or local SQLite dev.db).

Matching logic lives in api/src/driver_matching.py (shared with the
POST /drivers/import-ssn-slack API endpoint, which is the preferred way
to run this against production — it doesn't require handing raw DB
credentials to a local script).
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime
from pathlib import Path

# ── path setup ────────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))

from api.src.driver_matching import load_ssn, load_slack, best_ssn_match, best_slack_match


def run(ssn_path: str, slack_path: str, db_url: str | None, dry_run: bool):
    db_url = db_url or os.getenv("DATABASE_URL", "sqlite:///./dev.db")
    os.environ["DATABASE_URL"] = db_url

    from api.src.database import SessionLocal, DriverRosterEntry, Base, engine
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    roster = db.query(DriverRosterEntry).all()
    print(f"\nRoster loaded: {len(roster)} entries")

    ssn_rows   = load_ssn(ssn_path)   if ssn_path   else []
    slack_rows = load_slack(slack_path) if slack_path else []
    print(f"SSN file:   {len(ssn_rows)} employees")
    print(f"Slack file: {len(slack_rows)} members\n")

    ssn_hits = ssn_misses = 0
    slack_hits = slack_misses = 0

    for entry in roster:
        name = entry.payroll_name

        # ── SSN match ────────────────────────────────────────────────────────
        if ssn_rows:
            last4, score = best_ssn_match(name, ssn_rows)
            if last4:
                print(f"  SSN  [{score:.2f}] {name!r:45s}  → PIN {last4}")
                if not dry_run:
                    entry.ssn_last4 = last4
                ssn_hits += 1
            else:
                print(f"  SSN  [NO MATCH {score:.2f}] {name!r}")
                ssn_misses += 1

        # ── Slack match ───────────────────────────────────────────────────────
        if slack_rows:
            uid, display, score = best_slack_match(name, slack_rows)
            if uid:
                print(f"  Slack[{score:.2f}] {name!r:45s}  → {uid} ({display})")
                if not dry_run:
                    entry.slack_member_id    = uid
                    entry.slack_display_name = display
                    entry.slack_verified     = True
                    entry.slack_verified_at  = datetime.utcnow()
                slack_hits += 1
            else:
                # Only log Slack misses at a lower verbosity (most drivers won't be in channel)
                slack_misses += 1

    if not dry_run:
        db.commit()
        print(f"\n✅  Committed to database.")
    else:
        print(f"\n⚠️  DRY RUN — no changes written.")

    db.close()
    print(f"\nSSN:   {ssn_hits} matched / {ssn_misses} unmatched")
    print(f"Slack: {slack_hits} matched / {slack_misses} unmatched (most drivers not in channel — expected)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Import SSN last-4 PINs and Slack IDs into driver_roster")
    parser.add_argument("--ssn",   default="C:/Users/chief/Downloads/SSN (1).xlsx",
                        help="Path to SSN xlsx file")
    parser.add_argument("--slack", default="C:/Users/chief/Downloads/nday-team-room_slack_members (1).xlsx",
                        help="Path to Slack members xlsx file")
    parser.add_argument("--db-url", default=None,
                        help="Database URL (overrides DATABASE_URL env var)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Preview matches without writing to DB")
    args = parser.parse_args()
    run(args.ssn, args.slack, args.db_url, args.dry_run)
