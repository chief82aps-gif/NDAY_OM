import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface GlitchReport {
  id: number;
  reporter_name: string | null;
  source_page: string | null;
  description: string;
  status: 'open' | 'resolved';
  reported_at: string | null;
  resolved_at: string | null;
  resolved_by: string | null;
}

const SOURCE_LABELS: Record<string, string> = {
  driver_home: 'Driver Home',
  dispatch_home: 'Dispatch Home',
  hr_home: 'HR Home',
};

export default function GlitchReportsPage() {
  const api = resolveApi();
  const [filter, setFilter] = useState<'open' | 'resolved' | 'all'>('open');
  const [reports, setReports] = useState<GlitchReport[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const qs = filter === 'all' ? '' : `?status=${filter}`;
      const res = await fetch(`${api}/glitch-reports${qs}`);
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      setReports(data.reports ?? []);
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api, filter]);

  useEffect(() => { load(); }, [load]);

  const toggle = async (id: number, action: 'resolve' | 'reopen') => {
    try {
      await fetch(`${api}/glitch-reports/${id}/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: action === 'resolve' ? JSON.stringify({ resolved_by: 'dispatch_console' }) : undefined,
      });
      load();
    } catch {
      setError('Action failed.');
    }
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 20 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>🐛 App Glitch Reports</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Submitted from the "Report an App Glitch" button on every Home tab.
            </p>
          </div>

          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            {(['open', 'resolved', 'all'] as const).map(f => (
              <button
                key={f}
                onClick={() => setFilter(f)}
                style={{
                  padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  border: filter === f ? '1px solid #2563eb' : '1px solid #334155',
                  background: filter === f ? '#2563eb' : '#1e293b',
                  color: filter === f ? '#fff' : '#94a3b8',
                  textTransform: 'capitalize',
                }}
              >
                {f}
              </button>
            ))}
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {!loading && !error && reports && reports.length === 0 && (
            <p style={{ color: '#64748b' }}>No {filter === 'all' ? '' : filter} reports.</p>
          )}

          {!loading && !error && reports && reports.map(r => (
            <div key={r.id} style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 12, border: '1px solid #334155' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8, gap: 12 }}>
                <div>
                  <span style={{ fontWeight: 700, color: '#f1f5f9', fontSize: 14 }}>{r.reporter_name || 'Unknown'}</span>
                  <span style={{ fontSize: 12, color: '#64748b', marginLeft: 8 }}>
                    {SOURCE_LABELS[r.source_page || ''] || r.source_page} · {r.reported_at ? new Date(r.reported_at).toLocaleString() : '—'}
                  </span>
                </div>
                <span style={{
                  fontSize: 11, fontWeight: 700, padding: '2px 8px', borderRadius: 4, textTransform: 'uppercase',
                  color: r.status === 'open' ? '#f87171' : '#4ade80',
                  border: `1px solid ${r.status === 'open' ? '#7f1d1d' : '#14532d'}`,
                }}>
                  {r.status}
                </span>
              </div>
              <p style={{ margin: '0 0 12px', fontSize: 14, color: '#e2e8f0', whiteSpace: 'pre-wrap' }}>{r.description}</p>
              <button
                onClick={() => toggle(r.id, r.status === 'open' ? 'resolve' : 'reopen')}
                style={{
                  padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer',
                  border: '1px solid #334155', background: '#0f172a', color: '#94a3b8',
                }}
              >
                {r.status === 'open' ? 'Mark Resolved' : 'Reopen'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
