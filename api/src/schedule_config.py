"""
Shared tunable constants for the night-before driver schedule pipeline.

SHOWTIME_OFFSET_MINUTES is read independently by rostering.py (_calc_showtime,
single wave string) and ingest/driver_schedule.py (_calculate_show_times,
wave-consolidated groups) — those two algorithms operate on different shapes
and aren't merged, but both must shift by the same offset, so the value
itself lives here once instead of as two independent literals.
"""
import os

SHOWTIME_OFFSET_MINUTES = int(os.getenv("SHOWTIME_OFFSET_MINUTES", "25"))
SCHEDULE_GAP_CHECK_HOUR = int(os.getenv("SCHEDULE_GAP_CHECK_HOUR", "21"))
