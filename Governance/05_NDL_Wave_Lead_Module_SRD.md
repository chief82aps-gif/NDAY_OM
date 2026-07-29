# Wave Lead Module — Design SRD

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** New module — wave-scoped leads, standing team rosters, inter-team competition, nightly roster suggestion/validation
**Status:** Design captured 2026-07-29, following a full requirements conversation + codebase research pass. Not yet built.
**Relationship to existing docs:** `Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md` already reserves this exact expansion — its own §17 open-decisions list states *"whether `schedule_ingest` is ever needed depends on whether the lead pool grows past a size where manual CRUD is still practical — revisit if/when a 3rd+ lead is added."* This is that moment: 4 wave leads + 2 Wave 5 leads + 2 senior leads is 8 lead positions, well past manual-CRUD scale.

---

## 1. Summary of the model

**Waves 1–4** are the standard departure-time waves (today: free-text time strings on `DailyRouteAssignment.wave`/`Cortex.wave`/etc. — no discrete "Wave 1" entity exists in the data model yet, only a display-only bucketing heuristic in `pdf_generator.py`).

**Wave 5** is the 4×4 truck — permanently and only its two dedicated wave leads, no team members ever assigned to it, no Front/Back-Half split, operating independently. Not part of the team competition.

**8 teams** = Waves 1–4 × {Front Half (Sun–Wed), Back Half (Wed–Sun)}. **Wednesday is a real, deliberate overlap day** — both halves are active on it, confirmed explicitly. A driver's Front/Back-Half team is a **stable, standing assignment** (set/changed deliberately by dispatch/HR, not recomputed nightly) — a driver whose actual schedule doesn't cleanly match their team's nominal days still competes as part of that team.

**Wave leads**: one per wave (1–4), shared across both that wave's Front and Back Half teams — **4 total**, not 8. They are rostered onto a route **last**, after all regular team members, taking whatever's left (the "extras"/sweeper framing).

**Senior wave leads** — Spencer and Gallo, specifically — are **independent and roving**, not attached to any wave or team, and **not part of the team competition**. They move from fully-dispatched to on-road dispatching and become the default daily sweep/cleanup crew (tow straps, etc., likely needing an equipped company vehicle — noted as a fleet/ops follow-up, not something this module builds).

**Competition**: each morning, a summary message reports which of the 8 teams is "leading," ranked by **team average of the existing 20/40/40 blended overall score** (`driver_scoring.py`) across that team's standing membership. Awards/bonuses tied to this are a stated future idea, not scoped for build yet.

**System role for v1**: **suggestion + validation only.** Dispatch keeps rostering in whatever process/tool they use today; this module proposes a roster (wave assignment + order, wave lead last) and then checks dispatch's actual roster against its own suggestion, surfacing mismatches for dispatch to confirm-as-is or fix. It does not become the system of record for v1.

**"Work Blocks"** — confirmed to be the *existing* Amazon "Rostered Work Blocks" Excel tab, already ingested nightly (`api/src/ingest/driver_schedule.py`, populates `DriverScheduleEntry.wave_time`). No new ingest format — this module reads data that already lands every night. Still want the promised screenshot to confirm exactly which columns/values let the system infer per-wave headcount.

---

## 2. A real bug this design surfaces (fix bundled into this build, per explicit decision)

Two different, disagreeing driver rankings exist today:
- `driver_scoring.py::compute_driver_scores()` — the 20/40/40 blended score, already powering the Mentoring Dashboard and tiers.
- `route_assignment.py::_load_quality_map()` — a separate, older ranking straight off raw `QualityMetricDriver.overall_standing`/`overall_score` (Amazon's own combined figure), which is what **actually drives route assignment order today**.

This is a pre-existing, already-documented architecture violation (`Governance/SRD_MODULE_ARCHITECTURE_v3.md:143`: *"`route_assignment.py` must call a public `quality.get_driver_rank()`-style function instead of querying `QualityMetricDriver` directly (currently violated)"*). Per explicit decision, this build **fixes it**: `route_assignment.py` will be changed to source ranking from `driver_scoring.py`'s blended score, so "assign in rank order" means the same thing everywhere — the Mentoring Dashboard, the tier badges, and now real route/wave assignment.

---

## 3. Data model — new tables

```python
class WaveLeadRole(Base):
    """Standing wave-lead assignment. Waves 1-4 get exactly one active row
    each (shared across Front/Back Half); Wave 5 gets exactly two. Senior
    wave leads (Spencer/Gallo) are deliberately NOT rows here -- they're
    independent/roving, not wave-scoped."""
    __tablename__ = "wave_lead_roles"
    id = Column(Integer, primary_key=True)
    wave_number = Column(Integer, nullable=False)          # 1-4, or 5 for the 4x4 truck
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=False)
    active = Column(Boolean, default=True)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(String(100))
    __table_args__ = (Index('idx_wave_lead_wave_active', 'wave_number', 'active'),)


class WaveTeam(Base):
    """The 8 fixed teams -- seeded once, essentially static reference data."""
    __tablename__ = "wave_teams"
    id = Column(Integer, primary_key=True)
    wave_number = Column(Integer, nullable=False)          # 1-4 only; Wave 5 has no team concept
    half = Column(String(10), nullable=False)              # "front" | "back"
    __table_args__ = (UniqueConstraint('wave_number', 'half'),)


class WaveTeamMembership(Base):
    """Standing driver -> team assignment. One team per driver; changed
    deliberately by dispatch/HR, never auto-recomputed from a night's
    actual roster."""
    __tablename__ = "wave_team_memberships"
    id = Column(Integer, primary_key=True)
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), unique=True, nullable=False)
    team_id = Column(Integer, ForeignKey("wave_teams.id"), nullable=False)
    assigned_at = Column(DateTime, default=datetime.utcnow)
    assigned_by = Column(String(100))


class WaveRosterSuggestion(Base):
    """The system's proposed wave + order for one date, generated after
    that night's Rostered Work Blocks ingest lands."""
    __tablename__ = "wave_roster_suggestions"
    id = Column(Integer, primary_key=True)
    roster_date = Column(Date, nullable=False, index=True)
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=False)
    suggested_wave = Column(Integer)
    suggested_rank_position = Column(Integer)              # order within the wave, wave lead always last
    is_wave_lead_slot = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint('roster_date', 'roster_id'),)


class WaveRosterDiscrepancy(Base):
    """A flagged mismatch between the suggestion and dispatch's actual
    roster, surfaced each morning for confirm-as-is or fix."""
    __tablename__ = "wave_roster_discrepancies"
    id = Column(Integer, primary_key=True)
    roster_date = Column(Date, nullable=False, index=True)
    roster_id = Column(Integer, ForeignKey("driver_roster.id"), nullable=False)
    discrepancy_type = Column(String(30))                  # wave_mismatch | missing | unexpected | lead_slot_unfilled
    detail = Column(Text)
    resolved = Column(Boolean, default=False)
    resolved_by = Column(String(100))
    resolved_at = Column(DateTime)
    created_at = Column(DateTime, default=datetime.utcnow)
```

No new table for competition standings — team-average score is computed on demand from `WaveTeamMembership` + `driver_scoring.py`, not stored. (A `WaveTeamStandingLog` for historical trend is a reasonable later add-on, not v1.)

---

## 4. What existing code changes

- **`route_assignment.py`** — replace `_load_quality_map()`'s direct `QualityMetricDriver` query + `_STANDING_RANK` with a call into `driver_scoring.py`'s blended score (per §2). This is the riskiest single change — it changes live route-assignment order, not just a new report.
- **`driver_lead_schedule.py`** — `get_current_lead()` today only ever resolves `scope_type="global"` (one lead, DSP-wide). Extend it to resolve per-wave (`scope_type="wave"`, `scope_key="Wave 1"`..`"Wave 4"`, plus `"Wave 5"` for the two 4x4 leads) — the schema already supports this, only the resolution code needs to change. `WaveLeadRole` above becomes the standing source; `DailyLeadAssignment` keeps its existing manual-override-for-a-specific-date behavior on top of it.
- **`rostering.py`** — the "Talk to My Lead" button DM logic (`_build_driver_dm()`, 3 call sites) currently resolves one global lead; needs to resolve the driver's actual wave's lead instead. Straightforward once `get_current_lead()` supports per-wave lookup.
- **New module** (own route file + the 5 tables above) — following `Governance/SRD_MODULE_ARCHITECTURE_v3.md`'s hub-and-spoke rule: reads `rostering.py`'s wave/route data and `driver_scoring.py`'s ranking only through those modules' public functions, never raw table queries. RBAC decorator applied from day one (both `rostering.py` and `driver_lead_schedule.py` currently lack one — explicitly logged as debt, not precedent to repeat).

---

## 5. Nightly pipeline (v1: suggestion + validation only)

1. Rostered Work Blocks ingest lands (existing pipeline, unchanged) → tomorrow's per-driver wave times known.
2. New step: for each wave, pull that wave's scheduled drivers, join to `WaveTeamMembership` + `driver_scoring.py` rank, produce a `WaveRosterSuggestion` row per driver (order within wave, wave lead slot placed last).
3. Suggestion surfaced to dispatch (Slack message and/or a read view) — not yet built which surface, see open items.
4. Dispatch rosters as they do today, in their existing tool/process.
5. New step: compare dispatch's actual roster (`DailyRouteAssignment` once it lands) against the suggestion, write `WaveRosterDiscrepancy` rows for mismatches.
6. Morning: DM dispatch a "you missed these — leave it or fix it?" summary of unresolved discrepancies.
7. Morning: DM each driver their wave's lead (via the extended `driver_lead_schedule.py`).
8. Morning: post the team-competition standings message.

**Future, explicitly not v1**: auto-managed per-wave Slack channels; a "Contact My Wave Lead" button on the driver Home tab. Note: the button already has a working, tested foundation — `LEAD_ROUTING_ACTIVE`'s existing "Talk to My Lead" flow in `rostering.py`/`slack_interactions.py` — it just needs the per-wave lead resolution from §4 rather than being built from scratch.

---

## 5.0. Work Blocks headcount inference — confirmed not buildable as originally envisioned (2026-07-29)

Checked the real downloadable "Rostered Work Blocks" export (`Week-31-Schedule.xlsx`) against the live Amazon portal screenshot that inspired this idea. Conclusion, confirmed directly with the user:

- The **downloadable file** is a per-driver grid (one row per driver, one column per date, each cell = that day's assigned service type + time) plus a day-level `Total Rostered` count (e.g. 42/47/45/48/0/0/0 for Sun–Sat). This is exactly what `driver_schedule.py::_parse_work_blocks_tab()` already ingests — no new ingest needed.
- The **live portal view** additionally breaks out *unassigned* blocks by service type + time + count — this is what would be needed to compute "how many routes are still needed in Wave N" — but this breakdown is **portal-only, not present in the downloadable export**, and the portal itself falls under this project's standing rule that Amazon portals (Cortex/Fleet/DVIC/WST/driver schedule) can never be scraped or automated.
- **Confirmed twice** (both the weekly downloadable export and a single-day live portal screenshot, 2026-07-29): the itemized unassigned-by-service-type/time/count breakdown exists only in the live Amazon "Scheduling" portal, with no download available for it at any granularity. **Net: true per-wave target headcount (assigned + gap) cannot be automatically inferred with current data access, full stop.**

### 5.0.1. Resolution: team headcount doesn't need this data at all (key insight, 2026-07-29)

Raised directly by the user: since NDL doesn't control how many routes Amazon assigns per wave day to day, how can a "team" have a stable size at all? The answer is that **operational wave-of-the-day and standing team are deliberately two different things, already decoupled by how this was built**:

- **Standing team** (`WaveTeamMembership`) — fixed, for competition/mentoring/discipline attribution only. Never recomputed from a day's actual roster. Team size for scoring purposes was never meant to track daily operational headcount.
- **Operational wave-of-the-day** (who a driver actually gets routed with, and therefore whose lead they reach on Zello/Slack) — resolved fresh every day from the driver's *real* assignment (`_resolve_wave_lead_for_driver()` in `rostering.py`, via `wave_number_for_assignment()` on their actual `DailyRouteAssignment`), completely independent of their standing team.

So a driver "spilling over" from their nominal Wave 2 into Wave 3 because that's where Amazon's real volume put them that day is **normal and expected** — they correctly reach Wave 3's lead that day (operational), while their team average score/mentoring/discipline attribution stays with their standing Wave 2 team (competition) regardless. Fixed 2026-07-29: `check_roster_discrepancies()` no longer flags this spillover as a `wave_mismatch` discrepancy — it isn't an error, so dispatch shouldn't be nagged about it. `generate_wave_roster_suggestion()` stays as built — standing team + blended rank, filtered to who's scheduled that night — since a headcount cross-check was never actually necessary for this to work correctly.

## 5.0.2. Dynamic wave channels — "PTT-lite" via Slack's native voice clips (added 2026-07-29)

User wanted push-to-talk voice communication layered on the wave/team structure. Researched Zello Work's API directly (real, documented — `channel/add`, `user/addto`/`removefrom`, channel roles) but it's paid-tier only and ruled out on cost. True live PTT would need a separate native app (see the pre-existing `Governance/02_NDL_Android_PTT_Messaging_App.md`, which already concluded Slack has no embeddable PTT SDK at all) — a genuinely bigger, separate project, logged to `project_ptt_future_options.md` rather than built now.

**What shipped instead ("Option A")**: Slack's own client already has native voice-clip recording — no new app or SDK needed for the audio itself. `sync_wave_channels()` auto-creates one Slack channel per wave (`wave-1-team` … `wave-5-4x4-truck`) and keeps membership synced to whoever's **actually working that wave today** (operational, via `wave_number_for_assignment()` on real `DailyRouteAssignment` rows — same operational/standing split as §5.0.1), plus that wave's lead(s). A driver just opens their current wave's channel and records a voice clip like normal Slack usage — it reaches exactly the right group automatically, no extra step. Gated by `WAVE_PTT_CHANNELS_ACTIVE` (default false); syncs every 60s from 6-11 AM Pacific once enabled (idempotent, safe to re-run repeatedly as rosters get finalized through the morning).

## 5.1. Follow-up found during build (not yet fixed)

`rostering.py` has **two separate driver-facing DM flows**, and only one was wave-aware even after this build's fix:
- The day-of shift assignment DM (`_build_driver_dm`, off `DailyRouteAssignment`) — **fixed** in this build to resolve each driver's own wave's lead via `_resolve_wave_lead_for_driver()`.
- The night-before schedule DM (`_build_shift_dm`, off `DriverScheduleEntry`) — **still uses the original hardcoded `_wave_lead_name(shift_date)` weekday dict** (Spencer/Fabian split), the exact thing `driver_lead_schedule.py` was originally built to replace. It never even adopted the single-global-lead system, let alone the new per-wave one. At least 10 call sites in `rostering.py` still call `_wave_lead_name()` directly. Flagging as a known gap — not fixed as part of this build given time constraints, but should be reconciled so a driver doesn't see two different "Wave Lead" names depending on which DM they're looking at.

## 6. Open items before/while building

- **Exact Work Blocks columns for inferring per-wave headcount** — need the screenshot to confirm.
- **Where the roster suggestion actually surfaces to dispatch** (Slack post? a website page? both?) — not yet decided.
- **Realistic timeline vs. the stated end-of-week target**: this is a genuinely large build — new ranking unification (touches live route assignment), 5 new tables, a new ingest cross-reference step, new Slack messaging, and a validation/discrepancy loop. Recommend treating "team competition message + standing team rosters + per-wave lead DMs" as the Friday-achievable slice, with the full suggestion/validation/discrepancy pipeline as a fast-follow rather than all landing by Friday. This needs explicit sign-off before work starts, not assumed.
