'use client';
import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

// Phase 1 of Governance/SRD_DRIVER_SCHEDULE_PTT_MODULE.md §12 — manual lead
// override CRUD only. Message feed and device/MDM panels are later phases,
// once DriverMessage / DriverDeviceRegistration exist and have real data.

function resolveApi() {
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://127.0.0.1:8001';
  }
  return '';
}

interface LeadResponse {
  date: string;
  driver_name: string;
  slack_user_id: string | null;
  source: 'manual_override' | 'schedule_ingest' | 'default_rotation';
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

const SOURCE_LABEL: Record<LeadResponse['source'], string> = {
  manual_override: 'Manually set',
  schedule_ingest: 'From schedule ingest',
  default_rotation: 'Fallback rotation (no override set today)',
};

export default function DispatchConsolePage() {
  const [selectedDate, setSelectedDate] = useState(todayISO());
  const [lead, setLead] = useState<LeadResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [draftName, setDraftName] = useState('');
  const [saving, setSaving] = useState(false);

  const load = useCallback(async (dateStr: string) => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${resolveApi()}/driver-lead-schedule/${dateStr}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d: LeadResponse = await res.json();
      setLead(d);
      setDraftName(d.driver_name);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(selectedDate); }, [selectedDate, load]);

  const handleSet = async () => {
    if (!draftName.trim()) return;
    setSaving(true);
    setError('');
    try {
      const res = await fetch(`${resolveApi()}/driver-lead-schedule/${selectedDate}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_name: draftName.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load(selectedDate);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save');
    } finally {
      setSaving(false);
    }
  };

  const handleClear = async () => {
    setSaving(true);
    setError('');
    try {
      const res = await fetch(`${resolveApi()}/driver-lead-schedule/${selectedDate}`, { method: 'DELETE' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await load(selectedDate);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to clear');
    } finally {
      setSaving(false);
    }
  };

  const styles = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    :root {
      --bg: #08111A; --surface: #0D1B26; --surface2: #122030;
      --border: #1C3040; --accent: #F5A123; --ok: #20D9A0; --bad: #F87171;
      --text: #D4E4F0; --muted: #5A7A90;
      --mono: 'Cascadia Code', 'SF Mono', 'Consolas', monospace;
    }
    @media (prefers-color-scheme: light) {
      :root { --bg:#EEF4F8; --surface:#FFFFFF; --surface2:#F0F6FA; --border:#C8DCE8; --text:#1A2E3C; --muted:#6A8FA8; }
    }
    :root[data-theme="dark"]  { --bg:#08111A; --surface:#0D1B26; --surface2:#122030; --border:#1C3040; --text:#D4E4F0; --muted:#5A7A90; }
    :root[data-theme="light"] { --bg:#EEF4F8; --surface:#FFFFFF; --surface2:#F0F6FA; --border:#C8DCE8; --text:#1A2E3C; --muted:#6A8FA8; }

    body { background: var(--bg); color: var(--text); font-family: system-ui, -apple-system, sans-serif; min-height: 100vh; }
    .topbar { padding: 12px 24px; border-bottom: 1px solid var(--border); background: var(--surface); }
    .page-label { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted); font-weight: 600; }
    .page-title { font-size: 18px; font-weight: 700; }

    .main { max-width: 640px; margin: 0 auto; padding: 24px; }
    .card { background: var(--surface); border: 1px solid var(--border); border-radius: 10px; padding: 20px; margin-bottom: 16px; }
    .field-label { font-size: 11px; letter-spacing: 0.08em; text-transform: uppercase; color: var(--muted); margin-bottom: 6px; display: block; }
    input[type="date"], input[type="text"] {
      width: 100%; background: var(--surface2); border: 1px solid var(--border); border-radius: 6px;
      padding: 8px 10px; color: var(--text); font-size: 14px; font-family: var(--mono); margin-bottom: 14px;
    }
    input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }

    .current-lead { display: flex; align-items: baseline; justify-content: space-between; margin-bottom: 4px; }
    .current-lead-name { font-size: 22px; font-weight: 800; }
    .source-tag { font-size: 12px; padding: 3px 10px; border-radius: 20px; font-family: var(--mono); }
    .source-manual_override { background: rgba(32,217,160,0.15); color: var(--ok); }
    .source-schedule_ingest { background: rgba(245,161,35,0.15); color: var(--accent); }
    .source-default_rotation { background: rgba(248,113,113,0.12); color: var(--bad); }
    .slack-status { font-size: 12px; color: var(--muted); font-family: var(--mono); margin-top: 4px; }

    .btn-row { display: flex; gap: 10px; margin-top: 6px; }
    button { border-radius: 6px; padding: 9px 16px; font-size: 13px; font-weight: 600; cursor: pointer; border: 1px solid var(--border); background: var(--surface2); color: var(--text); }
    button:disabled { opacity: 0.5; cursor: not-allowed; }
    .btn-primary { background: var(--accent); color: #08111A; border-color: var(--accent); }
    .btn-danger { color: var(--bad); }

    .error { color: var(--bad); padding: 12px; font-size: 13px; }
    .hint { font-size: 12px; color: var(--muted); margin-top: 10px; }
  `;

  return (
    <ProtectedRoute>
      <style>{styles}</style>
      <div className="topbar">
        <div className="page-label">New Day Logistics</div>
        <div className="page-title">Dispatch Console — Today's Lead</div>
      </div>

      <div className="main">
        <div className="card">
          <label className="field-label" htmlFor="lead-date">Date</label>
          <input
            id="lead-date"
            type="date"
            value={selectedDate}
            onChange={e => setSelectedDate(e.target.value)}
          />

          {loading && <div className="hint">Loading…</div>}
          {error && <div className="error">{error}</div>}

          {!loading && lead && (
            <>
              <div className="current-lead">
                <span className="current-lead-name">{lead.driver_name}</span>
                <span className={`source-tag source-${lead.source}`}>{SOURCE_LABEL[lead.source]}</span>
              </div>
              <div className="slack-status">
                {lead.slack_user_id ? `Slack-linked — reachable via "Talk to My Lead"` : 'Not Slack-linked — driver DM button falls back to Zello for this lead'}
              </div>
            </>
          )}
        </div>

        <div className="card">
          <label className="field-label" htmlFor="lead-name">Set lead for this date</label>
          <input
            id="lead-name"
            type="text"
            placeholder="Driver name (e.g. Spencer Colby)"
            value={draftName}
            onChange={e => setDraftName(e.target.value)}
          />
          <div className="btn-row">
            <button className="btn-primary" onClick={handleSet} disabled={saving || !draftName.trim()}>
              {saving ? 'Saving…' : 'Set Lead'}
            </button>
            <button className="btn-danger" onClick={handleClear} disabled={saving || lead?.source !== 'manual_override'}>
              Clear Override
            </button>
          </div>
          <div className="hint">
            A manual override here always wins over the fallback rotation. Clearing it reverts to whatever the
            system would otherwise resolve for this date.
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
