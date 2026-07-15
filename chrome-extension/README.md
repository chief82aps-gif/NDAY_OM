# NDL Hiring — Indeed to Asana Sync

Adds a **Sync** button to Indeed candidate pages. Clicking it scrapes the
candidate's name, contact info, work history, and screener answers, and
pushes them into the "New Day Hiring" Asana board — no copy-pasting.

## Install (once per person, once per computer)

1. Get this folder onto your computer — either clone/pull the
   `NDAY_OM_MODULAR` repo, or have whoever manages it zip and send you
   just the `chrome-extension/` folder.
2. Open Chrome and go to `chrome://extensions`.
3. Turn on **Developer mode** (top-right toggle).
4. Click **Load unpacked**.
5. Select the `chrome-extension/` folder.
6. The extension is now installed — you'll see "NDL Hiring — Indeed to
   Asana Sync" in your extensions list.

## Configure (once per person)

1. Right-click the extension's icon in Chrome's toolbar → **Options**
   (or go to `chrome://extensions`, find it, click **Details** →
   **Extension options**).
2. **Backend API base URL:** `https://nday-om.onrender.com`
3. **Extension Key:** ask whoever set up the backend for this — it's a
   shared secret, do not post it anywhere public (Slack DM, not a
   channel).
4. Click **Save**.

## Use it

1. Go to any candidate's detail page on Indeed.
2. A **Sync to Asana** button appears on the page.
3. Review the scraped info, click it.
4. The candidate is created/updated as a task on the New Day Hiring
   Asana board, tagged with keyword tags and average tenure.

## If something breaks

- **"Sync failed" / nothing happens:** re-check your Extension Key in
  Options — a wrong or missing key is the most common cause.
- **Button doesn't appear:** refresh the Indeed page; the button only
  injects on candidate detail pages.
- **Still stuck:** check with whoever manages the backend — the sync
  endpoint depends on `ASANA_API_TOKEN` / `CANDIDATE_SYNC_KEY` /
  `ASANA_HIRING_PROJECT_GID` being set correctly on the server side.

## Updating

Chrome doesn't auto-update unpacked extensions. When a new version
ships, pull the latest `chrome-extension/` folder and click the reload
icon (⟳) for this extension on `chrome://extensions`.
