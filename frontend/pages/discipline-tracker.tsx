import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';

interface TrackerItem {
  source: 'manager_accountability' | 'dvic' | 'dvic_violation' | 'attendance' | 'injury' | 'crash';
  id: number;
  shift_date: string | null;
  driver_name: string | null;
  manager_name: string | null;
  writeup_type: string;
  source_detail: string;
  dm_sent_at: string | null;
  needs_sign_role: string | null;
  occurrence_count: number | null;
}

const TYPE_LABELS: Record<string, string> = {
  unsigned_callout: 'Unsigned Callout',
  dvic_repeat_violation: 'DVIC — Formal Write-Up',
  dvic_stage_1: 'DVIC — Safety Reminder',
  dvic_stage_2: 'DVIC — Written Notice',
  dvic_stage_3: 'DVIC — Final Warning',
  dvic_stage_4: 'DVIC — Formal Write-Up',
  injury_report: 'Injury Report',
  crash_report: 'Crash Report',
};

const TYPE_COLORS: Record<string, string> = {
  unsigned_callout: '#f59e0b',
  dvic_stage_1: '#0ea5e9',
  dvic_stage_2: '#8b5cf6',
  dvic_stage_3: '#f97316',
  dvic_stage_4: '#ef4444',
  dvic_repeat_violation: '#ef4444',
  injury_report: '#dc2626',
  crash_report: '#7c2d12',
};

const ROLE_LABELS: Record<string, string> = {
  ops_manager: 'Ops Manager',
  hr: 'HR',
  owner: 'Owner',
  dispatch: 'Dispatch',
};

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

function typeLabelFor(item: TrackerItem): string {
  if (item.writeup_type.startsWith('attendance_')) return 'Callout';
  return TYPE_LABELS[item.writeup_type] ?? item.writeup_type;
}

function colorFor(item: TrackerItem): string {
  if (item.writeup_type.startsWith('attendance_')) return '#f59e0b';
  return TYPE_COLORS[item.writeup_type] ?? '#64748b';
}

export default function DisciplineTrackerPage() {
  const { user } = useAuth();
  const [items, setItems] = useState<TrackerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | TrackerItem['source']>('all');
  const [signingKey, setSigningKey] = useState<string | null>(null);
  const [signerName, setSignerName] = useState('');
  const [signError, setSignError] = useState('');

  const api = resolveApi();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${api}/manager-accountability/discipline-tracker`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setItems(data.items ?? []);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load discipline tracker');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const displayed = filter === 'all'
    ? items
    : filter === 'dvic'
      ? items.filter(i => i.source === 'dvic' || i.source === 'dvic_violation')
      : items.filter(i => i.source === filter);

  function canSign(item: TrackerItem): boolean {
    if (!item.needs_sign_role) return false;   // crash rows: read-only here
    if (!user?.role) return false;
    return user.role === 'admin' || user.role === item.needs_sign_role;
  }

  function keyFor(item: TrackerItem) {
    return `${item.source}-${item.id}`;
  }

  async function submitSign(item: TrackerItem) {
    setSignError('');
    if (!signerName.trim()) { setSignError('Please type your full name to sign.'); return; }
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    const headers: Record<string, string> = {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };

    try {
      let res: Response;
      if (item.source === 'attendance') {
        res = await fetch(`${api}/attendance/events/${item.id}/manager-sign`, {
          method: 'POST', headers,
          body: JSON.stringify({ manager_name: signerName.trim() }),
        });
      } else if (item.source === 'injury') {
        res = await fetch(`${api}/injury-reports/${item.id}/sign`, {
          method: 'POST', headers,
          body: JSON.stringify({ role: item.needs_sign_role, signed_by: signerName.trim() }),
        });
      } else if (item.source === 'dvic') {
        res = await fetch(`${api}/dvic/counseling/${item.id}/sign`, {
          method: 'POST', headers,
          body: JSON.stringify({ signed_by: signerName.trim() }),
        });
      } else if (item.source === 'dvic_violation') {
        res = await fetch(`${api}/dvic/violations/${item.id}/sign`, {
          method: 'POST', headers,
          body: JSON.stringify({ signed_by: signerName.trim() }),
        });
      } else {
        setSignError('This item type cannot be signed from this page.');
        return;
      }

      if (!res.ok) {
        const d = await res.json().catch(() => null);
        throw new Error(d?.detail ?? 'Sign failed');
      }
      setSigningKey(null);
      setSignerName('');
      await load();
    } catch (e: unknown) {
      setSignError(e instanceof Error ? e.message : 'Error signing.');
    }
  }

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1180, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Discipline Tracker</h1>
              <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
                All pending write-ups awaiting sign-off — callouts, DVIC, injury reports, and crash reports.
              </p>
            </div>
            <button
              onClick={load}
              disabled={loading}
              style={{
                background: loading ? '#1e293b' : '#0ea5e9',
                color: '#fff',
                border: 'none',
                borderRadius: 8,
                padding: '10px 20px',
                cursor: loading ? 'default' : 'pointer',
                fontWeight: 600,
                fontSize: 14,
              }}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          <div style={{ display: 'flex', gap: 4, marginBottom: 24, flexWrap: 'wrap' }}>
            {([
              ['all', `All (${items.length})`],
              ['attendance', `Callouts (${items.filter(i => i.source === 'attendance').length})`],
              ['dvic', `DVIC (${items.filter(i => i.source === 'dvic' || i.source === 'dvic_violation').length})`],
              ['injury', `Injury (${items.filter(i => i.source === 'injury').length})`],
              ['crash', `Crash (${items.filter(i => i.source === 'crash').length})`],
              ['manager_accountability', `Other (${items.filter(i => i.source === 'manager_accountability').length})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                style={{
                  background: filter === key ? '#1e40af' : '#1e293b',
                  color: filter === key ? '#fff' : '#94a3b8',
                  border: 'none',
                  borderRadius: 8,
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontWeight: 600,
                  fontSize: 14,
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}

          {!loading && displayed.length === 0 && !error && (
            <div style={{ textAlign: 'center', color: '#475569', padding: '60px 0' }}>
              Nothing pending review right now.
            </div>
          )}

          {displayed.length > 0 && (
            <div style={{ background: '#1e293b', borderRadius: 12, overflow: 'hidden', border: '1px solid #334155' }}>
              <div style={{ overflowX: 'auto' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                  <tr style={{ background: '#0f172a', textAlign: 'left' }}>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Type</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Driver</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Detail</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Occurrences</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Who Signs</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Date</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Action</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((item) => {
                    const key = keyFor(item);
                    const signable = canSign(item);
                    return (
                      <tr key={key} style={{ borderTop: '1px solid #334155' }}>
                        <td style={{ padding: '12px 16px' }}>
                          <span
                            style={{
                              display: 'inline-block',
                              padding: '3px 10px',
                              borderRadius: 999,
                              fontSize: 12,
                              fontWeight: 600,
                              color: '#fff',
                              background: colorFor(item),
                            }}
                          >
                            {typeLabelFor(item)}
                          </span>
                        </td>
                        <td style={{ padding: '12px 16px', color: '#e2e8f0' }}>{item.driver_name ?? '—'}</td>
                        <td style={{ padding: '12px 16px', color: '#e2e8f0' }}>{item.source_detail}</td>
                        <td style={{ padding: '12px 16px', color: '#94a3b8', fontVariantNumeric: 'tabular-nums' }}>
                          {item.occurrence_count ?? '—'}
                        </td>
                        <td style={{ padding: '12px 16px', color: '#94a3b8' }}>
                          {item.needs_sign_role ? (ROLE_LABELS[item.needs_sign_role] ?? item.needs_sign_role) : '—'}
                        </td>
                        <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{fmt(item.shift_date)}</td>
                        <td style={{ padding: '12px 16px' }}>
                          {!item.needs_sign_role ? (
                            <span style={{ fontSize: 12, color: '#475569' }}>Via Slack</span>
                          ) : signingKey === key ? (
                            <div style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                              <input
                                autoFocus
                                value={signerName}
                                onChange={e => setSignerName(e.target.value)}
                                placeholder="Type your name"
                                style={{ width: 130, background: '#0f172a', border: '1px solid #334155', borderRadius: 6, padding: '6px 8px', color: '#f1f5f9', fontSize: 13 }}
                              />
                              <button
                                onClick={() => submitSign(item)}
                                style={{ background: '#16a34a', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 12px', fontSize: 12, fontWeight: 600, cursor: 'pointer' }}
                              >
                                Confirm
                              </button>
                              <button
                                onClick={() => { setSigningKey(null); setSignError(''); }}
                                style={{ background: 'transparent', color: '#94a3b8', border: 'none', fontSize: 12, cursor: 'pointer' }}
                              >
                                Cancel
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => { setSigningKey(key); setSignerName(''); setSignError(''); }}
                              disabled={!signable}
                              title={!signable ? `Requires ${ROLE_LABELS[item.needs_sign_role] ?? item.needs_sign_role} login` : undefined}
                              style={{
                                background: signable ? '#1d4ed8' : '#1e293b',
                                color: signable ? '#fff' : '#475569',
                                border: 'none',
                                borderRadius: 6,
                                padding: '6px 14px',
                                fontSize: 12,
                                fontWeight: 600,
                                cursor: signable ? 'pointer' : 'not-allowed',
                              }}
                            >
                              Sign as {ROLE_LABELS[item.needs_sign_role] ?? item.needs_sign_role}
                            </button>
                          )}
                          {signingKey === key && signError && (
                            <p style={{ color: '#f87171', fontSize: 11, margin: '4px 0 0' }}>{signError}</p>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
              </div>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
