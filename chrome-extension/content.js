/**
 * NDL Hiring Sync — content script.
 *
 * Calibrated 2026-07-14 against live Indeed employer dashboard DOM (via
 * DevTools inspection during setup). The triage buttons are identified by
 * data-testid="ApplicantSentiment-yes|maybe|no" with selection state on
 * data-is-selected / aria-pressed — these are stable attributes, not
 * hashed CSS class names, so this should hold up across minor Indeed
 * front-end rebuilds. Row/name/experience extraction still walks the DOM
 * heuristically (Indeed doesn't expose a stable "row" test-id), so those
 * are the more likely spots to need a future tweak.
 */

const SENTIMENT_ATTR_PREFIX = "ApplicantSentiment-";
const PHONE_RE = /(\+?1[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}/;
const EMAIL_RE = /[\w.+-]+@[\w-]+\.[\w.-]+/;

function hasContactInfo(screenerAnswers) {
  const combined = (screenerAnswers || []).map((a) => a.answer).join(" ");
  return PHONE_RE.test(combined) || EMAIL_RE.test(combined);
}

function getStoredConfig() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(["apiBase", "extensionKey"], (result) => {
      resolve({
        apiBase: result.apiBase || "https://nday-om.onrender.com",
        extensionKey: result.extensionKey || "",
      });
    });
  });
}

function isDetailPage() {
  return !!document.getElementById("candidateProfileContainer");
}

/** Group every triage button on the page by its immediate shared parent —
 * that parent is one "decision group" (the ✓ / ? / ✗ trio for one candidate),
 * whether there's one on a detail page or a dozen on a list page. */
function getDecisionGroups() {
  const buttons = Array.from(
    document.querySelectorAll(`[data-testid^="${SENTIMENT_ATTR_PREFIX}"]`)
  );
  const groups = new Map();
  for (const btn of buttons) {
    const parent = btn.parentElement;
    if (!parent) continue;
    if (!groups.has(parent)) groups.set(parent, []);
    groups.get(parent).push(btn);
  }
  return Array.from(groups.values());
}

function isButtonSelected(btn) {
  return (
    btn.getAttribute("data-is-selected") === "true" ||
    btn.getAttribute("aria-pressed") === "true"
  );
}

function readDecisionFromGroup(buttons) {
  for (const btn of buttons) {
    if (!isButtonSelected(btn)) continue;
    const testid = btn.getAttribute("data-testid") || "";
    if (testid.endsWith("yes")) return "accept";
    if (testid.endsWith("maybe")) return "undecided";
    return "reject"; // anything else selected in this trio is the reject button
  }
  return null; // no selection made yet
}

/** Walk up from the button group looking for an ancestor that contains
 * both a checkbox (list-row marker) and a heading (candidate name) — that's
 * the candidate "card". Falls back to a fixed number of levels up if no
 * such ancestor is found (e.g. on the single-candidate detail view, which
 * has no row checkbox). */
function findCandidateCard(buttonGroupParent) {
  let node = buttonGroupParent;
  for (let i = 0; i < 8 && node; i++) {
    const hasCheckbox = node.querySelector('input[type="checkbox"]');
    const hasHeading = node.querySelector("h1, h2, h3");
    if (hasCheckbox && hasHeading) return node;
    node = node.parentElement;
  }
  // Detail page (no checkbox on the page) — climb a fixed number of levels
  // instead, or fall back to document.body.
  node = buttonGroupParent;
  for (let i = 0; i < 5 && node; i++) {
    if (node.querySelector("h1, h2, h3")) return node;
    node = node.parentElement;
  }
  return document.body;
}

function extractName(card) {
  const heading = card.querySelector("h1, h2, h3");
  return heading ? heading.textContent.trim() : "";
}

function extractCandidateId(card) {
  const link = card.querySelector('a[href*="id="]');
  if (link && link.href) {
    const match = link.href.match(/[?&]id=([a-zA-Z0-9]+)/);
    if (match) return match[1];
  }
  // Detail page: the candidate id is in the current page URL.
  const urlMatch = window.location.href.match(/[?&]id=([a-zA-Z0-9]+)/);
  if (urlMatch) return urlMatch[1];
  return null;
}

/** Best-effort parse of "Title" followed by "Employer (dateRange)" line
 * pairs, as seen in both the list-row and detail-page work history blocks. */
function extractWorkExperience(card) {
  const text = (card.innerText || card.textContent || "");
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  const entries = [];
  for (let i = 1; i < lines.length; i++) {
    const match = lines[i].match(/^(.*)\(([^)]+)\)\s*$/);
    if (match) {
      entries.push({
        title: lines[i - 1],
        employer: match[1].trim(),
        date_range: match[2].trim(),
      });
    }
  }
  return entries;
}

function extractScreenerAnswers() {
  // Screener questions aren't reliably phrased with a "?" (e.g. "Please
  // provide a valid email address.") so trying to precisely pair each
  // question with its answer is more fragile than it needs to be — the
  // only thing this data is ever used for (client-side and server-side)
  // is regex-matching phone/email out of the text. Just grab the whole
  // "Screener questions" section as one blob and let the regex do the work.
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4"));
  const heading = headings.find((h) => /screener questions/i.test(h.textContent || ""));
  if (!heading) return [];

  // Walk up from the heading until the container is wide enough to include
  // the actual answers below it, not just the heading itself.
  let container = heading.closest("section") || heading.parentElement;
  for (let i = 0; i < 5 && container && container.innerText.trim().length < 200; i++) {
    container = container.parentElement;
  }
  // innerText (not textContent) — textContent concatenates separate block
  // elements with no whitespace, which ran sentences together and made the
  // greedy phone/email regex over-match across boundaries (e.g. "...valid
  // email address.brandy_cragg@yahoo.comPlease provide..." all as one token).
  const text = container ? container.innerText.trim().slice(0, 5000) : "";
  return text ? [{ question: "Screener questions section", answer: text }] : [];
}

function extractRecruitingSummary() {
  const headings = Array.from(document.querySelectorAll("h1, h2, h3, h4"));
  const summaryHeading = headings.find((h) => /recruiting assistant/i.test(h.textContent || ""));
  if (!summaryHeading) return "";
  const container = summaryHeading.closest("section, div");
  return container ? container.innerText.trim().slice(0, 2000) : "";
}

function extractMatchScore(card) {
  const text = card.textContent || "";
  const match = text.match(/\b(\d{1,3})\b\s*(?:match|score)?/i);
  return match ? parseInt(match[1], 10) : null;
}

async function postCandidate(payload) {
  const { apiBase, extensionKey } = await getStoredConfig();
  if (!extensionKey) {
    alert("NDL Hiring Sync: set your Extension Key in the extension's Options page first.");
    return null;
  }
  // Actual fetch happens in the background service worker (background.js),
  // not here, so the request isn't subject to the Indeed page's CORS rules.
  return new Promise((resolve) => {
    chrome.runtime.sendMessage(
      { type: "SYNC_CANDIDATE", apiBase, extensionKey, payload },
      (response) => {
        if (chrome.runtime.lastError) {
          console.error("[NDL Sync] messaging error", chrome.runtime.lastError);
          resolve({ error: chrome.runtime.lastError.message });
          return;
        }
        if (response && response.error) {
          console.error("[NDL Sync] sync failed", response.error);
          resolve({ error: response.error });
          return;
        }
        resolve(response ? response.data : { error: "no response" });
      }
    );
  });
}

function buildPayloadFromCard(card, decision) {
  const candidateId = extractCandidateId(card);
  if (!candidateId) {
    console.warn("[NDL Sync] could not find candidate id for card", card);
    return null;
  }
  const payload = {
    indeed_candidate_id: candidateId,
    decision,
    raw_name: extractName(card),
    work_experience: extractWorkExperience(card),
    screener_answers: [],
  };
  if (isDetailPage()) {
    payload.indeed_profile_url = window.location.href;
    payload.indeed_match_score = extractMatchScore(card);
    payload.recruiting_summary = extractRecruitingSummary();
    payload.screener_answers = extractScreenerAnswers();
  }
  return payload;
}

async function syncAll() {
  const groups = getDecisionGroups();
  if (groups.length === 0) {
    console.warn("[NDL Sync] could not find any triage buttons on this page — selectors need calibration");
    alert("NDL Hiring Sync: couldn't find any candidates on this page.");
    return;
  }

  // Hard requirement: never create an Asana card without contact info.
  // Phone/email only exist in screener answers on the individual candidate
  // page, so a plain list-page sync can never satisfy this — block it
  // outright rather than offering a bypass.
  if (!isDetailPage()) {
    alert(
      "NDL Hiring Sync: candidates can only be synced from their individual " +
      "profile page, not the list view — phone/email (required before a " +
      "card is created) only exist there. Open each candidate and click " +
      "Sync from their profile."
    );
    return;
  }

  let synced = 0, skipped = 0, failed = 0, blocked = 0;
  for (const buttons of groups) {
    const decision = readDecisionFromGroup(buttons);
    if (!decision || decision === "reject") {
      skipped++;
      continue;
    }
    const card = findCandidateCard(buttons[0].parentElement);
    const payload = buildPayloadFromCard(card, decision);
    if (!payload) {
      failed++;
      continue;
    }
    if (!hasContactInfo(payload.screener_answers)) {
      console.warn(`[NDL Sync] blocked — no phone/email found for "${payload.raw_name}"`);
      blocked++;
      continue;
    }
    const result = await postCandidate(payload);
    if (result === null) return; // extension key not configured — postCandidate already alerted
    if (result && !result.error) synced++;
    else failed++;
  }

  alert(
    `NDL Hiring Sync: ${synced} synced, ${skipped} skipped (no decision / rejected), ` +
    `${blocked} blocked (no contact info found), ${failed} failed. Check console for details.`
  );
}

function injectSyncButton() {
  if (document.getElementById("ndl-sync-button")) return;
  const button = document.createElement("button");
  button.id = "ndl-sync-button";
  button.textContent = "Sync to Asana";
  button.addEventListener("click", syncAll);
  document.body.appendChild(button);
}

injectSyncButton();
// Indeed is a single-page app — re-inject if the page navigates without a full reload.
new MutationObserver(() => injectSyncButton()).observe(document.body, { childList: true, subtree: true });
