'use client';
import { useEffect, useState, useCallback, useRef } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';

function resolveApi() {
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://127.0.0.1:8001';
  }
  return '';
}

// ── Types ────────────────────────────────────────────────────────────────────

interface ChecklistItem {
  done: boolean | null;  // null = source not configured (e.g. ADP)
  at: string | null;
}

interface Checklist {
  schedule_acked:  ChecklistItem;
  adp_clocked_in:  ChecklistItem;
  arrived:         ChecklistItem;
  eod_checklist:   ChecklistItem;
  adp_clocked_out: ChecklistItem;
}

interface RtsInfo {
  status: 'not_started' | 'in_progress' | 'completed';
  started_at: string | null;
  completed_at: string | null;
  expected_return_time: string | null;   // "3:45 PM" label, set on submit
  routed_to_rescue: boolean;
  reattempt_assigned_count: number | null;
}

interface Driver {
  driver_name: string;
  route_code: string | null;
  van_number: string | null;
  stage_location: string | null;
  stops: number | null;
  service_type: string | null;
  status: 'arrived' | 'missing' | 'pending';
  arrived_at: string | null;
  checklist: Checklist;
  eta_return: string | null;       // "3:45 PM" | "Done" | null
  eta_return_at: string | null;    // naive-UTC ISO instant, for the live countdown
  pct_complete: number | null;     // 0–100 from latest Cortex snapshot
  packages_remaining: number | null;
  rts: RtsInfo;
}

interface Wave {
  wave_time: string;
  wave_past: boolean;
  minutes_to_wave: number | null;
  total: number;
  arrived: number;
  missing: number;
  pending: number;
  drivers: Driver[];
}

interface WaveStatus {
  date: string;
  wave_lead: string;
  as_of: string;
  summary: { total: number; arrived: number; missing: number; pending: number };
  waves: Wave[];
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt12(isoOrNull: string | null): string {
  if (!isoOrNull) return '—';
  try {
    return new Date(isoOrNull + 'Z').toLocaleTimeString('en-US', {
      hour: 'numeric', minute: '2-digit', hour12: true,
    });
  } catch { return isoOrNull; }
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

function clDotClass(done: boolean | null): string {
  if (done === null) return 'cl-dot cl-na';
  return done ? 'cl-dot cl-done' : 'cl-dot cl-undone';
}

function waveCountdown(mins: number | null): string {
  if (mins === null) return '';
  if (mins < 0) return `${Math.abs(Math.round(mins))}m ago`;
  if (mins < 1) return 'NOW';
  return `in ${Math.round(mins)}m`;
}

function fmtCountdown(seconds: number): string {
  const overdue = seconds < 0;
  const abs = Math.abs(Math.round(seconds));
  const h = Math.floor(abs / 3600);
  const m = Math.floor((abs % 3600) / 60);
  const s = abs % 60;
  const body = h > 0
    ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
    : `${m}:${String(s).padStart(2, '0')}`;
  return overdue ? `${body} over` : body;
}

// Ticks once a second on its own — independent of the 30s data poll — so the
// countdown doesn't visibly stall between refreshes.
function Countdown({ targetIso }: { targetIso: string }) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);
  const target = new Date(targetIso + 'Z').getTime();
  const secondsLeft = (target - now) / 1000;
  const overdue = secondsLeft < 0;
  return (
    <span className={`countdown${overdue ? ' overdue' : ''}`}>
      {fmtCountdown(secondsLeft)}
    </span>
  );
}

// ── Status config ─────────────────────────────────────────────────────────────

const STATUS = {
  arrived: { label: 'Arrived', stripe: '#20D9A0', badge: 'rgba(32,217,160,0.15)', text: '#20D9A0' },
  missing: { label: 'Missing', stripe: '#F87171', badge: 'rgba(248,113,113,0.15)', text: '#F87171' },
  pending: { label: 'Pending', stripe: '#4A6880', badge: 'rgba(74,104,128,0.15)', text: '#7A9DB8' },
} as const;

const RTS_STATUS = {
  not_started: { label: 'Not Started', text: '#5A7A90', badge: 'rgba(90,122,144,0.12)' },
  in_progress: { label: 'RTS In Progress', text: '#F5A123', badge: 'rgba(245,161,35,0.12)' },
  completed:   { label: 'RTS Complete',    text: '#20D9A0', badge: 'rgba(32,217,160,0.12)' },
} as const;

// ── Component ────────────────────────────────────────────────────────────────

export default function WaveStatusPage() {
  const { user } = useAuth();
  const [data, setData] = useState<WaveStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedDate, setSelectedDate] = useState(todayISO());
  const [countdown, setCountdown] = useState(30);
  const [activeWave, setActiveWave] = useState<string | null>(null);
  const [sendingRts, setSendingRts] = useState<Record<string, 'sending' | 'sent' | 'failed'>>({});
  const intervalRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (date: string) => {
    try {
      const res = await fetch(`${resolveApi()}/rostering/wave-status?shift_date=${date}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d: WaveStatus = await res.json();
      setData(d);
      setError('');
      // Auto-select first non-empty wave
      if (d.waves.length > 0 && !activeWave) {
        setActiveWave(d.waves[0].wave_time);
      }
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, [activeWave]);

  // Initial load + auto-refresh
  useEffect(() => {
    load(selectedDate);
    setCountdown(30);
    if (intervalRef.current) clearInterval(intervalRef.current);
    intervalRef.current = setInterval(() => {
      setCountdown(c => {
        if (c <= 1) { load(selectedDate); return 30; }
        return c - 1;
      });
    }, 1000);
    return () => { if (intervalRef.current) clearInterval(intervalRef.current); };
  }, [selectedDate, load]);

  const sendRts = async (driverName: string) => {
    setSendingRts(prev => ({ ...prev, [driverName]: 'sending' }));
    try {
      const res = await fetch(`${resolveApi()}/rts/generate`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_name: driverName, generated_by: user?.username ?? 'dispatch' }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setSendingRts(prev => ({ ...prev, [driverName]: 'sent' }));
      load(selectedDate);
    } catch {
      setSendingRts(prev => ({ ...prev, [driverName]: 'failed' }));
    }
  };

  const s = data?.summary;
  const arrivedPct = s && s.total > 0 ? Math.round((s.arrived / s.total) * 100) : 0;

  const styles = `
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

    :root {
      --bg:       #08111A;
      --surface:  #0D1B26;
      --surface2: #122030;
      --border:   #1C3040;
      --accent:   #F5A123;
      --arrived:  #20D9A0;
      --missing:  #F87171;
      --pending:  #4A6880;
      --text:     #D4E4F0;
      --muted:    #5A7A90;
      --mono:     'Cascadia Code', 'SF Mono', 'Consolas', monospace;
    }
    @media (prefers-color-scheme: light) {
      :root {
        --bg:       #EEF4F8;
        --surface:  #FFFFFF;
        --surface2: #F0F6FA;
        --border:   #C8DCE8;
        --text:     #1A2E3C;
        --muted:    #6A8FA8;
      }
    }
    :root[data-theme="dark"] {
      --bg:#08111A; --surface:#0D1B26; --surface2:#122030;
      --border:#1C3040; --text:#D4E4F0; --muted:#5A7A90;
    }
    :root[data-theme="light"] {
      --bg:#EEF4F8; --surface:#FFFFFF; --surface2:#F0F6FA;
      --border:#C8DCE8; --text:#1A2E3C; --muted:#6A8FA8;
    }

    body { background: var(--bg); color: var(--text);
           font-family: system-ui, -apple-system, sans-serif; min-height: 100vh; }

    .topbar {
      position: sticky; top: 0; z-index: 50;
      background: var(--surface); border-bottom: 1px solid var(--border);
      padding: 12px 24px; display: flex; align-items: center;
      justify-content: space-between; gap: 16px; flex-wrap: wrap;
    }
    .topbar-left { display: flex; align-items: center; gap: 16px; }
    .page-label { font-size: 11px; letter-spacing: 0.12em; text-transform: uppercase;
                  color: var(--muted); font-weight: 600; }
    .page-title { font-size: 18px; font-weight: 700; color: var(--text); }
    .date-input {
      background: var(--surface2); border: 1px solid var(--border);
      border-radius: 6px; padding: 6px 10px; color: var(--text);
      font-size: 13px; font-family: var(--mono); cursor: pointer;
    }
    .date-input:focus { outline: 2px solid var(--accent); outline-offset: 1px; }
    .refresh-badge {
      display: flex; align-items: center; gap: 6px;
      font-size: 12px; color: var(--muted); font-family: var(--mono);
    }
    .refresh-dot { width: 7px; height: 7px; border-radius: 50%;
                   background: var(--arrived); animation: pulse 2s infinite; }
    @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.3; } }

    .stats-row {
      display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
      padding: 20px 24px;
    }
    .stat-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 10px; padding: 16px 20px;
    }
    .stat-label { font-size: 11px; letter-spacing: 0.1em; text-transform: uppercase;
                  color: var(--muted); margin-bottom: 6px; }
    .stat-value { font-size: 32px; font-weight: 800; font-family: var(--mono);
                  font-variant-numeric: tabular-nums; line-height: 1; }
    .stat-sub { font-size: 12px; color: var(--muted); margin-top: 4px;
                font-family: var(--mono); }

    .progress-bar {
      margin: 0 24px 4px; height: 4px; background: var(--surface2);
      border-radius: 2px; overflow: hidden;
    }
    .progress-fill {
      height: 100%; border-radius: 2px; background: var(--arrived);
      transition: width 0.6s ease;
    }
    .progress-label {
      margin: 0 24px 20px; font-size: 11px; color: var(--muted);
      font-family: var(--mono);
    }

    .main { padding: 0 24px 48px; }

    .wave-tabs {
      display: flex; gap: 8px; margin-bottom: 20px;
      overflow-x: auto; padding-bottom: 4px;
    }
    .wave-tab {
      flex-shrink: 0; padding: 8px 16px; border-radius: 8px; cursor: pointer;
      border: 1px solid var(--border); background: var(--surface);
      font-size: 13px; font-family: var(--mono); color: var(--muted);
      transition: all 0.15s; white-space: nowrap;
    }
    .wave-tab:hover { border-color: var(--accent); color: var(--text); }
    .wave-tab.active {
      background: var(--accent); color: #08111A; border-color: var(--accent);
      font-weight: 700;
    }
    .wave-tab-count {
      display: inline-block; margin-left: 6px; padding: 1px 6px;
      border-radius: 10px; font-size: 11px;
    }
    .tab-missing { background: rgba(248,113,113,0.25); color: #F87171; }
    .tab-ok { background: rgba(32,217,160,0.15); color: #20D9A0; }

    .wave-section { animation: fadein 0.2s ease; }
    @keyframes fadein { from { opacity: 0; transform: translateY(4px); } to { opacity: 1; transform: none; } }

    .wave-header {
      display: flex; align-items: center; justify-content: space-between;
      margin-bottom: 16px; flex-wrap: wrap; gap: 12px;
    }
    .wave-time-block { display: flex; align-items: baseline; gap: 10px; }
    .wave-time { font-size: 28px; font-weight: 800; font-family: var(--mono);
                 color: var(--text); }
    .wave-countdown { font-size: 13px; font-family: var(--mono);
                      color: var(--accent); font-weight: 600; }
    .wave-lead-tag { font-size: 12px; color: var(--muted);
                     background: var(--surface2); border: 1px solid var(--border);
                     border-radius: 6px; padding: 4px 10px; }
    .wave-chips { display: flex; gap: 8px; flex-wrap: wrap; }
    .wave-chip { padding: 4px 12px; border-radius: 20px; font-size: 12px;
                 font-family: var(--mono); font-weight: 600; }
    .chip-arrived { background: rgba(32,217,160,0.12); color: #20D9A0; }
    .chip-missing { background: rgba(248,113,113,0.12); color: #F87171; }
    .chip-pending { background: rgba(74,104,128,0.12); color: #7A9DB8; }

    .driver-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
      gap: 10px;
    }
    .driver-card {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 14px 14px 14px 17px;
      display: flex; gap: 12px; align-items: stretch;
      border-left: 3px solid var(--border); transition: border-color 0.15s;
    }
    .driver-card.arrived { border-left-color: #20D9A0; }
    .driver-card.missing { border-left-color: #F87171; }
    .driver-card.pending { border-left-color: #4A6880; }
    .driver-card.missing { background: rgba(248,113,113,0.04); }

    .driver-info { flex: 1; min-width: 0; }
    .driver-name { font-size: 14px; font-weight: 600; color: var(--text);
                   white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
                   margin-bottom: 6px; }
    .driver-meta { display: flex; flex-wrap: wrap; gap: 6px; }
    .meta-chip {
      font-size: 11px; font-family: var(--mono); padding: 2px 7px;
      border-radius: 4px; background: var(--surface2); color: var(--muted);
      border: 1px solid var(--border); white-space: nowrap;
    }
    .meta-chip.route { color: var(--accent); border-color: rgba(245,161,35,0.3);
                       background: rgba(245,161,35,0.08); font-weight: 600; }
    .meta-chip.adp-in  { color: #20D9A0; border-color: rgba(32,217,160,0.3);
                          background: rgba(32,217,160,0.08); font-weight: 600; }
    .meta-chip.adp-out { color: #F87171; border-color: rgba(248,113,113,0.3);
                          background: rgba(248,113,113,0.08); font-weight: 600; }
    .meta-chip.adp-unknown { color: var(--muted); }

    .eta-row {
      display: flex; align-items: center; gap: 8px; margin-top: 7px;
    }
    .eta-label { font-size: 11px; color: var(--muted); font-family: var(--mono); }
    .eta-value { font-size: 12px; font-weight: 700; font-family: var(--mono);
                 color: var(--accent); }
    .eta-value.done { color: var(--arrived); }
    .pkg-bar {
      flex: 1; height: 3px; background: var(--surface2);
      border-radius: 2px; overflow: hidden;
    }
    .pkg-bar-fill {
      height: 100%; border-radius: 2px; background: var(--accent);
      transition: width 0.6s ease;
    }
    .pkg-bar-fill.high { background: var(--arrived); }

    .countdown {
      font-size: 12px; font-weight: 700; font-family: var(--mono);
      font-variant-numeric: tabular-nums; color: var(--accent);
    }
    .countdown.overdue { color: #F87171; }

    .rts-row { margin-top: 7px; }
    .rts-chip {
      display: inline-flex; align-items: center; gap: 6px;
      font-size: 11px; font-family: var(--mono); font-weight: 600;
      padding: 3px 9px; border-radius: 20px;
    }
    .rts-chip .rts-dot { width: 6px; height: 6px; border-radius: 50%; }
    .rts-send-btn {
      font-size: 11px; font-family: var(--mono); font-weight: 600;
      padding: 4px 10px; border-radius: 20px; cursor: pointer;
      background: rgba(245,161,35,0.12); color: var(--accent);
      border: 1px solid rgba(245,161,35,0.35);
    }
    .rts-send-btn:hover:not(:disabled) { background: rgba(245,161,35,0.22); }
    .rts-send-btn:disabled { cursor: default; opacity: 0.6; }

    .checklist-row {
      display: flex; gap: 6px; flex-wrap: wrap; margin-top: 8px;
      padding-top: 8px; border-top: 1px solid var(--border);
    }
    .cl-item {
      display: flex; align-items: center; gap: 3px;
      font-size: 10px; font-family: var(--mono);
      color: var(--muted); white-space: nowrap;
    }
    .cl-dot {
      width: 8px; height: 8px; border-radius: 50%; flex-shrink: 0;
    }
    .cl-done   { background: #20D9A0; }
    .cl-undone { background: var(--border); }
    .cl-na     { background: transparent; border: 1px solid var(--border); }
    .cl-label-done { color: var(--text-dim); }
    .driver-status-col { display: flex; flex-direction: column;
                          align-items: flex-end; justify-content: space-between;
                          min-width: 70px; text-align: right; }
    .status-label { font-size: 11px; font-weight: 700; letter-spacing: 0.05em;
                    text-transform: uppercase; }
    .status-arrived { color: #20D9A0; }
    .status-missing { color: #F87171; }
    .status-pending  { color: #7A9DB8; }
    .arrived-time { font-size: 11px; color: var(--muted);
                    font-family: var(--mono); margin-top: 4px; }

    .empty { padding: 40px; text-align: center; color: var(--muted);
              font-size: 14px; background: var(--surface);
              border: 1px solid var(--border); border-radius: 10px; }
    .error { color: #F87171; padding: 20px; text-align: center; }

    .wave-lead-bar {
      background: var(--surface); border: 1px solid var(--border);
      border-radius: 8px; padding: 10px 16px; margin-bottom: 20px;
      display: flex; align-items: center; gap: 10px; font-size: 13px;
    }
    .wave-lead-dot { width: 8px; height: 8px; border-radius: 50%;
                     background: var(--accent); flex-shrink: 0; }

    @media (max-width: 640px) {
      .stats-row { grid-template-columns: repeat(2, 1fr); }
      .topbar { padding: 10px 16px; }
      .main { padding: 0 16px 40px; }
      .stats-row { padding: 16px; }
      .progress-bar, .progress-label { margin-left: 16px; margin-right: 16px; }
      .wave-time { font-size: 22px; }
    }
  `;

  return (
    <ProtectedRoute>
      <style>{styles}</style>

      {/* Top bar */}
      <div className="topbar">
        <div className="topbar-left">
          <div>
            <div className="page-label">New Day Logistics</div>
            <div className="page-title">Wave Status</div>
          </div>
          <input
            type="date"
            className="date-input"
            value={selectedDate}
            onChange={e => { setSelectedDate(e.target.value); setLoading(true); setData(null); setActiveWave(null); }}
          />
        </div>
        <div className="refresh-badge">
          <div className="refresh-dot" />
          Auto-refresh in {countdown}s
        </div>
      </div>

      {/* Summary stats */}
      {data && (
        <>
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-label">Total Scheduled</div>
              <div className="stat-value" style={{ color: 'var(--text)' }}>{s!.total}</div>
              <div className="stat-sub">{data.wave_lead}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Arrived</div>
              <div className="stat-value" style={{ color: '#20D9A0' }}>{s!.arrived}</div>
              <div className="stat-sub">{arrivedPct}% on-site</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Missing</div>
              <div className="stat-value" style={{ color: s!.missing > 0 ? '#F87171' : 'var(--muted)' }}>
                {s!.missing}
              </div>
              <div className="stat-sub">{s!.missing > 0 ? 'Past wave time' : 'None'}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Pending</div>
              <div className="stat-value" style={{ color: '#7A9DB8' }}>{s!.pending}</div>
              <div className="stat-sub">Pre-wave</div>
            </div>
          </div>

          <div className="progress-bar">
            <div className="progress-fill" style={{ width: `${arrivedPct}%` }} />
          </div>
          <div className="progress-label">{arrivedPct}% confirmed · as of {fmt12(data.as_of.replace('T', ' ').slice(0, 19))}</div>
        </>
      )}

      <div className="main">
        {loading && !data && <div className="empty">Loading…</div>}
        {error && <div className="error">{error}</div>}

        {data && data.waves.length === 0 && (
          <div className="empty">No assignments found for {selectedDate}.<br />Upload a Cortex file to populate assignments.</div>
        )}

        {data && data.waves.length > 0 && (
          <>
            {/* Wave tabs */}
            <div className="wave-tabs">
              {data.waves.map(w => (
                <button
                  key={w.wave_time}
                  className={`wave-tab${activeWave === w.wave_time ? ' active' : ''}`}
                  onClick={() => setActiveWave(w.wave_time)}
                >
                  {w.wave_time}
                  {w.missing > 0 && (
                    <span className="wave-tab-count tab-missing">{w.missing} missing</span>
                  )}
                  {w.missing === 0 && w.wave_past && (
                    <span className="wave-tab-count tab-ok">✓</span>
                  )}
                </button>
              ))}
            </div>

            {/* Selected wave detail */}
            {data.waves.filter(w => w.wave_time === activeWave).map(wave => (
              <div key={wave.wave_time} className="wave-section">

                <div className="wave-header">
                  <div className="wave-time-block">
                    <div className="wave-time">{wave.wave_time}</div>
                    {wave.minutes_to_wave !== null && (
                      <div className="wave-countdown">{waveCountdown(wave.minutes_to_wave)}</div>
                    )}
                  </div>
                  <div className="wave-chips">
                    <span className="wave-chip chip-arrived">✅ {wave.arrived} arrived</span>
                    {wave.missing > 0 && <span className="wave-chip chip-missing">🚨 {wave.missing} missing</span>}
                    {wave.pending > 0 && <span className="wave-chip chip-pending">⏳ {wave.pending} pending</span>}
                  </div>
                </div>

                {/* Missing drivers call-out */}
                {wave.missing > 0 && (
                  <div style={{
                    background: 'rgba(248,113,113,0.06)', border: '1px solid rgba(248,113,113,0.25)',
                    borderRadius: 8, padding: '12px 16px', marginBottom: 16,
                    fontSize: 13, color: '#F87171',
                  }}>
                    <strong>🚨 Missing after wave time:</strong>{' '}
                    {wave.drivers.filter(d => d.status === 'missing').map(d => d.driver_name).join(' · ')}
                  </div>
                )}

                <div className="driver-grid">
                  {wave.drivers.map(driver => {
                    const cfg = STATUS[driver.status];
                    return (
                      <div key={driver.driver_name} className={`driver-card ${driver.status}`}>
                        <div className="driver-info">
                          <div className="driver-name">{driver.driver_name}</div>
                          <div className="driver-meta">
                            {driver.route_code && <span className="meta-chip route">{driver.route_code}</span>}
                            {driver.van_number && <span className="meta-chip">{driver.van_number}</span>}
                            {driver.stage_location && <span className="meta-chip">{driver.stage_location}</span>}
                            {driver.stops != null && <span className="meta-chip">{driver.stops} stops</span>}
                          </div>
                          {(driver.eta_return || driver.pct_complete != null) && (
                            <div className="eta-row">
                              {driver.pct_complete != null && (
                                <div className="pkg-bar" title={`${Math.round(driver.pct_complete)}% delivered`}>
                                  <div
                                    className={`pkg-bar-fill${driver.pct_complete >= 95 ? ' high' : ''}`}
                                    style={{ width: `${Math.min(100, driver.pct_complete)}%` }}
                                  />
                                </div>
                              )}
                              {driver.eta_return && (
                                <>
                                  <span className="eta-label">ETA</span>
                                  <span className={`eta-value${driver.eta_return === 'Done' ? ' done' : ''}`}>
                                    {driver.eta_return}
                                  </span>
                                  {driver.eta_return_at && driver.eta_return !== 'Done' && (
                                    <Countdown targetIso={driver.eta_return_at} />
                                  )}
                                  {driver.packages_remaining != null && driver.packages_remaining > 0 && (
                                    <span className="eta-label">· {driver.packages_remaining} left</span>
                                  )}
                                </>
                              )}
                            </div>
                          )}
                          {driver.rts && driver.rts.status !== 'not_started' && (
                            <div className="rts-row">
                              <span
                                className="rts-chip"
                                style={{
                                  color: RTS_STATUS[driver.rts.status].text,
                                  background: RTS_STATUS[driver.rts.status].badge,
                                }}
                                title={
                                  driver.rts.completed_at ? `Completed ${fmt12(driver.rts.completed_at)}`
                                  : driver.rts.started_at ? `Started ${fmt12(driver.rts.started_at)}`
                                  : undefined
                                }
                              >
                                <span className="rts-dot" style={{ background: RTS_STATUS[driver.rts.status].text }} />
                                {driver.rts.routed_to_rescue ? 'Routed to Rescue' : RTS_STATUS[driver.rts.status].label}
                                {driver.rts.expected_return_time && ` · ETA ${driver.rts.expected_return_time}`}
                              </span>
                            </div>
                          )}
                          {driver.rts && driver.rts.status === 'not_started' && (
                            <div className="rts-row">
                              <button
                                className="rts-send-btn"
                                disabled={sendingRts[driver.driver_name] === 'sending'}
                                onClick={() => sendRts(driver.driver_name)}
                              >
                                {sendingRts[driver.driver_name] === 'sending' ? 'Sending…'
                                  : sendingRts[driver.driver_name] === 'failed' ? 'Failed — retry'
                                  : '🔄 Send RTS Check'}
                              </button>
                            </div>
                          )}
                          {driver.checklist && (
                            <div className="checklist-row">
                              {([
                                ['schedule_acked',  'Sched'],
                                ['adp_clocked_in',  'ADP In'],
                                ['arrived',         'Arrived'],
                                ['eod_checklist',   'EOD'],
                                ['adp_clocked_out', 'Clk Out'],
                              ] as [keyof Checklist, string][]).map(([key, label]) => {
                                const item = driver.checklist[key];
                                return (
                                  <span key={key} className="cl-item" title={item.at ? fmt12(item.at) : label}>
                                    <span className={clDotClass(item.done)} />
                                    <span className={item.done ? 'cl-label-done' : ''}>{label}</span>
                                  </span>
                                );
                              })}
                            </div>
                          )}
                        </div>
                        <div className="driver-status-col">
                          <span className={`status-label status-${driver.status}`}>{cfg.label}</span>
                          {driver.arrived_at && (
                            <span className="arrived-time">{fmt12(driver.arrived_at)}</span>
                          )}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>
            ))}
          </>
        )}
      </div>
    </ProtectedRoute>
  );
}
