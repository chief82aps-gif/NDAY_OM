'use client';

import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') return '';
  return 'http://127.0.0.1:8001';
}

interface PunchStatus {
  clocked_in: boolean;
  clocked_out: boolean;
  in_at: string | null;
  out_at: string | null;
}

interface AdpStatus {
  configured: boolean;
  message?: string;
  required_env_vars?: string[];
  optional_env_vars?: string[];
  clocked_in_count?: number | null;
  clocked_out_count?: number | null;
  clocked_in_names?: string[] | null;
  clocked_out_names?: string[] | null;
  worker_count?: number;
  cache_age_seconds?: number;
  status?: string;
}

interface SetupStep {
  step: number;
  title: string;
  detail: string;
}

interface SetupGuide {
  title: string;
  steps: SetupStep[];
  env_vars: Record<string, string>;
}

function fmtTime(iso: string | null): string {
  if (!iso) return '—';
  try {
    return new Date(iso).toLocaleTimeString('en-US', {
      hour: 'numeric',
      minute: '2-digit',
      hour12: true,
      timeZone: 'America/Los_Angeles',
    });
  } catch {
    return iso;
  }
}

function AdpPage() {
  const [status, setStatus] = useState<AdpStatus | null>(null);
  const [guide, setGuide] = useState<SetupGuide | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [toast, setToast] = useState<{ type: 'ok' | 'err'; msg: string } | null>(null);
  const [showGuide, setShowGuide] = useState(false);

  const base = resolveApi();

  const showToast = (type: 'ok' | 'err', msg: string) => {
    setToast({ type, msg });
    setTimeout(() => setToast(null), 4000);
  };

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${base}/adp/status`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: AdpStatus = await res.json();
      setStatus(data);
      if (!data.configured && !guide) {
        const gr = await fetch(`${base}/adp/setup-guide`);
        if (gr.ok) setGuide(await gr.json());
      }
    } catch (e) {
      showToast('err', `Failed to load ADP status: ${e}`);
    } finally {
      setLoading(false);
    }
  }, [base, guide]);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 30_000);
    return () => clearInterval(id);
  }, [fetchStatus]);

  const handleRefresh = async () => {
    setRefreshing(true);
    try {
      const res = await fetch(`${base}/adp/refresh`, { method: 'POST' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      showToast('ok', 'Cache cleared — re-fetching from ADP…');
      setTimeout(fetchStatus, 800);
    } catch (e) {
      showToast('err', `Refresh failed: ${e}`);
    } finally {
      setRefreshing(false);
    }
  };

  // Build merged worker list: union of clocked_in and clocked_out names
  const buildWorkerList = (): Array<{ name: string } & PunchStatus> => {
    if (!status?.configured) return [];
    const map = new Map<string, PunchStatus>();
    for (const n of status.clocked_in_names ?? []) {
      map.set(n, { clocked_in: true, clocked_out: false, in_at: null, out_at: null });
    }
    for (const n of status.clocked_out_names ?? []) {
      if (map.has(n)) {
        map.get(n)!.clocked_out = true;
        map.get(n)!.clocked_in = false;
      } else {
        map.set(n, { clocked_in: false, clocked_out: true, in_at: null, out_at: null });
      }
    }
    return Array.from(map.entries())
      .map(([name, ps]) => ({ name, ...ps }))
      .sort((a, b) => a.name.localeCompare(b.name));
  };

  const workers = buildWorkerList();

  return (
    <>
      <style>{`
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body { background: #0f1117; color: #e2e8f0; font-family: system-ui, sans-serif; }

        .page { max-width: 900px; margin: 0 auto; padding: 24px 16px 60px; }

        .page-header { display: flex; align-items: center; justify-content: space-between;
          flex-wrap: wrap; gap: 12px; margin-bottom: 24px; }
        .page-title { font-size: 1.5rem; font-weight: 700; color: #f8fafc; }
        .page-sub { font-size: 0.8rem; color: #64748b; margin-top: 2px; }

        .btn { display: inline-flex; align-items: center; gap: 6px;
          padding: 8px 16px; border-radius: 8px; border: none;
          font-size: 0.85rem; font-weight: 600; cursor: pointer;
          transition: opacity .15s, transform .1s; }
        .btn:disabled { opacity: .45; cursor: not-allowed; }
        .btn:not(:disabled):active { transform: scale(.97); }
        .btn-primary { background: #3b82f6; color: #fff; }
        .btn-primary:not(:disabled):hover { opacity: .85; }
        .btn-ghost { background: #1e293b; color: #94a3b8; border: 1px solid #334155; }
        .btn-ghost:not(:disabled):hover { background: #293548; }

        /* Status banner */
        .banner { display: flex; align-items: center; gap: 12px;
          padding: 14px 18px; border-radius: 10px; margin-bottom: 20px;
          border: 1px solid transparent; font-size: 0.9rem; }
        .banner-ok { background: #052e16; border-color: #166534; color: #86efac; }
        .banner-warn { background: #1c1007; border-color: #92400e; color: #fcd34d; }
        .banner-err { background: #1a0708; border-color: #991b1b; color: #fca5a5; }
        .banner-dot { width: 10px; height: 10px; border-radius: 50%; flex-shrink: 0; }
        .dot-ok { background: #4ade80; }
        .dot-warn { background: #fbbf24; animation: pulse 2s infinite; }
        .dot-err { background: #f87171; }
        @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:.4} }

        /* Stat cards */
        .stats-row { display: grid; grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
          gap: 12px; margin-bottom: 20px; }
        .stat-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
          padding: 14px 16px; }
        .stat-label { font-size: 0.72rem; color: #64748b; text-transform: uppercase;
          letter-spacing: .05em; margin-bottom: 6px; }
        .stat-value { font-size: 1.7rem; font-weight: 700; font-variant-numeric: tabular-nums; }
        .stat-value.green { color: #4ade80; }
        .stat-value.amber { color: #fbbf24; }
        .stat-value.blue  { color: #60a5fa; }
        .stat-value.slate { color: #94a3b8; }

        /* Worker table */
        .section { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
          overflow: hidden; margin-bottom: 20px; }
        .section-head { display: flex; align-items: center; justify-content: space-between;
          padding: 14px 18px; border-bottom: 1px solid #334155; }
        .section-title { font-size: 0.9rem; font-weight: 600; color: #cbd5e1; }
        .section-badge { font-size: 0.75rem; color: #64748b; }

        .table-wrap { overflow-x: auto; }
        table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
        th { padding: 10px 16px; text-align: left; font-size: 0.72rem; text-transform: uppercase;
          letter-spacing: .05em; color: #475569; border-bottom: 1px solid #1e293b;
          background: #161e2c; }
        td { padding: 11px 16px; border-bottom: 1px solid #1e293b; color: #cbd5e1;
          font-variant-numeric: tabular-nums; }
        tr:last-child td { border-bottom: none; }
        tr:hover td { background: #1a2436; }

        .pill { display: inline-flex; align-items: center; gap: 5px;
          padding: 3px 10px; border-radius: 20px; font-size: 0.75rem; font-weight: 600; }
        .pill-in  { background: #052e16; color: #4ade80; }
        .pill-out { background: #1c1007; color: #fbbf24; }
        .pill-no  { background: #1e293b; color: #475569; border: 1px solid #334155; }

        /* Setup guide */
        .guide-card { background: #1e293b; border: 1px solid #334155; border-radius: 10px;
          overflow: hidden; margin-bottom: 20px; }
        .guide-toggle { width: 100%; padding: 14px 18px; background: none; border: none;
          display: flex; align-items: center; justify-content: space-between;
          cursor: pointer; color: #cbd5e1; font-size: 0.9rem; font-weight: 600; }
        .guide-toggle:hover { background: #1a2436; }
        .guide-body { padding: 0 18px 18px; }
        .step-row { display: flex; gap: 14px; padding: 14px 0;
          border-bottom: 1px solid #1e293b; }
        .step-row:last-child { border-bottom: none; }
        .step-num { width: 28px; height: 28px; border-radius: 50%;
          background: #0f172a; border: 2px solid #334155;
          display: flex; align-items: center; justify-content: center;
          font-size: 0.75rem; font-weight: 700; color: #60a5fa; flex-shrink: 0; }
        .step-content { flex: 1; }
        .step-title { font-weight: 600; color: #e2e8f0; margin-bottom: 4px; font-size: 0.875rem; }
        .step-detail { font-size: 0.8rem; color: #64748b; line-height: 1.5; }

        .env-grid { display: grid; grid-template-columns: auto 1fr; gap: 6px 12px;
          margin-top: 14px; padding: 14px; background: #0f172a; border-radius: 8px;
          font-size: 0.8rem; }
        .env-key { font-family: monospace; color: #60a5fa; }
        .env-req { color: #f87171; font-size: 0.72rem; }
        .env-opt { color: #64748b; font-size: 0.72rem; }

        /* Empty state */
        .empty { text-align: center; padding: 40px 16px; color: #475569; font-size: 0.875rem; }

        /* Toast */
        .toast { position: fixed; bottom: 20px; right: 20px; z-index: 999;
          padding: 12px 18px; border-radius: 8px; font-size: 0.875rem;
          font-weight: 500; box-shadow: 0 4px 20px rgba(0,0,0,.4);
          animation: slideUp .25s ease; }
        .toast-ok  { background: #052e16; color: #4ade80; border: 1px solid #166534; }
        .toast-err { background: #1a0708; color: #fca5a5; border: 1px solid #991b1b; }
        @keyframes slideUp { from { transform: translateY(12px); opacity: 0; } to { transform: translateY(0); opacity: 1; } }

        .spinner { display: inline-block; width: 14px; height: 14px;
          border: 2px solid rgba(255,255,255,.25); border-top-color: #fff;
          border-radius: 50%; animation: spin .6s linear infinite; }
        @keyframes spin { to { transform: rotate(360deg); } }

        .cache-note { font-size: 0.72rem; color: #475569; margin-top: 2px; }
      `}</style>

      <div className="page">
        {/* Header */}
        <div className="page-header">
          <div>
            <div className="page-title">ADP Workforce Now</div>
            <div className="page-sub">Clock-in status integration · refreshes every 30 s</div>
          </div>
          <div style={{ display: 'flex', gap: 8 }}>
            <button
              className="btn btn-ghost"
              onClick={() => setShowGuide(g => !g)}
            >
              {showGuide ? '▲ Hide Setup' : '▼ Setup Guide'}
            </button>
            <button
              className="btn btn-primary"
              onClick={handleRefresh}
              disabled={refreshing || !status?.configured}
              title={!status?.configured ? 'ADP not configured' : 'Clear 2-min cache and re-fetch from ADP'}
            >
              {refreshing ? <><span className="spinner" /> Refreshing…</> : '↺ Refresh Cache'}
            </button>
          </div>
        </div>

        {/* Connection banner */}
        {loading ? (
          <div className="banner banner-warn">
            <span className="banner-dot dot-warn" />
            Loading ADP status…
          </div>
        ) : status?.configured ? (
          <div className={`banner ${status.status === 'ok' ? 'banner-ok' : 'banner-err'}`}>
            <span className={`banner-dot ${status.status === 'ok' ? 'dot-ok' : 'dot-err'}`} />
            {status.status === 'ok'
              ? `Connected to ADP Workforce Now — ${status.worker_count ?? 0} active workers loaded`
              : 'ADP connected but API call failed — credentials may be invalid'}
            {status.cache_age_seconds != null && (
              <span style={{ marginLeft: 'auto', fontSize: '0.75rem', color: '#64748b' }}>
                cached {Math.round(status.cache_age_seconds)}s ago
              </span>
            )}
          </div>
        ) : (
          <div className="banner banner-warn">
            <span className="banner-dot dot-warn" />
            <span>
              ADP not configured — set{' '}
              <code style={{ fontFamily: 'monospace', fontSize: '0.85em' }}>ADP_CLIENT_ID</code> and{' '}
              <code style={{ fontFamily: 'monospace', fontSize: '0.85em' }}>ADP_CLIENT_SECRET</code> on Render to enable clock-in tracking
            </span>
          </div>
        )}

        {/* Stat cards (only when configured) */}
        {status?.configured && (
          <div className="stats-row">
            <div className="stat-card">
              <div className="stat-label">Workers</div>
              <div className="stat-value blue">{status.worker_count ?? '—'}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Clocked In</div>
              <div className="stat-value green">{status.clocked_in_count ?? '—'}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Clocked Out</div>
              <div className="stat-value amber">{status.clocked_out_count ?? '—'}</div>
            </div>
            <div className="stat-card">
              <div className="stat-label">Cache Age</div>
              <div className="stat-value slate" style={{ fontSize: '1.2rem' }}>
                {status.cache_age_seconds != null ? `${Math.round(status.cache_age_seconds)}s` : '—'}
              </div>
              <div className="cache-note">max 120s</div>
            </div>
          </div>
        )}

        {/* Worker table */}
        {status?.configured && (
          <div className="section">
            <div className="section-head">
              <span className="section-title">Today's Punch Status</span>
              <span className="section-badge">{workers.length} workers with activity</span>
            </div>
            {workers.length === 0 ? (
              <div className="empty">
                {status.status === 'ok'
                  ? 'No punch activity recorded yet today'
                  : 'Could not retrieve punch data — check credentials'}
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Worker (ADP name)</th>
                      <th>Status</th>
                      <th>Clock In</th>
                      <th>Clock Out</th>
                    </tr>
                  </thead>
                  <tbody>
                    {workers.map(w => (
                      <tr key={w.name}>
                        <td style={{ textTransform: 'capitalize' }}>{w.name}</td>
                        <td>
                          {w.clocked_in ? (
                            <span className="pill pill-in">● In</span>
                          ) : w.clocked_out ? (
                            <span className="pill pill-out">● Out</span>
                          ) : (
                            <span className="pill pill-no">No punch</span>
                          )}
                        </td>
                        <td>{fmtTime(w.in_at)}</td>
                        <td>{fmtTime(w.out_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        )}

        {/* Setup guide (toggle + always shown when not configured) */}
        {(showGuide || !status?.configured) && guide && (
          <div className="guide-card">
            {status?.configured && (
              <button className="guide-toggle" onClick={() => setShowGuide(false)}>
                <span>📋 {guide.title}</span>
                <span style={{ color: '#64748b' }}>▲ hide</span>
              </button>
            )}
            {!status?.configured && (
              <div style={{ padding: '14px 18px', borderBottom: '1px solid #334155' }}>
                <span style={{ fontWeight: 600, color: '#cbd5e1', fontSize: '0.9rem' }}>
                  📋 {guide.title}
                </span>
              </div>
            )}
            <div className="guide-body">
              {guide.steps.map(s => (
                <div key={s.step} className="step-row">
                  <div className="step-num">{s.step}</div>
                  <div className="step-content">
                    <div className="step-title">{s.title}</div>
                    <div className="step-detail">{s.detail}</div>
                  </div>
                </div>
              ))}

              {/* Env var table */}
              <div className="env-grid">
                {Object.entries(guide.env_vars).map(([k, v]) => (
                  <>
                    <span className="env-key" key={`k-${k}`}>{k}</span>
                    <span key={`v-${k}`} className={v === 'required' ? 'env-req' : 'env-opt'}>
                      {v}
                    </span>
                  </>
                ))}
              </div>
            </div>
          </div>
        )}
      </div>

      {toast && (
        <div className={`toast toast-${toast.type}`}>{toast.msg}</div>
      )}
    </>
  );
}

export default function AdpPageWrapper() {
  return (
    <ProtectedRoute>
      <AdpPage />
    </ProtectedRoute>
  );
}
