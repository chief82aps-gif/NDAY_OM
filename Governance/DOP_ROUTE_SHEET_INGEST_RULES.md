# DOP & Route Sheet Ingest Rules

> Discovery: Browse all governance docs in [Governance Index](README.md).

## 1. Core Principle: We Do Not Control the File

Both the DOP (Day of Plan) and the Route Sheet are **submitted by Amazon**, not
generated or named by NDAY or the DSP. That means:

- **We cannot rely on a filename convention.** Whoever at the station uploads
  the file (or whichever Amazon system exports it) can save it under any
  name, in any case, with any spacing. A detector that requires specific
  words in the filename (e.g. `"dop"` or `.xlsx`) will eventually miss a real
  file — this already happened once (2026-07-16: `NDAY.csv` landed on time,
  in the right channel, and was silently invisible because detection
  required `.xlsx`).
- **We cannot rely on a fixed file type for the DOP.** Amazon exports it as
  "some spreadsheet" — that could be `.xlsx`, the legacy binary `.xls`, or
  `.csv`, and which one shows up varies by who generated it. The system must
  be able to ingest *any* spreadsheet-shaped file, not a whitelist of
  extensions.
- **The Route Sheet is the one constant: it is always a PDF.** Unlike the
  DOP, this format has never varied.

Given that, the two signals we *can* trust are **where** the file lands
(channel) and **when** it lands (time window) — not what it's called or its
literal extension. Detection logic should be built around those two anchors
first, and treat filename keywords as a weak, best-effort hint at most —
never a hard requirement.

---

## 2. File Format Rules

### 2.1 DOP

- **Accepted**: any spreadsheet-shaped file — `.xlsx`, `.xls`, `.csv`, or any
  other extension a spreadsheet gets saved under.
- **Parsing is content-based, not extension-based.** `read_tabular_file()`
  (`api/src/column_mapping.py`) sniffs the actual file bytes (Excel zip
  signature `PK\x03\x04`/`PK\x05\x06`, legacy OLE signature
  `\xd0\xcf\x11\xe0...`) and picks `pd.read_excel` or `pd.read_csv`
  accordingly — the extension in the filename is never trusted for parsing.
  This part of the pipeline already complies with this doc.
- **Detection is currently NOT fully content-based** — see §5 (Known Gap).

### 2.2 Route Sheet

- **Accepted**: `.pdf` only. This has never varied and isn't expected to.

---

## 3. Detection Rules — Channel & Time Window

### 3.1 Channel (Location)

- DOP and Route Sheet files are scanned from **`#dlv3-nday-info`**
  (`NOTIFY_CHANNEL`, default `C0AF48TPAMV`) via `scan_channel_for_files()`
  in `api/src/routes/daily_notify.py`.
- This is a *different* channel from `#nday-operations-management`
  (`C0BE4ALL1EX`), which handles Cortex, Fleet, DVIC, and Scorecard uploads.
  Do not conflate the two — a DOP/Route Sheet upload to the wrong channel
  will not be seen by this scanner (the separate misrouted-file watcher in
  `ops_ingest.py` covers the *other* channel's file types, not this one).

### 3.2 Time Window

- The automatic scan (`_daily_notify_loop()` in `api/main.py`) runs every
  10 minutes between **8:00–10:00 AM Pacific**. Outside that window, new
  files sit undetected until the next morning's window opens, unless
  manually triggered (`POST /daily-notify/check` or `/daily-notify/rerun`).
- This window is a legitimate, working control — the 2026-07-16 miss
  (file uploaded 8:37 AM, well inside the window) was a detection-logic bug,
  not a timing bug. Don't re-diagnose a future miss as a timing issue without
  first checking whether the file was actually visible to the scanner.

---

## 3A. There Are Two Independent Detection Pipelines — Know Which One You're Debugging

DOP/Route Sheet detection is not a single code path. Two parallel systems
both watch for these files, independently, and don't talk to each other:

| | `ops_ingest.py` | `daily_notify.py` |
|---|---|---|
| Scanner | `scan_ops_channel()` | `scan_channel_for_files()` |
| Classification | **Content-based** (`_classify()` — sniffs extension + filename/message keywords, with an explicit fallback: an unrecognized `.csv` in `#dlv3-nday-info` is assumed DOP by convention) | **Filename-keyword-based** (requires `"dop"`/`"day"` in the name — see §5) |
| Tracked in | `OpsIngestJob` table | `SlackIngestLog` table |
| Feeds | The 9 AM reminder (§3B) below; manual `/ops-ingest/jobs/{id}/ingest` | `DailyRouteAssignment` build, driver DMs, showtime/assignment matrix |
| Still requires a second, manual "ingest" click | Yes | No — ingests automatically once detected |

**A file can be correctly detected by one pipeline and invisible to the
other at the same time.** That's exactly what happened 2026-07-16:
`ops_ingest.py` classified `NDAY.csv` as `dop` at 8:37 AM (correct,
content-based, no bug) — but `daily_notify.py`'s separate scanner still
missed it (filename-keyword bug, since fixed). Don't assume "the reminder
didn't fire, so detection is broken" or vice versa — check the specific
pipeline the symptom is actually in before diagnosing.

### 3B. The 9 AM Reminder (already implemented)

`mgt_reminders.py` DMs every `#nday-mgt` member, every 5 minutes, from
**9:00–10:00 AM PT**, for any of: DOP file, Route Sheets file, Cortex
Routes file, Fleet/Vehicle Data — until a matching `OpsIngestJob` row
appears (i.e. until `ops_ingest.py`'s pipeline, not `daily_notify.py`'s,
sees it). Always active, no feature flag. Manual trigger:
`POST /mgt-reminders/check`.

---

## 4. Cross-Validation Rule — Cortex Is the Source of Truth for Route Codes

**Status: implemented 2026-07-16** — `check_route_code_reconciliation()`
(`api/src/routes/daily_notify.py`), called as step 4A of `check_and_notify()`.

- **Cortex is always correct on route codes.** If the DOP's or the Route
  Sheet's route-code set disagrees with Cortex's, the DOP or Route Sheet is
  the one that's wrong — never Cortex.
- **Threshold**: if more than **10%** of route codes differ between
  (DOP vs. Cortex) or (Route Sheet vs. Cortex), that file should be treated
  as unreliable for that day.
- **On breach**: the system must **not** silently proceed with mismatched
  data. It should kick back a prompt (to `#nday-mgt`, matching the pattern
  used elsewhere for ops-manager prompts) identifying *which* file is the
  outlier (DOP or Route Sheet) and stating that the station needs to correct
  and resubmit it.
- **Comparison basis**: route *codes* specifically (not packages, wave, or
  other fields) — this rule is about whether the files are talking about the
  same set of routes at all, not about field-level accuracy.

---

## 5. Known Gaps / Follow-Up Work

- **Detection still checks for `.xlsx`/`.csv` + a `"dop"`/`"day"` keyword in
  the filename** (`scan_channel_for_files()`), rather than being fully
  content-based the way parsing already is. The 2026-07-16 fix added `.csv`
  to the extension check, but per §1 this is still a keyword-based detector,
  not a channel+time-window-only one. A more robust version would treat any
  non-PDF file landing in `#dlv3-nday-info` during the scan as a DOP
  candidate, full stop.
- **Resolved 2026-07-16/17: Route Sheet data used to be lost entirely if
  DOP arrived later than the Route Sheet on the same day** (exactly what
  happened 2026-07-16 — the DOP `.csv` detection bug delayed DOP by
  hours, so by the time it landed the already-ingested Route Sheet's
  van/wave/stage data had nowhere to go, since nothing persisted it past
  the single `check_and_notify()` call that parsed it). Fixed by adding
  `RouteSheetEntry` (`api/src/database.py`), persisted the same way
  DOP/Cortex already are — see `get_latest_route_sheet_rows()`.
  `build_daily_assignments()` now merges from this table instead of
  relying solely on the same-call `pdf_data` parameter.

---

## 6. This Document Governs

- DOP channel scanning: `scan_channel_for_files()` (`api/src/routes/daily_notify.py`)
- DOP parsing: `parse_dop_excel()` (`api/src/ingest/dop.py`), via
  `read_tabular_file()` (`api/src/column_mapping.py`)
- Route Sheet parsing: `parse_route_sheet_pdf()` (`api/src/routes/daily_notify.py`)
- Route Sheet persistence: `RouteSheetEntry` / `get_latest_route_sheet_rows()` (`api/src/database.py`)
- The automatic scan window: `_daily_notify_loop()` (`api/main.py`)
- The 9 AM reminder: `mgt_reminders.py`
- Cross-validation (§4): `check_route_code_reconciliation()` (`api/src/routes/daily_notify.py`)
- Manual "Re-Run Route Assignments" (covers post-initial-run corrections,
  notifies drivers of new/changed/removed assignments): `rerun_route_assignments()`
  (`api/src/routes/daily_notify.py`), Dispatch Home button
- Generalized auto-ingest (a file only needs to be *placed* in the right
  channel, no separate confirmation click): `run_ops_auto_ingest()`
  (`api/src/routes/ops_ingest.py`) — covers `dvic`/`driver_schedule`/
  `fleet`/`quality_csv`/`safety_events`/`dsp_scorecard`/`tenured_workforce`;
  deliberately excludes `dop`/`cortex`/`route_sheets` (see §3A)

**Any changes to these rules must**:

1. Update this document
2. Update relevant code comments
3. Include a version/date stamp in the commit message

---

## 7. Quick Reference: Rules Checklist

- [ ] Never require a specific filename pattern to accept a DOP or Route Sheet file
- [ ] DOP: accept any spreadsheet-shaped file; parse by content-sniffing, not extension
- [ ] Route Sheet: PDF only
- [ ] Detection scoped to `#dlv3-nday-info`, not `#nday-operations-management`
- [ ] Automatic scan runs 8:00–10:00 AM Pacific, every 10 min; manual rerun available outside that window
- [ ] 9 AM reminder (mgt_reminders.py) already covers "not received in time" nagging — don't build a duplicate
- [ ] Cortex route codes are authoritative — DOP/Route Sheet are presumed wrong on disagreement, never Cortex
- [ ] >10% route-code mismatch (DOP vs. Cortex, or Route Sheet vs. Cortex) → kick back a prompt naming the bad file, don't proceed silently *(not yet implemented)*
