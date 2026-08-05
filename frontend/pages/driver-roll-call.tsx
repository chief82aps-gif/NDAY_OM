import { useEffect, useState, useCallback, CSSProperties } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface RollCallRow {
  driver_name: string;
  acknowledged_at: string | null;
  arrived_at: string | null;
  rts_status: 'not_started' | 'in_progress' | 'complete';
  rts_at: string | null;
  eod_at: string | null;
  efficiency_pct: number | null;
}

function todayStr(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function fmtTime(t: string | null): string {
  if (!t) return '';
  try {
    return new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
  } catch { return ''; }
}

const MISSING_CELL_STYLE: CSSProperties = {
  background: '#3b1e1e',
  color: '#fca5a5',
  fontWeight: 600,
};

function TimeCell({ time, inProgress }: { time: string | null; inProgress?: boolean }) {
  if (!time) {
    return (
      <td style={{ padding: '8px 12px', textAlign: 'center', ...MISSING_CELL_STYLE }}>
        MISSING
      </td>
    );
  }
  return (
    <td style={{ padding: '8px 12px', textAlign: 'center', color: inProgress ? '#fbbf24' : '#e2e8f0' }}>
      {fmtTime(time)}{inProgress ? ' (in progress)' : ''}
    </td>
  );
}

function EfficiencyCell({ pct }: { pct: number | null }) {
  if (pct === null) {
    return (
      <td style={{ padding: '8px 12px', textAlign: 'center', ...MISSING_CELL_STYLE }}>
        MISSING
      </td>
    );
  }
  const color = pct >= 100 ? '#4ade80' : pct >= 80 ? '#fbbf24' : '#f87171';
  return (
    <td style={{ padding: '8px 12px', textAlign: 'center', color, fontWeight: 700 }}>
      {pct}%
    </td>
  );
}

export default function DriverRollCallPage() {
  const api = resolveApi();
  const [dateStr, setDateStr] = useState(todayStr());
  const [rows, setRows] = useState<RollCallRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [search, setSearch] = useState('');
  const [missingOnly, setMissingOnly] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${api}/rostering/roll-call/${dateStr}`);
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      setRows(data.drivers ?? []);
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api, dateStr]);

  useEffect(() => { load(); }, [load]);

  const filtered = rows
    .filter(r => !search || r.driver_name.toLowerCase().includes(search.toLowerCase()))
    .filter(r => !missingOnly || !r.eod_at);

  const counts = {
    acknowledged: rows.filter(r => r.acknowledged_at).length,
    arrived: rows.filter(r => r.arrived_at).length,
    rts: rows.filter(r => r.rts_status === 'complete').length,
    eod: rows.filter(r => r.eod_at).length,
  };
  const missingEod = rows.filter(r => !r.eod_at);

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0d1117', color: '#e2e8f0', fontFamily: 'sans-serif', padding: '24px 32px' }}>
        <div style={{ marginBottom: 20 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>📋 Driver Roll Call</h1>
          <p style={{ color: '#94a3b8', marginTop: 4, fontSize: 13 }}>
            Acknowledged (night before) → Arrived → RTS submission → EOD submission, plus worked-time-vs-planned-route efficiency. Missing data is flagged in red.
          </p>
        </div>

        <div style={{ display: 'flex', gap: 12, marginBottom: 20, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            type="date"
            value={dateStr}
            onChange={e => setDateStr(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13 }}
          />
          <input
            placeholder="Search driver name..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13, width: 220 }}
          />
          <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13, color: '#94a3b8', cursor: 'pointer' }}>
            <input type="checkbox" checked={missingOnly} onChange={e => setMissingOnly(e.target.checked)} />
            Missing EOD only ({missingEod.length})
          </label>
        </div>

        {!loading && rows.length > 0 && (
          <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 16px', fontSize: 12 }}>
              <span style={{ color: '#94a3b8' }}>Acknowledged: </span>
              <span style={{ fontWeight: 700 }}>{counts.acknowledged}/{rows.length}</span>
            </div>
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 16px', fontSize: 12 }}>
              <span style={{ color: '#94a3b8' }}>Arrived: </span>
              <span style={{ fontWeight: 700 }}>{counts.arrived}/{rows.length}</span>
            </div>
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 16px', fontSize: 12 }}>
              <span style={{ color: '#94a3b8' }}>RTS: </span>
              <span style={{ fontWeight: 700 }}>{counts.rts}/{rows.length}</span>
            </div>
            <div style={{ background: counts.eod < rows.length ? '#3b1e1e' : '#1e293b', border: `1px solid ${counts.eod < rows.length ? '#7f1d1d' : '#334155'}`, borderRadius: 8, padding: '6px 16px', fontSize: 12 }}>
              <span style={{ color: '#94a3b8' }}>EOD: </span>
              <span style={{ fontWeight: 700 }}>{counts.eod}/{rows.length}</span>
              {missingEod.length > 0 && <span style={{ color: '#fca5a5', marginLeft: 8 }}>{missingEod.length} missing</span>}
            </div>
          </div>
        )}

        {error && <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>{error}</div>}
        {loading && <p style={{ color: '#60a5fa' }}>Loading...</p>}

        {!loading && !error && rows.length === 0 && (
          <div style={{ background: '#1e293b', borderRadius: 10, padding: 32, textAlign: 'center', color: '#94a3b8' }}>
            No drivers scheduled for {dateStr}.
          </div>
        )}

        {!loading && filtered.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#1a3c6e' }}>
                  <th style={{ padding: '8px 12px', textAlign: 'left', color: '#fff', fontWeight: 600 }}>Driver</th>
                  <th style={{ padding: '8px 12px', color: '#fff', fontWeight: 600 }}>Acknowledged</th>
                  <th style={{ padding: '8px 12px', color: '#fff', fontWeight: 600 }}>I&apos;ve Arrived</th>
                  <th style={{ padding: '8px 12px', color: '#fff', fontWeight: 600 }}>RTS Submission</th>
                  <th style={{ padding: '8px 12px', color: '#fff', fontWeight: 600 }}>EOD Submission</th>
                  <th style={{ padding: '8px 12px', color: '#fff', fontWeight: 600 }}>Efficiency %</th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((r, i) => (
                  <tr key={r.driver_name} style={{ background: i % 2 === 1 ? '#161b22' : '#0d1117' }}>
                    <td style={{ padding: '8px 12px' }}>{r.driver_name}</td>
                    <TimeCell time={r.acknowledged_at} />
                    <TimeCell time={r.arrived_at} />
                    <TimeCell time={r.rts_at} inProgress={r.rts_status === 'in_progress'} />
                    <TimeCell time={r.eod_at} />
                    <EfficiencyCell pct={r.efficiency_pct} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
