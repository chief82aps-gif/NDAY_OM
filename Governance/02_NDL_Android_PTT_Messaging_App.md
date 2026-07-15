# NDL Android App — PTT & Messaging System

**Project:** New Day Logistics (NDL / NDAY) Driver Platform
**Component:** Track 2 — Custom Android app (fully controllable)
**Status:** Concept / pre-build
**Purpose of this doc:** Portable spec to move into VS Code / Claude Code for implementation.

---

## 1. Summary

A **fully customizable native Android app** for NDL's company-issued driver phones. It combines the driver dashboard with a **built-in messaging system** and **push-to-talk (PTT)** voice. Because NDL controls the hardware and only targets Android, this app has no cross-platform or Slack-rendering constraints — full control over layout, including a **docked chat/PTT panel beneath the dashboard** (the composite view Slack cannot provide).

**Strategic role:** This app is intended to **eventually replace the Slack app (Track 1)**. Track 1 exists for speed-to-implementation; Track 2 is the long-term primary tool.

---

## 2. Why a custom app (vs. staying in Slack)

- **Full layout control** — dashboard + live message pane + PTT in one screen, split-screen or docked, however designed.
- **No per-phone formatting concern** — single target OS (Android), single device fleet via MDM.
- **PTT** — not possible in Slack; native to this app.
- **Own message model** — threading, read receipts, route/stop context, dispatch routing all under NDL control.
- **Tradeoff:** more to build and maintain than Slack Block Kit. Justified by PTT + docked layout + single controlled fleet.

---

## 3. Platform & deployment

- **Target:** Android only, NDL company-issued phones.
- **Distribution:** via **MDM** (managed deployment, no public Play Store needed) — pre-install and auto-configure.
- **Auth:** driver signs in against NDL backend (OAuth / platform SSO); device enrollment through MDM.
- **Framework:** native Android (Kotlin) recommended for PTT/audio control and MDM integration; cross-platform frameworks (Flutter/React Native) possible but native is safest for low-latency audio.

---

## 4. Feature scope

### 4.1 Dashboard (parity with Slack Track 1, then beyond)
- Header: driver, date, station, shift-state.
- Metric cards: Route, Van, Stops, Wave.
- Shift actions: Confirm callout, Vehicle inspection, Claim van keys, Device checkout.
- On the road: Bad address, Missing package, Roadside/breakdown, Message dispatch.
- HR & reporting: RTO, Callout, Injury, Crash/Accident, Incident (same functions as Track 1 — see Slack doc §5).
- Checklist: ADP clock-in, Netradyne check, load/scan, fuel card, GPS start.
- Resources: Handbook, dispatch line, scorecard.

### 4.2 Messaging (built-in)
- Text messaging: dispatch ↔ driver, with route/stop context attached.
- Threading, read receipts, delivery status.
- Persistent history in NDL backend.
- **Docked panel** under dashboard controls — dashboard + comms visible together.

### 4.3 Push-to-talk (PTT)
- Hold-to-talk voice channel for driver ↔ dispatch (and optionally driver ↔ driver / group).
- See Section 6 for implementation options.

---

## 5. Messaging architecture — two paths

**Option A — Slack as backend (embed / reuse Slack messages)**
- Slack offers **no embeddable chat SDK or drop-in widget**. You cannot place a live Slack conversation inside a third-party Android UI.
- You *can* call Slack APIs (`conversations.history` to read, `chat.postMessage` to post, Events API to subscribe) and render them in **your own UI**.
- Net: you'd be **reimplementing the chat frontend** against Slack's API — most of the effort of building your own, minus the control, plus Slack's rate limits/message model.
- **Wins only if** keeping everyone (drivers + office + adjacent staff) on one Slack message store matters more than PTT and layout.

**Option B — Own messaging system (recommended)**
- Build text + PTT on the NDL backend directly.
- Realtime layer: WebSockets or a managed realtime service.
- Persistence in NDL backend; full control of model, routing, context.
- Enables the docked/side-by-side layout and native PTT.
- **Recommended** given PTT goal + layout control + existing backend. Option A's main draw (reusing Slack) evaporates once you're building your own UI against its API anyway.

**Optional bridge:** If desired, mirror dispatch messages between the NDL system and Slack so office staff on Slack and drivers on the app stay in sync. Optional, can come later.

---

## 6. PTT implementation options

| Approach | What it is | Latency | Effort | Notes |
|---|---|---|---|---|
| **Purpose-built PTT SDK** (e.g., Zello) | Workforce/driver PTT platform with SDK | Low (true walkie-talkie) | Medium | Built exactly for this use case; may involve licensing/subscription |
| **Realtime audio SDK** (Agora / LiveKit / Twilio) | General realtime-voice provider, configured as PTT channels | Low | Medium–High | Flexible, you build the PTT UX on top |
| **Record-and-send voice clips (DIY)** | Hold button → record short clip → send as voice message over your message channel | Higher (not live) | Low | "Push-to-send-voice-clip" rather than true live PTT; often good enough for dispatch |

**Guidance:** For true low-latency walkie-talkie behavior, a purpose-built SDK (Zello-style) or realtime audio provider earns its keep. If near-real-time voice notes suffice, the DIY record-and-send route is far cheaper and simpler and can ship first, with true PTT added later.

---

## 7. Backend architecture

- **NDL backend is the single source of truth** for both Track 1 (Slack) and Track 2 (Android app).
- Android app is **another view** onto the same records and integrations (**ADP, Amazon Logistics API, Netradyne/Cortex, MDM, Google Maps**).
- Messaging/PTT services attach to the same backend so dispatch, route context, HR records, and comms are unified.
- Because both tracks share the backend, migrating drivers from Slack → Android app is a **client swap, not a data migration**.

---

## 8. Migration plan (Slack → Android)

1. Ship Track 1 (Slack) for immediate rollout.
2. Build Track 2 backend services (messaging, PTT) alongside, reusing the shared source of truth.
3. Build Android app with dashboard parity + messaging + PTT.
4. Pilot on a subset of NDL Android devices via MDM.
5. Once stable, transition drivers off the Slack app; optionally keep Slack for office staff or as fallback.
6. Retire or repurpose the Slack app per operational need.

---

## 9. Build checklist / next steps

- [ ] Decide messaging path: **Option B (own system)** vs. Option A (Slack backend). Recommended: B.
- [ ] Decide PTT approach: purpose-built SDK vs. realtime audio SDK vs. record-and-send. Evaluate cost/latency.
- [ ] Define Android auth against NDL backend + MDM enrollment flow.
- [ ] Backend: realtime messaging service (WebSocket / managed) + persistence schema.
- [ ] Backend: PTT audio pipeline (per chosen approach).
- [ ] App: dashboard UI parity with Track 1.
- [ ] App: docked messaging panel + PTT control.
- [ ] Reuse HR modals/records from shared backend (RTO, Callout, Injury, Crash, Incident).
- [ ] MDM packaging + fleet deployment.
- [ ] Pilot → rollout → Slack retirement.

**Immediate first artifacts for VS:** (1) messaging path decision doc with cost/latency for PTT options; (2) backend data model for messages + PTT; (3) Android app screen scaffold (dashboard + docked chat/PTT).

---

## 10. Open decisions

- Messaging: Option A vs. B (recommend B).
- PTT: which provider/approach; budget and latency requirements.
- Whether PTT is driver↔dispatch only, or also driver↔driver / group channels.
- Whether to build the optional Slack ↔ NDL message bridge.
- Native Android (Kotlin) vs. cross-platform framework.
- Timeline for retiring the Slack app after Android rollout.
