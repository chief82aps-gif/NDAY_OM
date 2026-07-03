import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface DriverRanking {
  rank: number;
  driver_name: string;
  transporter_id: string;
  overall_standing: string;
  overall_score: number | null;
  standing_rank: number;
  focus_areas: string[];
  metrics: {
    speeding_rate: number | null; speeding_score: number | null;
    seatbelt_rate: number | null; seatbelt_score: number | null;
    distraction_rate: number | null; distraction_score: number | null;
    sign_violation_rate: number | null; sign_violation_score: number | null;
    following_distance_rate: number | null; following_distance_score: number | null;
    cdf_dpmo: number | null; cdf_dpmo_score: number | null;
    dc_dpmo: number | null; dc_dpmo_score: number | null;
    dsb_count: number | null; dsb_score: number | null;
    pod_pct: number | null; pod_score: number | null;
    psb_rate: number | null; psb_score: number | null;
    packages_delivered: number | null;
  };
}

interface Snapshot {
  id: number;
  week: string;
  source_file: string;
  driver_count: number;
  imported_at: string;
}

interface RankingsResponse {
  week: string;
  driver_count: number;
  imported_at: string | null;
  rankings: DriverRanking[];
  message?: string;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const STANDING_COLOR: Record<string, string> = {
  Platinum: '#60a5fa',
  Gold:     '#f59e0b',
  Silver:   '#94a3b8',
  Bronze:   '#b45309',
};

const STANDING_BG: Record<string, string> = {
  Platinum: '#1e3a5f',
  Gold:     '#3d2e00',
  Silver:   '#1e293b',
  Bronze:   '#2c1a00',
};

function standingBadge(s: string) {
  const color = STANDING_COLOR[s] || '#888';
  const bg    = STANDING_BG[s]    || '#222';
  return (
    <span style={{ background: bg, color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>
      {s}
    </span>
  );
}

function scoreBar(score: number | null) {
  if (score == null) return <span style={{ color: '#555' }}>—</span>;
  const pct = Math.min(100, Math.max(0, score));
  const color = pct >= 90 ? '#22c55e' : pct >= 70 ? '#f59e0b' : '#ef4444';
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
      <span style={{ display: 'inline-block', width: 60, height: 6, background: '#333', borderRadius: 3, overflow: 'hidden' }}>
        <span style={{ display: 'block', width: `${pct}%`, height: '100%', background: color }} />
      </span>
      <span style={{ color, fontWeight: 600, fontSize: 12 }}>{score.toFixed(1)}</span>
    </span>
  );
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const METRIC_ROWS: { label: string; rate: keyof DriverRanking['metrics']; score: keyof DriverRanking['metrics'] }[] = [
  { label: 'Speeding Event Rate',        rate: 'speeding_rate',           score: 'speeding_score' },
  { label: 'Seatbelt-Off Rate',          rate: 'seatbelt_rate',           score: 'seatbelt_score' },
  { label: 'Distractions Rate',          rate: 'distraction_rate',        score: 'distraction_score' },
  { label: 'Sign/Signal Violations',     rate: 'sign_violation_rate',     score: 'sign_violation_score' },
  { label: 'Following Distance Rate',    rate: 'following_distance_rate', score: 'following_distance_score' },
  { label: 'CDF DPMO',                   rate: 'cdf_dpmo',                score: 'cdf_dpmo_score' },
  { label: 'Delivery Completion DPMO',   rate: 'dc_dpmo',                 score: 'dc_dpmo_score' },
  { label: 'Delivery Success Behaviors', rate: 'dsb_count',               score: 'dsb_score' },
  { label: 'Photo on Delivery (POD)',    rate: 'pod_pct',                 score: 'pod_score' },
  { label: 'Pickup Success Behaviors',   rate: 'psb_rate',                score: 'psb_score' },
];

function DriverRow({ d, idx }: { d: DriverRanking; idx: number }) {
  const [open, setOpen] = useState(false);
  const rowBg = idx % 2 === 0 ? '#161b22' : '#0d1117';

  return (
    <>
      <tr
        style={{ background: rowBg, cursor: 'pointer' }}
        onClick={() => setOpen(o => !o)}
      >
        <td style={td}>{d.rank}</td>
        <td style={td}>{d.driver_name}</td>
        <td style={td}>{standingBadge(d.overall_standing)}</td>
        <td style={td}>{scoreBar(d.overall_score)}</td>
        <td style={{ ...td, fontSize: 11, color: '#94a3b8' }}>
          {d.focus_areas.length > 0 ? d.focus_areas.slice(0, 2).join(', ') : '—'}
        </td>
        <td style={{ ...td, color: '#555', fontSize: 13 }}>{open ? '▲' : '▼'}</td>
      </tr>
      {open && (
        <tr style={{ background: '#0a0f17' }}>
          <td colSpan={6} style={{ padding: '12px 24px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ ...th, textAlign: 'left' }}>Metric</th>
                  <th style={th}>Rate / Count</th>
                  <th style={th}>Score</th>
                </tr>
              </thead>
              <tbody>
                {METRIC_ROWS.map(row => (
                  <tr key={row.label} style={{ borderBottom: '1px solid #1e293b' }}>
                    <td style={{ ...td, color: '#ccc' }}>{row.label}</td>
                    <td style={{ ...td, textAlign: 'center', color: '#888' }}>
                      {d.metrics[row.rate] != null ? String(d.metrics[row.rate]) : '—'}
                    </td>
                    <td style={{ ...td, textAlign: 'center' }}>{scoreBar(d.metrics[row.score] as number | null)}</td>
                  </tr>
                ))}
                <tr>
                  <td style={{ ...td, color: '#888' }} colSpan={2}>Packages Delivered</td>
                  <td style={{ ...td, color: '#60a5fa', fontWeight: 600 }}>
                    {d.metrics.packages_delivered != null ? d.metrics.packages_delivered.toLocaleString() : '—'}
                  </td>
                </tr>
                <tr>
                  <td style={{ ...td, color: '#888' }} colSpan={2}>Transporter ID</td>
                  <td style={{ ...td, color: '#555', fontSize: 11 }}>{d.transporter_id}</td>
                </tr>
              </tbody>
            </table>
          </td>
        </tr>
      )}
    </>
  );
}

const th: React.CSSProperties = { padding: '6px 12px', textAlign: 'center', color: '#94a3b8', fontWeight: 600, fontSize: 11 };
const td: React.CSSProperties = { padding: '8px 12px', verticalAlign: 'middle' };

export default function DriverQualityPage() {
  const [snapshots, setSnapshots] = useState<Snapshot[]>([]);
  const [selectedWeek, setSelectedWeek] = useState<string>('');
  const [data, setData] = useState<RankingsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [standingFilter, setStandingFilter] = useState<string>('all');
  const [search, setSearch] = useState('');

  const api = resolveApi();

  const loadSnapshots = useCallback(async () => {
    try {
      const r = await fetch(`${api}/quality/snapshots`);
      const snaps: Snapshot[] = await r.json();
      setSnapshots(snaps);
      if (snaps.length > 0 && !selectedWeek) setSelectedWeek(snaps[0].week);
    } catch {}
  }, [api, selectedWeek]);

  const loadRankings = useCallback(async (week: string) => {
    if (!week) return;
    setLoading(true);
    try {
      const r = await fetch(`${api}/quality/rankings?week=${encodeURIComponent(week)}`);
      setData(await r.json());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { loadSnapshots(); }, [loadSnapshots]);
  useEffect(() => { if (selectedWeek) loadRankings(selectedWeek); }, [selectedWeek, loadRankings]);

  const filtered = (data?.rankings ?? []).filter(d => {
    if (standingFilter !== 'all' && d.overall_standing !== standingFilter) return false;
    if (search && !d.driver_name.toLowerCase().includes(search.toLowerCase())) return false;
    return true;
  });

  const standing_counts = (data?.rankings ?? []).reduce<Record<string, number>>((acc, d) => {
    acc[d.overall_standing] = (acc[d.overall_standing] || 0) + 1;
    return acc;
  }, {});

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0d1117', color: '#e2e8f0', fontFamily: 'sans-serif', padding: '24px 32px' }}>
        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>Driver Quality Rankings</h1>
          <p style={{ color: '#94a3b8', marginTop: 4, fontSize: 13 }}>
            Six-week trailing safety &amp; quality metrics. Used for roster priority ordering.
          </p>
        </div>

        {/* Controls */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          <select
            value={selectedWeek}
            onChange={e => setSelectedWeek(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13 }}
          >
            {snapshots.map(s => (
              <option key={s.id} value={s.week}>{s.week} — {s.driver_count} drivers ({fmt(s.imported_at)})</option>
            ))}
          </select>
          <input
            placeholder="Search driver name..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13, width: 200 }}
          />
          <select
            value={standingFilter}
            onChange={e => setStandingFilter(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13 }}
          >
            <option value="all">All Standings</option>
            <option value="Platinum">Platinum</option>
            <option value="Gold">Gold</option>
            <option value="Silver">Silver</option>
            <option value="Bronze">Bronze</option>
          </select>
        </div>

        {/* Standing summary chips */}
        {data && (
          <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
            {(['Platinum', 'Gold', 'Silver', 'Bronze'] as const).map(s => (
              <div
                key={s}
                style={{ background: STANDING_BG[s], border: `1px solid ${STANDING_COLOR[s]}`, borderRadius: 8, padding: '6px 16px', cursor: 'pointer', opacity: standingFilter !== 'all' && standingFilter !== s ? 0.4 : 1 }}
                onClick={() => setStandingFilter(prev => prev === s ? 'all' : s)}
              >
                <span style={{ color: STANDING_COLOR[s], fontWeight: 700, fontSize: 13 }}>{s}</span>
                <span style={{ color: '#94a3b8', fontSize: 12, marginLeft: 6 }}>{standing_counts[s] ?? 0}</span>
              </div>
            ))}
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 16px' }}>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>Total: </span>
              <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 13 }}>{data.rankings.length}</span>
            </div>
          </div>
        )}

        {/* No data */}
        {!loading && snapshots.length === 0 && (
          <div style={{ background: '#1e293b', borderRadius: 10, padding: 32, textAlign: 'center', color: '#94a3b8' }}>
            No quality CSV data ingested yet. Drop the DSP Overview Dashboard CSV in #nday-operations-management.
          </div>
        )}

        {loading && (
          <div style={{ textAlign: 'center', padding: 40, color: '#60a5fa' }}>Loading rankings...</div>
        )}

        {/* Rankings table */}
        {!loading && data && filtered.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#1a3c6e' }}>
                  <th style={{ ...th, color: '#fff' }}>#</th>
                  <th style={{ ...th, color: '#fff', textAlign: 'left' }}>Driver</th>
                  <th style={{ ...th, color: '#fff' }}>Standing</th>
                  <th style={{ ...th, color: '#fff' }}>Overall Score</th>
                  <th style={{ ...th, color: '#fff', textAlign: 'left' }}>Focus Areas</th>
                  <th style={{ ...th, color: '#fff' }}></th>
                </tr>
              </thead>
              <tbody>
                {filtered.map((d, i) => <DriverRow key={d.transporter_id} d={d} idx={i} />)}
              </tbody>
            </table>
          </div>
        )}

        {!loading && data && filtered.length === 0 && data.rankings.length > 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: '#94a3b8' }}>No drivers match the current filter.</div>
        )}
      </div>
    </ProtectedRoute>
  );
}
