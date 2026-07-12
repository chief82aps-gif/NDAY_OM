import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface Driver {
  id: number;
  payroll_name: string;
  is_active: boolean;
  source: string;
  last_seen_on_schedule: string | null;
  flagged_inactive: boolean;
  flagged_inactive_at: string | null;
  slack_member_id: string | null;
  slack_verified: boolean;
  phone: string | null;
}

const SOURCE_LABELS: Record<string, string> = {
  adp_import: 'ADP Import',
  schedule_upload: 'Schedule Upload',
  hr_module: 'HR Module',
};

const SOURCE_COLORS: Record<string, string> = {
  adp_import: '#8b5cf6',
  schedule_upload: '#0ea5e9',
  hr_module: '#10b981',
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

export default function DriversPage() {
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'flagged'>('all');

  const api = resolveApi();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${api}/drivers`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setDrivers(data.drivers ?? []);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load drivers');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const displayed = filter === 'flagged' ? drivers.filter(d => d.flagged_inactive) : drivers;

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Driver Profiles</h1>
              <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
                Interim source of truth, fed by ADP import and schedule uploads — a future HR module will own create/terminate.
              </p>
            </div>
            <button
              onClick={load}
              disabled={loading}
              style={{
                background: loading ? '#1e293b' : '#0ea5e9', color: '#fff', border: 'none', borderRadius: 8,
                padding: '10px 20px', cursor: loading ? 'default' : 'pointer', fontWeight: 600, fontSize: 14,
              }}
            >
              {loading ? 'Loading…' : 'Refresh'}
            </button>
          </div>

          <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
            {([
              ['all', `All Active (${drivers.length})`],
              ['flagged', `Flagged Inactive (${drivers.filter(d => d.flagged_inactive).length})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                style={{
                  background: filter === key ? '#1e40af' : '#1e293b', color: filter === key ? '#fff' : '#94a3b8',
                  border: 'none', borderRadius: 8, padding: '8px 18px', cursor: 'pointer', fontWeight: 600, fontSize: 14,
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
            <div style={{ textAlign: 'center', color: '#475569', padding: '60px 0' }}>Nothing to show.</div>
          )}

          {displayed.length > 0 && (
            <div style={{ background: '#1e293b', borderRadius: 12, overflow: 'hidden', border: '1px solid #334155' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                  <tr style={{ background: '#0f172a', textAlign: 'left' }}>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Driver</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Source</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Last Seen on Schedule</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Slack</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((d) => (
                    <tr key={d.id} style={{ borderTop: '1px solid #334155' }}>
                      <td style={{ padding: '12px 16px', color: '#e2e8f0' }}>{d.payroll_name}</td>
                      <td style={{ padding: '12px 16px' }}>
                        <span style={{
                          display: 'inline-block', padding: '3px 10px', borderRadius: 999, fontSize: 12,
                          fontWeight: 600, color: '#fff', background: SOURCE_COLORS[d.source] ?? '#64748b',
                        }}>
                          {SOURCE_LABELS[d.source] ?? d.source}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{fmt(d.last_seen_on_schedule)}</td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{d.slack_verified ? '✅ Verified' : d.slack_member_id ? 'Linked' : '—'}</td>
                      <td style={{ padding: '12px 16px' }}>
                        {d.flagged_inactive ? (
                          <span style={{ color: '#f59e0b', fontWeight: 600 }}>⚠ Not seen 30+ days</span>
                        ) : (
                          <span style={{ color: '#10b981' }}>Active</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
