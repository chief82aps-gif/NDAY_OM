import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface WeekTrend {
  week: string;
  total_violations: number;
  unique_drivers: number;
  violations_per_driver: number | null;
  date_range_start: string | null;
  date_range_end: string | null;
  imported_at: string | null;
  source_file: string | null;
}

interface DayCount {
  date: string;
  count: number;
}

interface TrendResponse {
  weekly: WeekTrend[];
  latest_week_daily_breakdown: DayCount[];
  note: string;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

function fmtDate(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso + 'T00:00:00').toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}

function BarChart({
  data, valueKey, color, label,
}: {
  data: { label: string; value: number | null }[];
  valueKey: string;
  color: string;
  label: string;
}) {
  const max = Math.max(1, ...data.map(d => d.value ?? 0));
  return (
    <div>
      <div style={{ fontSize: 12, color: '#94a3b8', marginBottom: 10, textTransform: 'uppercase', letterSpacing: 0.5 }}>{label}</div>
      <div style={{ display: 'flex', alignItems: 'flex-end', gap: 10, height: 160 }}>
        {data.map((d, i) => {
          const v = d.value ?? 0;
          const pct = Math.max(2, (v / max) * 100);
          return (
            <div key={i} style={{ flex: 1, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 6 }}>
              <div style={{ fontSize: 12, color: '#e2e8f0', fontWeight: 700 }}>{d.value ?? '—'}</div>
              <div style={{ width: '100%', height: 120, display: 'flex', alignItems: 'flex-end' }}>
                <div style={{ width: '100%', height: `${pct}%`, background: color, borderRadius: '4px 4px 0 0', minHeight: 3 }} />
              </div>
              <div style={{ fontSize: 11, color: '#64748b' }}>{d.label}</div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export default function DvicTrendPage() {
  const [data, setData] = useState<TrendResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const api = resolveApi();

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${api}/dvic/trend`);
      if (!res.ok) { setError('Failed to load trend data.'); return; }
      setData(await res.json());
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const weekly = data?.weekly ?? [];
  const first = weekly[0];
  const last = weekly[weekly.length - 1];
  const pctChange = first && last && first.total_violations > 0
    ? Math.round(((last.total_violations - first.total_violations) / first.total_violations) * 100)
    : null;
  const trendGood = pctChange !== null && pctChange < 0;

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 6px' }}>📉 DVIC Pre-Trip Trend</h1>
          <p style={{ color: '#94a3b8', fontSize: 13, margin: '0 0 24px' }}>
            Under-90-second pre-trip inspections, week over week. Each week shown is its final rolling-7-day snapshot, not a sum of every daily re-upload.
          </p>

          {loading && <div style={{ color: '#60a5fa' }}>Loading...</div>}
          {error && <div style={{ color: '#f87171' }}>{error}</div>}

          {!loading && data && weekly.length === 0 && (
            <div style={{ background: '#1e293b', borderRadius: 10, padding: 32, textAlign: 'center', color: '#94a3b8' }}>
              No DVIC data ingested yet.
            </div>
          )}

          {!loading && weekly.length > 0 && (
            <>
              {/* Headline stat */}
              {pctChange !== null && (
                <div style={{
                  background: trendGood ? '#052e16' : '#450a0a',
                  border: `1px solid ${trendGood ? '#16a34a' : '#dc2626'}`,
                  borderRadius: 10, padding: '16px 20px', marginBottom: 24,
                }}>
                  <div style={{ fontSize: 28, fontWeight: 800, color: trendGood ? '#4ade80' : '#f87171' }}>
                    {pctChange > 0 ? '+' : ''}{pctChange}%
                  </div>
                  <div style={{ fontSize: 13, color: '#94a3b8' }}>
                    total under-90-second violations, {first?.week} → {last?.week} ({first?.total_violations} → {last?.total_violations})
                  </div>
                </div>
              )}

              {/* Week-over-week charts */}
              <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, marginBottom: 20 }}>
                <BarChart
                  label="Total violations per week"
                  valueKey="total_violations"
                  color="#f87171"
                  data={weekly.map(w => ({ label: w.week.replace('2026-W', 'W'), value: w.total_violations }))}
                />
              </div>
              <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, marginBottom: 20 }}>
                <BarChart
                  label="Violations per driver (repeat-offender rate)"
                  valueKey="violations_per_driver"
                  color="#fbbf24"
                  data={weekly.map(w => ({ label: w.week.replace('2026-W', 'W'), value: w.violations_per_driver }))}
                />
              </div>
              <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, marginBottom: 20 }}>
                <BarChart
                  label="Unique drivers flagged per week"
                  valueKey="unique_drivers"
                  color="#60a5fa"
                  data={weekly.map(w => ({ label: w.week.replace('2026-W', 'W'), value: w.unique_drivers }))}
                />
              </div>

              {/* Latest week daily breakdown */}
              {data && data.latest_week_daily_breakdown.length > 0 && (
                <div style={{ background: '#1e293b', borderRadius: 10, padding: 20, marginBottom: 20 }}>
                  <BarChart
                    label="Most recent snapshot — by day"
                    valueKey="count"
                    color="#a78bfa"
                    data={data.latest_week_daily_breakdown.map(d => ({ label: fmtDate(d.date), value: d.count }))}
                  />
                  <p style={{ fontSize: 11, color: '#64748b', marginTop: 12, marginBottom: 0 }}>
                    The last 1–2 days here are typically undercounted — Amazon's export usually hasn't fully synced those yet.
                  </p>
                </div>
              )}

              {/* Weekly table */}
              <div style={{ overflowX: 'auto' }}>
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                  <thead>
                    <tr style={{ background: '#1e40af' }}>
                      <th style={{ padding: '8px 12px', textAlign: 'left', color: '#fff' }}>Week</th>
                      <th style={{ padding: '8px 12px', color: '#fff' }}>Violations</th>
                      <th style={{ padding: '8px 12px', color: '#fff' }}>Unique Drivers</th>
                      <th style={{ padding: '8px 12px', color: '#fff' }}>Per Driver</th>
                      <th style={{ padding: '8px 12px', color: '#fff' }}>Imported</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...weekly].reverse().map((w, i) => (
                      <tr key={w.week} style={{ background: i % 2 === 0 ? '#1e293b' : '#0f172a' }}>
                        <td style={{ padding: '8px 12px' }}>{w.week}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'center', fontWeight: 700 }}>{w.total_violations}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'center' }}>{w.unique_drivers}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'center' }}>{w.violations_per_driver ?? '—'}</td>
                        <td style={{ padding: '8px 12px', textAlign: 'center', color: '#64748b', fontSize: 11 }}>{fmtDate((w.imported_at || '').slice(0, 10))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
