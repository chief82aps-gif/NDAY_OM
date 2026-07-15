# NDL Slack Dashboard App — Concept & Build Spec

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** Track 1 — Slack App Home dashboard
**Status:** Concept / pre-build
**Purpose of this doc:** Portable spec to move into VS Code / Claude Code for implementation.

---

## 1. Summary

A driver-facing control panel delivered as a **Slack App Home tab**, backed by the NDL platform. It centralizes daily driver operations and **all HR functions** (RTO, callout, injury reporting, crash/accident reporting, and related forms) into one Slack-native surface.

Chosen over a **Slack Canvas** because Canvas is a document format (rich text, checklists, links, embeds) and does **not** support interactive buttons. The App Home tab fully supports Block Kit interactive components.

**Strategic note:** This Slack app is Track 1 of a two-track plan. It is intended as the **speed-to-implementation** option and will eventually be **replaced by the custom Android app** (Track 2, separate doc). It is still worth building now for fast rollout and as a fallback / office-staff surface.

---

## 2. Why Slack App Home (not Canvas)

| Surface | Interactive buttons | Per-user dynamic content | Verdict |
|---|---|---|---|
| Slack Canvas | No | No | Document only — rejected |
| Slack App Home tab | Yes (Block Kit) | Yes (`views.publish` per user) | **Selected** |
| Block Kit message | Yes | Limited | Used for alerts/pings |
| Modal (`views.open`) | Yes | N/A | Used for forms/quick input |

**Key benefit:** Formatting/rendering is Slack's responsibility. One set of Block Kit JSON renders natively on iOS, Android, and desktop — no per-phone layout work. If it works in Slack, it works on every phone that runs Slack.

---

## 3. Access model

- Driver opens the Slack mobile app → taps the **NDL Dispatch** app → lands on the **Home** tab.
- Slack provides surrounding chrome (status bar, app header, Messages / Home / About tabs). The app controls only the Home tab contents.

**Requirements**
- Driver is a member of the NDL Slack workspace.
- App installed to the workspace with the **Home tab feature enabled** (App config at api.slack.com).
- Each driver has a provisioned Slack account → ties into onboarding (fits alongside **HRM-018**; note existing shared-credential security item there).

**Discoverability**
- Start-of-shift bot DM containing a deep link: `slack://app?team=T...&id=A...&tab=home`.
- Optional slash command: `/myroute`.
- If NDL issues company devices: pre-install and pre-authenticate Slack via **MDM**.

---

## 4. Dashboard layout (Home tab, top → bottom)

1. **Header** — driver name, date, station (e.g., DBX3), shift-state badge (Pre-shift / On route / Returning).
2. **Metric cards (2-col)** — Route, Van, Stops, Wave time. Built from `section` blocks with `fields`.
3. **Shift actions** — Confirm callout (primary/green), Vehicle inspection, Claim van keys, Device checkout.
4. **On the road** — Report bad address, Missing package, Roadside/breakdown (danger/red), Message dispatch.
5. **HR & reporting** — see Section 5 (RTO, Callout, Injury, Crash/Accident, etc.).
6. **Today's checklist** — Clock in via ADP, Netradyne camera check, Load van & scan totes, Fuel card confirmation, Start GPS tracking.
7. **Resources** — Handbook, Dispatch line, Incident form, My scorecard.

**Design caution:** Home tabs re-render top-to-bottom on `views.publish`. Keep time-sensitive actions (roadside, missing package, injury) high, not buried under resources.

---

## 5. HR functions (required in this app)

All HR workflows are surfaced as buttons on the Home tab that open **Block Kit modals** (`views.open`) for structured input, then write to the NDL backend (and integrate with ADP / existing forms where relevant).

| Function | Trigger | Modal captures | Backend action |
|---|---|---|---|
| **Request Time Off (RTO)** | "Request time off" button | Date range, type (PTO/UTO/unpaid), reason, notes | Create RTO record; route for approval; reflect in scheduling |
| **Call Out** | "Call out" button | Date, reason, expected return, contact method | Create callout record; notify dispatch; update roster |
| **Injury Report** | "Report injury" button | Date/time, location, body part, description, witnesses, medical attention y/n | Create injury record; trigger required OSHA/HR workflow; notify management |
| **Crash / Accident Report** | "Report crash" button | Date/time, location, vehicle #, other party info, injuries y/n, photos, description | Create accident record; notify safety/management; kick off insurance/Amazon reporting |
| **Vehicle inspection** | "Vehicle inspection" button | Checklist + defects + photos | DVIR-style record; flag OOS conditions |
| **Incident / general report** | "Incident form" button | Category, description, photos | Route to appropriate owner |

**Notes**
- These map to NDL's existing policy/form library (HRM-001…HRM-025, OPS-001…OPS-014, handbook forms). Reuse those as the field source of truth.
- Injury and crash reports are **compliance-sensitive** — ensure required fields, timestamps, and immutable audit trail. Consider mandatory manager notification on submit.
- Multi-step forms: Block Kit modals support multiple inputs and can chain (submit one modal → open a follow-up). Use for longer reports.

---

## 6. How it works technically

- Every button is a Block Kit `button` inside `section` / `actions` blocks.
- Tapping a button sends an **interaction payload** to the NDL backend, which does the real work (confirm callout, open a modal, message dispatch, write HR record).
- The Home tab is **dynamic and per-user** via `views.publish` — each driver sees their own route, van, checklist state, and HR status. Re-publish to update shift state (Pre-shift → On route → Returning).
- **Button styling is limited** to three options: default, `primary` (green), `danger` (red). Semantic coloring in mockups is achievable within this constraint.
- **Checklist completion:** use Block Kit `checkboxes` (reports state back) or render status from backend and re-publish. No native "tap-to-complete" on Home tabs.
- **Forms:** `views.open` modals for all HR/reporting input; validate required fields server-side.

---

## 7. Backend architecture

- **NDL backend is the source of truth.** The Slack Home tab is a *view* onto it.
- Slack reads/writes via its Web API and Events API; backend persists records and integrates with **ADP, Amazon Logistics API, Netradyne (Cortex), MDM, Google Maps**.
- All button interactions flow: Slack → interaction payload → NDL backend → (DB write + integration) → `views.publish` refresh / modal response.
- Design so that when Track 2 (Android app) arrives, it is simply **another view** onto the same backend and same records.

---

## 8. Messaging within the Slack app

- **App/bot messages** (dispatch pings, alerts) live in the app's **Messages tab** — one tap from Home (not a separate app). Home tab can surface a "N new from dispatch" `section` that deep-links to Messages.
- **Other people/channels** (team channel, dispatcher DMs) live elsewhere in Slack, outside the NDL Dispatch app.
- **No composite window:** Slack does not expose a layout showing dashboard + live chat pane together. Closest is a **modal** ("Message dispatch" → `views.open` text field → send → returns to dashboard).
- This limitation is a primary reason for Track 2.

---

## 9. Mockup caveats (stylized vs. real Slack)

- Full-width filled buttons in mockups render smaller/pill-ish in real Slack and stack more vertically on a narrow phone.
- Rounded "resource chips" are not native Block Kit — those are small buttons or a `context` block with links.
- Metric cards (Route/Van/Stops/Wave) are real — built from `section` blocks with `fields` in a two-column grid.

---

## 10. Build checklist / next steps

- [ ] Create Slack app at api.slack.com; enable App Home (Home tab) and interactivity.
- [ ] Define OAuth scopes (chat:write, im:write, commands, users:read, etc.).
- [ ] Stand up interaction endpoint (request URL) on NDL backend.
- [ ] Author Block Kit JSON for Home tab (`views.publish` payload).
- [ ] Build modals (`views.open`) for RTO, Callout, Injury, Crash, Inspection, Incident.
- [ ] Wire interaction handlers → backend records → integrations (ADP, etc.).
- [ ] Per-user publish logic driven by shift state + route data.
- [ ] Onboarding step: provision Slack account + workspace invite (tie to HRM-018).
- [ ] MDM: pre-install/pre-auth Slack on company Android devices.
- [ ] Start-of-shift bot DM with deep link to Home tab.

**Immediate first artifact for VS:** the Block Kit `views.publish` JSON for the Home tab + interaction-handler stubs for each button, including the HR modal definitions.

---

## 11. Open decisions

- Confirm HR field sets against existing NDL forms (HRM/OPS library) — exact required fields per report.
- Approval routing for RTO/callout — who approves, escalation, SLA.
- Compliance requirements for injury/crash records (retention, audit, mandatory notifications).
- How much shift-operations detail lives in Slack vs. deferred to Track 2.
