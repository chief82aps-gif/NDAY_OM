import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface TrackerItem {
  source: 'manager_accountability' | 'dvic';
  id: number;
  shift_date: string | null;
  manager_name: string | null;
  writeup_type: string;
  source_detail: string;
  dm_sent_at: string | null;
}

const TYPE_LABELS: Record<string, string> = {
  unsigned_callout: 'Unsigned Callout',
  dvic_repeat_violation: 'DVIC — Formal Write-Up',
  dvic_stage_1: 'DVIC — Safety Reminder',
  dvic_stage_2: 'DVIC — Written Notice',
  dvic_stage_3: 'DVIC — Final Warning',
  dvic_stage_4: 'DVIC — Formal Write-Up',
};

const TYPE_COLORS: Record<string, string> = {
  unsigned_callout: '#f59e0b',
  dvic_stage_1: '#0ea5e9',
  dvic_stage_2: '#8b5cf6',
  dvic_stage_3: '#f97316',
  dvic_stage_4: '#ef4444',
  dvic_repeat_violation: '#ef4444',
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

export default function DisciplineTrackerPage() {
  const [items, setItems] = useState<TrackerItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'manager_accountability' | 'dvic'>('all');

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

  const displayed = filter === 'all' ? items : items.filter(i => i.source === filter);

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Discipline Tracker</h1>
              <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
                All pending write-ups awaiting NDAY Management review — unsigned callouts, DVIC safety notices, and formal write-ups.
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

          <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
            {([
              ['all', `All (${items.length})`],
              ['manager_accountability', `Manager Accountability (${items.filter(i => i.source === 'manager_accountability').length})`],
              ['dvic', `DVIC (${items.filter(i => i.source === 'dvic').length})`],
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
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                  <tr style={{ background: '#0f172a', textAlign: 'left' }}>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Type</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Detail</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Manager</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Date</th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((item) => (
                    <tr key={`${item.source}-${item.id}`} style={{ borderTop: '1px solid #334155' }}>
                      <td style={{ padding: '12px 16px' }}>
                        <span
                          style={{
                            display: 'inline-block',
                            padding: '3px 10px',
                            borderRadius: 999,
                            fontSize: 12,
                            fontWeight: 600,
                            color: '#fff',
                            background: TYPE_COLORS[item.writeup_type] ?? '#64748b',
                          }}
                        >
                          {TYPE_LABELS[item.writeup_type] ?? item.writeup_type}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', color: '#e2e8f0' }}>{item.source_detail}</td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{item.manager_name ?? '—'}</td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{fmt(item.shift_date)}</td>
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
