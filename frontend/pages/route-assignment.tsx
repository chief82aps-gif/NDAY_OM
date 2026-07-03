import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

// ─── Types ───────────────────────────────────────────────────────────────────

interface RouteRow {
  route_code: string;
  service_type: string;
  is_electric: boolean;
  driver_name: string | null;
  transporter_id: string | null;
  quality_rank: number | null;
  quality_standing: string | null;
  quality_score: number | null;
  quality_week: string | null;
  packages: number | null;
  stops: number | null;
  departure_time: string | null;
  wave: string | null;
  staging_location: string | null;
  planned_packages: number | null;
  route_duration_min: number | null;
  van_number: string | null;
  van_warning: string | null;
  is_callout: boolean;
  is_callout_coverage: boolean;
  dm_sent: boolean;
  assignment_status: string;
}

interface DriverRow {
  transporter_id: string;
  quality_rank: number;
  quality_standing: string;
  quality_score: number | null;
  is_callout: boolean;
  assigned_route: string | null;
}

interface Callout {
  id: number;
  transporter_id: string;
  driver_name: string;
  callout_type: string;
  notes: string | null;
  created_at: string | null;
}

interface BoardData {
  date: string;
  route_count: number;
  callout_count: number;
  needs_coverage_count: number;
  quality_week: string | null;
  routes: RouteRow[];
  driver_pool: DriverRow[];
  callouts: Callout[];
}

// ─── Helpers ─────────────────────────────────────────────────────────────────

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const STANDING_COLOR: Record<string, string> = {
  Platinum: '#60a5fa', Gold: '#f59e0b', Silver: '#94a3b8', Bronze: '#b45309', Unknown: '#6b7280',
};

function standingChip(s: string | null) {
  if (!s) return <span style={{ color: '#555' }}>—</span>;
  return (
    <span style={{
      background: '#1e293b', color: STANDING_COLOR[s] || '#888',
      border: `1px solid ${STANDING_COLOR[s] || '#555'}`,
      borderRadius: 4, padding: '1px 6px', fontSize: 10, fontWeight: 700,
    }}>{s}</span>
  );
}

function svcShort(s: string | null): string {
  if (!s) return '—';
  if (s.includes('Rivian')) return 'Electric – Rivian';
  if (s.includes('Nursery')) {
    const m = s.match(/Level (\d)/);
    return `Nursery L${m?.[1] || '?'} – EV`;
  }
  if (s.includes('Electric Vehicle')) return 'Electric EV';
  if (s.includes('Extra Large')) return 'XL Van';
  if (s.includes('14ft')) return 'CDV14';
  if (s.includes('16ft')) return 'CDV16';
  if (s.includes('4WD')) return '4WD P31';
  return s.slice(0, 18);
}

function statusBadge(r: RouteRow) {
  if (r.is_callout && !r.is_callout_coverage) {
    return <span style={{ background: '#78350f', color: '#fcd34d', borderRadius: 4, padding: '1px 6px', fontSize: 10, fontWeight: 700 }}>CALLOUT</span>;
  }
  if (r.is_callout_coverage) {
    return <span style={{ background: '#451a03', color: '#fb923c', borderRadius: 4, padding: '1px 6px', fontSize: 10, fontWeight: 700 }}>COVERAGE</span>;
  }
  if (r.assignment_status === 'finalized') {
    return <span style={{ background: '#14532d', color: '#86efac', borderRadius: 4, padding: '1px 6px', fontSize: 10, fontWeight: 700 }}>FINAL</span>;
  }
  if (r.assignment_status === 'confirmed') {
    return <span style={{ background: '#1e3a5f', color: '#93c5fd', borderRadius: 4, padding: '1px 6px', fontSize: 10, fontWeight: 700 }}>CONF</span>;
  }
  return <span style={{ background: '#1e293b', color: '#64748b', borderRadius: 4, padding: '1px 6px', fontSize: 10 }}>PENDING</span>;
}

function isoToLocal(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric', year: 'numeric' });
}

const CALLOUT_TYPES = ['sick', 'no_show', 'personal', 'other'];

// ─── Callout Modal ────────────────────────────────────────────────────────────

interface CalloutModalProps {
  route: RouteRow;
  onClose: () => void;
  onConfirm: (type: string, notes: string) => void;
}

function CalloutModal({ route, onClose, onConfirm }: CalloutModalProps) {
  const [type, setType] = useState('sick');
  const [notes, setNotes] = useState('');
  return (
    <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.7)', zIndex: 100, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
      <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 10, padding: 28, width: 380 }}>
        <h3 style={{ margin: '0 0 16px', color: '#f59e0b' }}>Mark Callout</h3>
        <p style={{ color: '#94a3b8', fontSize: 13, margin: '0 0 16px' }}>
          <strong style={{ color: '#e2e8f0' }}>{route.driver_name}</strong> — Route {route.route_code}
        </p>
        <p style={{ color: '#64748b', fontSize: 12, margin: '0 0 16px' }}>
          This driver will be moved to the bottom of today's assignment priority. Their route will be flagged for coverage.
        </p>
        <label style={{ fontSize: 11, color: '#94a3b8', display: 'block', marginBottom: 4 }}>Callout type</label>
        <select
          value={type}
          onChange={e => setType(e.target.value)}
          style={{ width: '100%', background: '#0d1117', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', marginBottom: 12 }}
        >
          {CALLOUT_TYPES.map(t => <option key={t} value={t}>{t.replace('_', ' ').replace(/\b\w/g, c => c.toUpperCase())}</option>)}
        </select>
        <label style={{ fontSize: 11, color: '#94a3b8', display: 'block', marginBottom: 4 }}>Notes (optional)</label>
        <textarea
          value={notes}
          onChange={e => setNotes(e.target.value)}
          rows={2}
          style={{ width: '100%', background: '#0d1117', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', marginBottom: 16, resize: 'none', boxSizing: 'border-box' }}
        />
        <div style={{ display: 'flex', gap: 10, justifyContent: 'flex-end' }}>
          <button onClick={onClose} style={{ background: 'none', border: '1px solid #334155', color: '#94a3b8', borderRadius: 6, padding: '6px 14px', cursor: 'pointer' }}>Cancel</button>
          <button onClick={() => onConfirm(type, notes)} style={{ background: '#f59e0b', color: '#000', border: 'none', borderRadius: 6, padding: '6px 18px', fontWeight: 700, cursor: 'pointer' }}>Confirm Callout</button>
        </div>
      </div>
    </div>
  );
}

// ─── Main Page ────────────────────────────────────────────────────────────────

export default function RouteAssignmentPage() {
  const [dateStr, setDateStr] = useState(new Date().toISOString().slice(0, 10));
  const [board, setBoard] = useState<BoardData | null>(null);
  const [loading, setLoading] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [finalizing, setFinalizing] = useState(false);
  const [msg, setMsg] = useState<{ text: string; color: string } | null>(null);
  const [calloutModal, setCalloutModal] = useState<RouteRow | null>(null);
  const [expandedRoute, setExpandedRoute] = useState<string | null>(null);
  const [search, setSearch] = useState('');
  const [filterCallout, setFilterCallout] = useState(false);

  const api = resolveApi();

  const flash = (text: string, color = '#60a5fa') => {
    setMsg({ text, color });
    setTimeout(() => setMsg(null), 5000);
  };

  const loadBoard = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${api}/route-assignment/board?date_str=${dateStr}`);
      if (!r.ok) throw new Error(await r.text());
      setBoard(await r.json());
    } catch (e: any) {
      flash(`Error loading board: ${e.message}`, '#ef4444');
    } finally {
      setLoading(false);
    }
  }, [api, dateStr]);

  useEffect(() => { loadBoard(); }, [loadBoard]);

  async function handleAutoAssign() {
    setAssigning(true);
    try {
      const r = await fetch(`${api}/route-assignment/auto-assign?date_str=${dateStr}`, { method: 'POST' });
      const data = await r.json();
      flash(`Auto-assigned: ${data.total_routes - data.callout_routes} confirmed, ${data.callout_routes} callout routes filled.`);
      loadBoard();
    } catch (e: any) {
      flash(`Auto-assign failed: ${e.message}`, '#ef4444');
    } finally {
      setAssigning(false);
    }
  }

  async function handleFinalize() {
    setFinalizing(true);
    try {
      const r = await fetch(`${api}/route-assignment/finalize?date_str=${dateStr}`, { method: 'POST' });
      const data = await r.json();
      flash(`${data.message}`, '#86efac');
      loadBoard();
    } catch (e: any) {
      flash(`Finalize failed: ${e.message}`, '#ef4444');
    } finally {
      setFinalizing(false);
    }
  }

  async function handleCallout(route: RouteRow, type: string, notes: string) {
    if (!route.transporter_id) return;
    setCalloutModal(null);
    try {
      const r = await fetch(`${api}/route-assignment/callout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          transporter_id: route.transporter_id,
          driver_name: route.driver_name,
          callout_date: dateStr,
          callout_type: type,
          notes,
        }),
      });
      const data = await r.json();
      flash(`Callout recorded for ${route.driver_name}. Route ${route.route_code} flagged for coverage.`, '#f59e0b');
      loadBoard();
    } catch (e: any) {
      flash(`Callout failed: ${e.message}`, '#ef4444');
    }
  }

  async function handleRemoveCallout(id: number) {
    try {
      await fetch(`${api}/route-assignment/callout/${id}`, { method: 'DELETE' });
      flash('Callout removed — driver available again.', '#86efac');
      loadBoard();
    } catch (e: any) {
      flash(`Remove failed: ${e.message}`, '#ef4444');
    }
  }

  const routes = (board?.routes ?? []).filter(r => {
    if (filterCallout && !r.is_callout) return false;
    if (search) {
      const q = search.toLowerCase();
      return (r.driver_name || '').toLowerCase().includes(q) || r.route_code.toLowerCase().includes(q);
    }
    return true;
  });

  const td: React.CSSProperties = { padding: '7px 10px', verticalAlign: 'middle', fontSize: 12 };
  const th: React.CSSProperties = { padding: '7px 10px', textAlign: 'left', fontSize: 11, fontWeight: 600, color: '#94a3b8' };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0d1117', color: '#e2e8f0', fontFamily: 'sans-serif', padding: '20px 24px' }}>

        {calloutModal && (
          <CalloutModal
            route={calloutModal}
            onClose={() => setCalloutModal(null)}
            onConfirm={(type, notes) => handleCallout(calloutModal, type, notes)}
          />
        )}

        {/* Header */}
        <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 20, flexWrap: 'wrap' }}>
          <div>
            <h1 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Route Assignment Board</h1>
            <p style={{ margin: '2px 0 0', color: '#94a3b8', fontSize: 12 }}>
              Cortex + DOP + Fleet + Quality Rankings · Callout rule active
            </p>
          </div>
          <input
            type="date"
            value={dateStr}
            onChange={e => setDateStr(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13 }}
          />
          <button onClick={loadBoard} disabled={loading} style={{ background: '#1e293b', color: '#60a5fa', border: '1px solid #334155', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13 }}>
            {loading ? 'Loading…' : 'Refresh'}
          </button>
          <button onClick={handleAutoAssign} disabled={assigning} style={{ background: '#1e3a5f', color: '#93c5fd', border: '1px solid #3b82f6', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            {assigning ? 'Running…' : 'Auto-Assign'}
          </button>
          <button onClick={handleFinalize} disabled={finalizing} style={{ background: '#14532d', color: '#86efac', border: '1px solid #22c55e', borderRadius: 6, padding: '6px 14px', cursor: 'pointer', fontSize: 13, fontWeight: 600 }}>
            {finalizing ? 'Finalizing…' : 'Finalize'}
          </button>
        </div>

        {/* Flash message */}
        {msg && (
          <div style={{ background: '#1e293b', border: `1px solid ${msg.color}`, borderRadius: 8, padding: '8px 16px', marginBottom: 16, color: msg.color, fontSize: 13 }}>
            {msg.text}
          </div>
        )}

        {/* Stats bar */}
        {board && (
          <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
            {[
              { label: 'Routes', val: board.route_count, color: '#60a5fa' },
              { label: 'Callouts', val: board.callout_count, color: '#f59e0b' },
              { label: 'Need Coverage', val: board.needs_coverage_count, color: board.needs_coverage_count > 0 ? '#ef4444' : '#22c55e' },
              { label: 'Quality Week', val: board.quality_week || 'No data', color: '#8b5cf6' },
            ].map(s => (
              <div key={s.label} style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 14px' }}>
                <span style={{ color: '#94a3b8', fontSize: 11 }}>{s.label} </span>
                <span style={{ color: s.color, fontWeight: 700, fontSize: 14 }}>{s.val}</span>
              </div>
            ))}
          </div>
        )}

        {/* Active callouts strip */}
        {board && board.callouts.length > 0 && (
          <div style={{ background: '#1c1107', border: '1px solid #78350f', borderRadius: 8, padding: '10px 14px', marginBottom: 16 }}>
            <span style={{ color: '#f59e0b', fontWeight: 700, fontSize: 12, marginRight: 10 }}>CALLOUTS TODAY</span>
            {board.callouts.map(c => (
              <span key={c.id} style={{ display: 'inline-flex', alignItems: 'center', gap: 6, marginRight: 10, background: '#292400', border: '1px solid #78350f', borderRadius: 6, padding: '2px 8px' }}>
                <span style={{ color: '#fcd34d', fontSize: 12 }}>{c.driver_name}</span>
                <span style={{ color: '#6b7280', fontSize: 11 }}>({c.callout_type})</span>
                <button
                  onClick={() => handleRemoveCallout(c.id)}
                  style={{ background: 'none', border: 'none', color: '#6b7280', cursor: 'pointer', fontSize: 13, padding: '0 2px', lineHeight: 1 }}
                  title="Remove callout"
                >✕</button>
              </span>
            ))}
          </div>
        )}

        {/* Filters */}
        <div style={{ display: 'flex', gap: 10, marginBottom: 12, alignItems: 'center' }}>
          <input
            placeholder="Search driver or route…"
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '5px 10px', fontSize: 12, width: 200 }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 12, color: '#94a3b8', cursor: 'pointer' }}>
            <input type="checkbox" checked={filterCallout} onChange={e => setFilterCallout(e.target.checked)} />
            Show callouts only
          </label>
          <span style={{ color: '#475569', fontSize: 12 }}>{routes.length} routes shown</span>
        </div>

        {/* Assignment Board */}
        {loading ? (
          <div style={{ textAlign: 'center', padding: 48, color: '#60a5fa' }}>Loading board…</div>
        ) : !board || routes.length === 0 ? (
          <div style={{ background: '#1e293b', borderRadius: 10, padding: 32, textAlign: 'center', color: '#94a3b8' }}>
            No Cortex data for {dateStr}. Upload a Routes file via #nday-operations-management or select a different date.
          </div>
        ) : (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr style={{ background: '#1a3c6e' }}>
                  {['Route', 'Driver', 'Standing', 'Van / Type', 'Wave', 'Staging', 'Pkgs', 'Depart', 'Status', ''].map(h => (
                    <th key={h} style={{ ...th, color: '#fff' }}>{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {routes.map((r, i) => {
                  const rowBg = r.is_callout ? '#1c1107' : r.is_callout_coverage ? '#1a0f00' : i % 2 === 0 ? '#0d1117' : '#111827';
                  const expanded = expandedRoute === r.route_code;
                  return (
                    <>
                      <tr
                        key={r.route_code}
                        style={{ background: rowBg, cursor: 'pointer', borderBottom: '1px solid #1e293b' }}
                        onClick={() => setExpandedRoute(expanded ? null : r.route_code)}
                      >
                        <td style={{ ...td, fontWeight: 700, color: r.is_electric ? '#60a5fa' : '#e2e8f0' }}>
                          {r.route_code}
                          {r.is_electric && <span style={{ fontSize: 9, marginLeft: 4, color: '#60a5fa' }}>⚡</span>}
                        </td>
                        <td style={td}>
                          <div style={{ color: r.is_callout ? '#fcd34d' : '#e2e8f0' }}>
                            {r.driver_name || '—'}
                          </div>
                          {r.transporter_id && (
                            <div style={{ color: '#475569', fontSize: 10 }}>{r.transporter_id}</div>
                          )}
                        </td>
                        <td style={{ ...td, whiteSpace: 'nowrap' }}>
                          {standingChip(r.quality_standing)}
                          {r.quality_rank && <span style={{ color: '#475569', fontSize: 10, marginLeft: 4 }}>#{r.quality_rank}</span>}
                        </td>
                        <td style={td}>
                          <div style={{ color: '#e2e8f0' }}>{r.van_number || '—'}</div>
                          <div style={{ color: '#475569', fontSize: 10 }}>{svcShort(r.service_type)}</div>
                          {r.van_warning === 'electric_violation' && (
                            <div style={{ color: '#ef4444', fontSize: 10 }}>⚠ Electric mismatch</div>
                          )}
                          {r.van_warning === 'no_van_available' && (
                            <div style={{ color: '#f59e0b', fontSize: 10 }}>⚠ No van available</div>
                          )}
                        </td>
                        <td style={{ ...td, color: '#94a3b8' }}>{r.wave || '—'}</td>
                        <td style={{ ...td, color: '#94a3b8' }}>{r.staging_location || '—'}</td>
                        <td style={{ ...td, color: '#e2e8f0' }}>{r.packages ?? r.planned_packages ?? '—'}</td>
                        <td style={{ ...td, color: '#e2e8f0' }}>{r.departure_time || '—'}</td>
                        <td style={td}>{statusBadge(r)}</td>
                        <td style={td}>
                          {!r.is_callout && r.transporter_id && (
                            <button
                              onClick={e => { e.stopPropagation(); setCalloutModal(r); }}
                              style={{ background: '#3d2000', color: '#f59e0b', border: '1px solid #78350f', borderRadius: 4, padding: '2px 8px', cursor: 'pointer', fontSize: 11 }}
                            >
                              Callout
                            </button>
                          )}
                          {r.is_callout && (
                            <span style={{ color: '#f59e0b', fontSize: 11 }}>📵</span>
                          )}
                        </td>
                      </tr>
                      {expanded && (
                        <tr key={`${r.route_code}-detail`} style={{ background: '#080d14' }}>
                          <td colSpan={10} style={{ padding: '12px 24px' }}>
                            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(180px, 1fr))', gap: 12 }}>
                              {[
                                ['Route Code', r.route_code],
                                ['Transporter ID', r.transporter_id],
                                ['Service Type', r.service_type],
                                ['Wave', r.wave],
                                ['Staging', r.staging_location],
                                ['Van', r.van_number],
                                ['Packages', r.packages ?? r.planned_packages],
                                ['Stops', r.stops],
                                ['Depart', r.departure_time],
                                ['Est. Duration', r.route_duration_min ? `${Math.floor(r.route_duration_min / 60)}h ${r.route_duration_min % 60}m` : null],
                                ['Quality Week', r.quality_week],
                                ['Quality Score', r.quality_score?.toFixed(1)],
                                ['DM Sent', r.dm_sent ? 'Yes' : 'No'],
                                ['Callout Coverage', r.is_callout_coverage ? 'Yes ⚠' : 'No'],
                              ].map(([label, val]) => (
                                <div key={String(label)}>
                                  <div style={{ color: '#475569', fontSize: 10 }}>{label}</div>
                                  <div style={{ color: '#cbd5e1', fontSize: 12 }}>{val ?? '—'}</div>
                                </div>
                              ))}
                            </div>
                          </td>
                        </tr>
                      )}
                    </>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        {/* Driver Pool Panel */}
        {board && board.driver_pool.length > 0 && (
          <div style={{ marginTop: 32 }}>
            <h2 style={{ fontSize: 15, fontWeight: 700, marginBottom: 12 }}>
              Driver Priority Pool
              <span style={{ color: '#475569', fontWeight: 400, fontSize: 12, marginLeft: 8 }}>
                ranked by quality · callouts at bottom
              </span>
            </h2>
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 8 }}>
              {board.driver_pool.slice(0, 60).map(d => (
                <div
                  key={d.transporter_id}
                  style={{
                    background: d.is_callout ? '#1c1107' : '#111827',
                    border: `1px solid ${d.is_callout ? '#78350f' : '#1e293b'}`,
                    borderRadius: 7,
                    padding: '8px 12px',
                    opacity: d.is_callout ? 0.7 : 1,
                  }}
                >
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                    <span style={{ color: '#475569', fontSize: 10 }}>#{d.quality_rank}</span>
                    {standingChip(d.quality_standing)}
                    {d.is_callout && <span style={{ color: '#f59e0b', fontSize: 10 }}>CALLOUT</span>}
                  </div>
                  <div style={{ color: '#94a3b8', fontSize: 10, marginTop: 2 }}>{d.transporter_id}</div>
                  <div style={{ color: '#475569', fontSize: 10, marginTop: 2 }}>
                    {d.assigned_route ? `Route: ${d.assigned_route}` : d.is_callout ? 'Available (last resort)' : 'Available'}
                  </div>
                  {d.quality_score !== null && (
                    <div style={{ color: '#475569', fontSize: 10 }}>Score: {d.quality_score?.toFixed(1)}</div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Footer callout rule notice */}
        <div style={{ marginTop: 32, background: '#111827', border: '1px solid #334155', borderRadius: 8, padding: '10px 16px', fontSize: 11, color: '#64748b' }}>
          <strong style={{ color: '#94a3b8' }}>Callout Rule:</strong> Drivers marked as called-out drop to the bottom of the assignment priority queue regardless of quality ranking. They are only assigned a route when no non-callout driver is available. Callout-coverage assignments are shown in amber.
        </div>
      </div>
    </ProtectedRoute>
  );
}
