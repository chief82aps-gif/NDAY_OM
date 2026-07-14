/**
 * NDL Hiring Sync — content script.
 *
 * IMPORTANT: the selectors in the SELECTORS block below are a first-pass
 * best guess based on screenshots of the Indeed employer dashboard, not
 * live DOM inspection (this was built without direct access to Indeed's
 * page). They will very likely need a calibration pass against the real
 * page — open DevTools Console on the Indeed page after installing this,
 * click Sync, and report any "[NDL Sync] could not find..." warnings back
 * so the selectors can be corrected quickly.
 */

const SELECTORS = {
  // A single candidate row on the list/board view.
  listRow: '[data-testid*="candidate-card"], [data-testid*="candidate-row"], li[class*="candidate"]',
  // The three triage buttons (✓ / ? / ✗) — matched by aria-label keywords, not exact class names.
  decisionButtons: 'button[aria-label], button[aria-pressed]',
  candidateNameInRow: 'a, [data-testid*="name"], h2, h3',
  candidateLink: 'a[href]',
  // Detail page markers.
  screenerSectionHeading: 'h2, h3, [role="heading"]',
  screenerQAPair: '[data-testid*="screener"], section',
  workExperienceItem: '[data-testid*="experience"] li, [data-testid*="work-history"] li',
  recruitingSummary: '[data-testid*="recruiting-assistant"], [data-testid*="summary"]',
  matchScore: '[data-testid*="score"]',
};

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
  const headings = Array.from(document.querySelectorAll(SELECTORS.screenerSectionHeading));
  return headings.some((h) => /screener questions/i.test(h.textContent || ""));
}

function isListPage() {
  return document.querySelectorAll(SELECTORS.listRow).length > 1;
}

function readDecision(container) {
  const buttons = Array.from(container.querySelectorAll(SELECTORS.decisionButtons));
  for (const btn of buttons) {
    const pressed = btn.getAttribute("aria-pressed") === "true" || btn.classList.contains("selected");
    if (!pressed) continue;
    const label = (btn.getAttribute("aria-label") || btn.textContent || "").toLowerCase();
    if (/reject|not a fit|no\b/.test(label)) return "reject";
    if (/undecided|maybe|unsure/.test(label)) return "undecided";
    if (/shortlist|accept|advance|yes\b/.test(label)) return "accept";
  }
  return null;
}

function extractCandidateId(container) {
  const withAttr = container.querySelector("[data-candidate-id]");
  if (withAttr) return withAttr.getAttribute("data-candidate-id");

  const link = container.querySelector(SELECTORS.candidateLink);
  if (link && link.href) {
    const match = link.href.match(/candidates?\/([a-zA-Z0-9_-]+)/);
    if (match) return match[1];
  }
  return null;
}

function extractName(container) {
  const el = container.querySelector(SELECTORS.candidateNameInRow);
  return el ? el.textContent.trim() : "";
}

function extractWorkExperience(container) {
  const items = Array.from(container.querySelectorAll(SELECTORS.workExperienceItem));
  return items
    .map((item) => {
      const text = item.textContent.trim();
      // Best-effort split of "Title - Employer (dateRange)" style text blocks.
      const dateMatch = text.match(/\(([^)]+)\)\s*$/);
      const date_range = dateMatch ? dateMatch[1] : "";
      return { title: "", employer: text.replace(/\([^)]+\)\s*$/, "").trim(), date_range };
    })
    .filter((w) => w.employer);
}

function extractScreenerAnswers() {
  // Screener Q&A on Indeed's detail page typically alternates a question
  // label and an answer value in adjacent block elements. This grabs every
  // heading-like element followed by the next text block as a Q/A pair.
  const answers = [];
  const candidates = Array.from(document.querySelectorAll("div, section"));
  for (const node of candidates) {
    const text = node.textContent || "";
    if (text.length > 300) continue; // skip huge wrapper containers
    const qMatch = text.match(/^(.*\?)\s*(.*)$/s);
    if (qMatch && qMatch[1] && qMatch[2]) {
      answers.push({ question: qMatch[1].trim(), answer: qMatch[2].trim() });
    }
  }
  return answers;
}

function extractRecruitingSummary() {
  const el = document.querySelector(SELECTORS.recruitingSummary);
  return el ? el.textContent.trim() : "";
}

function extractMatchScore() {
  const el = document.querySelector(SELECTORS.matchScore);
  if (!el) return null;
  const match = (el.textContent || "").match(/\d+/);
  return match ? parseInt(match[0], 10) : null;
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

async function syncListPage() {
  const rows = Array.from(document.querySelectorAll(SELECTORS.listRow));
  if (rows.length === 0) {
    console.warn("[NDL Sync] could not find any candidate rows — selectors need calibration");
    return;
  }
  let synced = 0, skipped = 0, failed = 0;
  for (const row of rows) {
    const decision = readDecision(row);
    if (!decision || decision === "reject") {
      skipped++;
      continue;
    }
    const candidateId = extractCandidateId(row);
    if (!candidateId) {
      console.warn("[NDL Sync] could not find candidate id for row", row);
      failed++;
      continue;
    }
    const payload = {
      indeed_candidate_id: candidateId,
      decision,
      raw_name: extractName(row),
      work_experience: extractWorkExperience(row),
      screener_answers: [],
    };
    const result = await postCandidate(payload);
    if (result && !result.error) synced++; else failed++;
  }
  alert(`NDL Hiring Sync: ${synced} synced, ${skipped} skipped (undecided/reject not touched or rejected), ${failed} failed. Check console for details.`);
}

async function syncDetailPage() {
  const decision = readDecision(document.body);
  if (!decision) {
    alert("NDL Hiring Sync: couldn't detect which of ✓ / ? / ✗ is currently selected on this page.");
    return;
  }
  const candidateId = extractCandidateId(document.body) || window.location.pathname;
  const payload = {
    indeed_candidate_id: candidateId,
    decision,
    raw_name: extractName(document.body) || document.title,
    indeed_profile_url: window.location.href,
    indeed_match_score: extractMatchScore(),
    recruiting_summary: extractRecruitingSummary(),
    work_experience: extractWorkExperience(document.body),
    screener_answers: extractScreenerAnswers(),
  };
  const result = await postCandidate(payload);
  if (result && !result.error) {
    alert("NDL Hiring Sync: candidate synced.");
  } else {
    alert("NDL Hiring Sync: sync failed, check console.");
  }
}

function injectSyncButton() {
  if (document.getElementById("ndl-sync-button")) return;
  const button = document.createElement("button");
  button.id = "ndl-sync-button";
  button.textContent = isDetailPage() ? "Sync candidate to Asana" : "Sync to Asana";
  button.addEventListener("click", () => {
    if (isDetailPage()) syncDetailPage();
    else syncListPage();
  });
  document.body.appendChild(button);
}

injectSyncButton();
// Indeed is a single-page app — re-inject if the page navigates without a full reload.
new MutationObserver(() => injectSyncButton()).observe(document.body, { childList: true, subtree: true });
