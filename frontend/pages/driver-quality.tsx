import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';

interface DriverScore {
  driver_name: string;
  transporter_id: string | null;
  overall: number | null;
  safety: number | null;
  quality: number | null;
  attendance: number;
  overall_tier: string;
  safety_tier: string;
  quality_tier: string;
  attendance_tier: string;
  ranking_eligible: boolean;
  high_performer_eligible: boolean;
  tenure_status: string;
  trailing_routes: number;
  lifetime_routes: number | null;
}

interface ScoresResponse {
  drivers: DriverScore[];
}

interface LedgerSummaryRow {
  driver: string;
  banked_amount: number;
  banked_packages: number;
}

interface MetricHighlight {
  label: string;
  score: number;
  video_url?: string | null;
}

interface DriverBreakdown {
  strengths: MetricHighlight[];
  focus_areas: MetricHighlight[];
}

interface TrainingVideoEntry {
  metric_label: string;
  video_url: string | null;
  updated_by: string | null;
  updated_at: string | null;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

// Collapsed to 5 tiers 2026-08-04 -- Tin/Lead/Sawdust were already
// indistinguishable in every display (all three showed "Does Not Meet
// Minimum"); driver_scoring.py now only ever produces "does_not_meet"
// as the one real tier below Bronze.
const TIER_LABEL: Record<string, string> = {
  platinum:      'Platinum',
  gold:          'Gold',
  silver:        'Silver',
  bronze:        'Bronze',
  does_not_meet: 'Does Not Meet Minimum',
  gray:          'No Data',
};

const TIER_COLOR: Record<string, string> = {
  platinum:      '#60a5fa',
  gold:          '#f59e0b',
  silver:        '#94a3b8',
  bronze:        '#b45309',
  does_not_meet: '#9ca3af',
  gray:          '#555',
};

const TIER_BG: Record<string, string> = {
  platinum:      '#1e3a5f',
  gold:          '#3d2e00',
  silver:        '#1e293b',
  bronze:        '#2c1a00',
  does_not_meet: '#26282c',
  gray:          '#1a1a1a',
};

const TIER_ICON: Record<string, string> = {
  platinum:      '💎',
  gold:          '⭐',
  silver:        '✨',
  bronze:        '🔶',
  does_not_meet: '🥫',
  gray:          '⚪',
};

const RANK_MEDAL: Record<number, string> = { 1: '🥇', 2: '🥈', 3: '🥉' };

function tierBadge(t: string) {
  const color = TIER_COLOR[t] || '#888';
  const bg    = TIER_BG[t]    || '#222';
  const label = TIER_LABEL[t] || t;
  return (
    <span style={{ background: bg, color, border: `1px solid ${color}`, borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 700 }}>
      {TIER_ICON[t] ? `${TIER_ICON[t]} ` : ''}{label}
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

function weakestCategory(d: DriverScore): string | null {
  const cats: [string, number | null][] = [
    ['Safety', d.safety],
    ['Quality', d.quality],
    ['Reliability', d.attendance],
  ];
  const scored = cats.filter((c): c is [string, number] => c[1] != null);
  if (scored.length === 0) return null;
  scored.sort((a, b) => a[1] - b[1]);
  return scored[0][0];
}

const th: React.CSSProperties = { padding: '6px 12px', textAlign: 'center', color: '#94a3b8', fontWeight: 600, fontSize: 11 };
const td: React.CSSProperties = { padding: '8px 12px', verticalAlign: 'middle' };

function DriverRow({ d, rank, banked, api }: { d: DriverScore; rank: number; banked: number | null; api: string }) {
  const [open, setOpen] = useState(false);
  const [breakdown, setBreakdown] = useState<DriverBreakdown | null>(null);
  const [breakdownLoading, setBreakdownLoading] = useState(false);
  const rowBg = rank % 2 === 1 ? '#161b22' : '#0d1117';
  const focus = weakestCategory(d);

  const handleToggle = () => {
    setOpen(o => !o);
    if (!breakdown && !breakdownLoading) {
      setBreakdownLoading(true);
      fetch(`${api}/driver-scoring/breakdown/${encodeURIComponent(d.driver_name)}`)
        .then(r => r.json())
        .then(b => setBreakdown({ strengths: b.strengths ?? [], focus_areas: b.focus_areas ?? [] }))
        .catch(() => setBreakdown({ strengths: [], focus_areas: [] }))
        .finally(() => setBreakdownLoading(false));
    }
  };

  return (
    <>
      <tr style={{ background: rowBg, cursor: 'pointer' }} onClick={handleToggle}>
        <td style={td}>{RANK_MEDAL[rank] ? `${RANK_MEDAL[rank]} ${rank}` : rank}</td>
        <td style={td}>
          {d.driver_name}
          {d.high_performer_eligible && <span title="High performer" style={{ marginLeft: 6 }}>🔥</span>}
          {!d.ranking_eligible && <span style={{ marginLeft: 6, fontSize: 10, color: '#64748b' }}>(not yet ranking-eligible)</span>}
        </td>
        <td style={td}>{tierBadge(d.overall_tier)}</td>
        <td style={td}>{scoreBar(d.overall)}</td>
        <td style={{ ...td, textAlign: 'right', color: '#e2e8f0', fontWeight: 600 }}>{d.lifetime_routes ?? '—'}</td>
        <td style={{ ...td, fontSize: 11, color: '#94a3b8' }}>{focus ? `Focus: ${focus}` : '—'}</td>
        <td style={{ ...td, textAlign: 'right', color: banked ? '#22c55e' : '#555', fontWeight: banked ? 700 : 400 }}>
          {banked ? `$${banked}` : '—'}
        </td>
        <td style={{ ...td, color: '#555', fontSize: 13 }}>{open ? '▲' : '▼'}</td>
      </tr>
      {open && (
        <tr style={{ background: '#0a0f17' }}>
          <td colSpan={8} style={{ padding: '12px 24px' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
              <thead>
                <tr>
                  <th style={{ ...th, textAlign: 'left' }}>Category</th>
                  <th style={th}>Weight</th>
                  <th style={th}>Score</th>
                  <th style={th}>Tier</th>
                </tr>
              </thead>
              <tbody>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ ...td, color: '#ccc' }}>Safety</td>
                  <td style={{ ...td, textAlign: 'center', color: '#888' }}>40%</td>
                  <td style={{ ...td, textAlign: 'center' }}>{scoreBar(d.safety)}</td>
                  <td style={{ ...td, textAlign: 'center' }}>{tierBadge(d.safety_tier)}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ ...td, color: '#ccc' }}>Quality</td>
                  <td style={{ ...td, textAlign: 'center', color: '#888' }}>40%</td>
                  <td style={{ ...td, textAlign: 'center' }}>{scoreBar(d.quality)}</td>
                  <td style={{ ...td, textAlign: 'center' }}>{tierBadge(d.quality_tier)}</td>
                </tr>
                <tr style={{ borderBottom: '1px solid #1e293b' }}>
                  <td style={{ ...td, color: '#ccc' }}>Reliability</td>
                  <td style={{ ...td, textAlign: 'center', color: '#888' }}>20%</td>
                  <td style={{ ...td, textAlign: 'center' }}>{scoreBar(d.attendance)}</td>
                  <td style={{ ...td, textAlign: 'center' }}>{tierBadge(d.attendance_tier)}</td>
                </tr>
                <tr>
                  <td style={{ ...td, color: '#888' }} colSpan={2}>Tenure Status</td>
                  <td style={{ ...td, textAlign: 'center' }} colSpan={2}>{d.tenure_status}</td>
                </tr>
                <tr>
                  <td style={{ ...td, color: '#888' }} colSpan={2}>Trailing 6-Week Routes</td>
                  <td style={{ ...td, textAlign: 'center', color: '#60a5fa', fontWeight: 600 }} colSpan={2}>{d.trailing_routes}</td>
                </tr>
                {d.transporter_id && (
                  <tr>
                    <td style={{ ...td, color: '#888' }} colSpan={2}>Transporter ID</td>
                    <td style={{ ...td, color: '#555', fontSize: 11 }} colSpan={2}>{d.transporter_id}</td>
                  </tr>
                )}
              </tbody>
            </table>

            {breakdownLoading && <p style={{ color: '#64748b', fontSize: 12, marginTop: 12 }}>Loading exact metrics…</p>}

            {breakdown && (breakdown.focus_areas.length > 0 || breakdown.strengths.length > 0) && (
              <div style={{ marginTop: 14, display: 'flex', gap: 24, flexWrap: 'wrap' }}>
                {breakdown.focus_areas.length > 0 && (
                  <div>
                    <p style={{ color: '#f59e0b', fontSize: 12, fontWeight: 700, margin: '0 0 6px' }}>🎯 Exact issue(s) driving the score</p>
                    {breakdown.focus_areas.map(f => (
                      <div key={f.label} style={{ fontSize: 12, color: '#e2e8f0', marginBottom: 4 }}>
                        <span>{f.label} — {f.score.toFixed(1)}</span>
                        {f.video_url && (
                          <a href={f.video_url} target="_blank" rel="noreferrer" style={{ marginLeft: 8, color: '#60a5fa' }}>
                            📺 Training video
                          </a>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {breakdown.strengths.length > 0 && (
                  <div>
                    <p style={{ color: '#22c55e', fontSize: 12, fontWeight: 700, margin: '0 0 6px' }}>🌟 Doing great on</p>
                    {breakdown.strengths.map(s => (
                      <div key={s.label} style={{ fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>{s.label} — {s.score.toFixed(1)}</div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </td>
        </tr>
      )}
    </>
  );
}

type SortKey = 'overall' | 'lifetime_routes';

function TrainingVideoManager({ api }: { api: string }) {
  const [videos, setVideos] = useState<TrainingVideoEntry[]>([]);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [saving, setSaving] = useState<string | null>(null);
  const [videoError, setVideoError] = useState('');

  const load = useCallback(() => {
    fetch(`${api}/driver-scoring/training-videos`)
      .then(r => r.json())
      .then(d => {
        setVideos(d.videos ?? []);
        const seeded: Record<string, string> = {};
        for (const v of d.videos ?? []) seeded[v.metric_label] = v.video_url ?? '';
        setDrafts(seeded);
      })
      .catch(() => {});
  }, [api]);

  useEffect(() => { load(); }, [load]);

  async function save(metric_label: string) {
    setSaving(metric_label);
    try {
      // Both endpoints are behind require_any_role("owner","hr"). Without the
      // token they return 401 and, because nothing checked res.ok, the button
      // appeared to work while saving nothing — the same silent-401 failure as
      // survey-admin (2026-08-07). Found by the frontend/backend auth scrub.
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const authHeaders: Record<string, string> = token ? { Authorization: `Bearer ${token}` } : {};

      const url = (drafts[metric_label] || '').trim();
      const res = !url
        ? await fetch(`${api}/driver-scoring/training-videos/${encodeURIComponent(metric_label)}`, {
            method: 'DELETE', headers: authHeaders,
          })
        : await fetch(`${api}/driver-scoring/training-videos`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json', ...authHeaders },
            body: JSON.stringify({ metric_label, video_url: url }),
          });

      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        const detail = typeof body.detail === 'string' ? body.detail : '';
        setVideoError(
          res.status === 401 ? 'Not signed in — your session expired. Sign in again and retry.'
          : res.status === 403 ? `Your account lacks permission (403). ${detail}`
          : `Save failed — HTTP ${res.status}${detail ? ': ' + detail : ''}`
        );
        return;
      }
      setVideoError('');
      load();
    } catch (e: any) {
      setVideoError(`Network error: ${e?.message ?? 'request never reached the server'}`);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 10, padding: 16, marginBottom: 20 }}>
      <p style={{ fontSize: 13, fontWeight: 700, margin: '0 0 10px' }}>📺 Training Video Library</p>
      {videoError && (
        <div style={{ marginBottom: 10, padding: '8px 12px', borderRadius: 6, fontSize: 13,
                      background: '#3b1e1e', border: '1px solid #7f1d1d', color: '#f87171' }}>
          {videoError}
        </div>
      )}
      <p style={{ fontSize: 11, color: '#94a3b8', margin: '0 0 12px' }}>
        One video per metric — auto-attaches whenever that metric shows up as a driver's focus area.
      </p>
      {videos.map(v => (
        <div key={v.metric_label} style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 6 }}>
          <span style={{ fontSize: 12, color: '#e2e8f0', width: 240, flexShrink: 0 }}>{v.metric_label}</span>
          <input
            value={drafts[v.metric_label] ?? ''}
            onChange={e => setDrafts(d => ({ ...d, [v.metric_label]: e.target.value }))}
            placeholder="https://..."
            style={{ flex: 1, background: '#0d1117', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '4px 10px', fontSize: 12 }}
          />
          <button
            onClick={() => save(v.metric_label)}
            disabled={saving === v.metric_label}
            style={{ background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
          >
            {saving === v.metric_label ? 'Saving…' : 'Save'}
          </button>
        </div>
      ))}
    </div>
  );
}

export default function DriverQualityPage() {
  const { user } = useAuth();
  const canManageVideos = user?.role === 'owner' || user?.role === 'hr' || user?.role === 'super_user';
  const [showVideoManager, setShowVideoManager] = useState(false);
  const [data, setData] = useState<ScoresResponse | null>(null);
  const [ledger, setLedger] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(false);
  const [tierFilter, setTierFilter] = useState<string>('all');
  const [search, setSearch] = useState('');
  const [minRoutes, setMinRoutes] = useState('');
  const [sortKey, setSortKey] = useState<SortKey>('overall');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('desc');

  const toggleSort = (key: SortKey) => {
    if (sortKey === key) {
      setSortDir(d => (d === 'desc' ? 'asc' : 'desc'));
    } else {
      setSortKey(key);
      setSortDir('desc');
    }
  };

  const api = resolveApi();

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await fetch(`${api}/driver-scoring/scores`);
      setData(await r.json());
    } catch {
      setData(null);
    } finally {
      setLoading(false);
    }
  }, [api]);

  const loadLedger = useCallback(async () => {
    try {
      const r = await fetch(`${api}/rescue/bonus/ledger-summary`);
      const rows: LedgerSummaryRow[] = await r.json();
      const map: Record<string, number> = {};
      for (const row of rows) if (row.banked_amount > 0) map[row.driver] = row.banked_amount;
      setLedger(map);
    } catch {}
  }, [api]);

  useEffect(() => { load(); loadLedger(); }, [load, loadLedger]);

  const filtered = (data?.drivers ?? []).filter(d => {
    if (tierFilter !== 'all' && d.overall_tier !== tierFilter) return false;
    if (search && !d.driver_name.toLowerCase().includes(search.toLowerCase())) return false;
    if (minRoutes && (d.lifetime_routes ?? 0) < parseInt(minRoutes, 10)) return false;
    return true;
  });

  const sorted = [...filtered].sort((a, b) => {
    const av = sortKey === 'overall' ? (a.overall ?? -1) : (a.lifetime_routes ?? -1);
    const bv = sortKey === 'overall' ? (b.overall ?? -1) : (b.lifetime_routes ?? -1);
    return sortDir === 'desc' ? bv - av : av - bv;
  });

  const sortArrow = (key: SortKey) => (sortKey === key ? (sortDir === 'desc' ? ' ▼' : ' ▲') : '');

  const tier_counts = (data?.drivers ?? []).reduce<Record<string, number>>((acc, d) => {
    acc[d.overall_tier] = (acc[d.overall_tier] || 0) + 1;
    return acc;
  }, {});

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0d1117', color: '#e2e8f0', fontFamily: 'sans-serif', padding: '24px 32px' }}>
        {/* Header */}
        <div style={{ marginBottom: 24 }}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: 0 }}>🏆 Mentoring Dashboard</h1>
          <p style={{ color: '#94a3b8', marginTop: 4, fontSize: 13 }}>
            Every driver ranked by their personal performance score (Safety 40% · Quality 40% · Reliability 20%). Tap a row for the full breakdown.
          </p>
          {canManageVideos && (
            <button
              onClick={() => setShowVideoManager(v => !v)}
              style={{ marginTop: 8, background: 'transparent', color: '#60a5fa', border: '1px solid #334155', borderRadius: 6, padding: '4px 12px', fontSize: 12, cursor: 'pointer' }}
            >
              {showVideoManager ? 'Hide' : 'Manage'} Training Videos
            </button>
          )}
        </div>

        {canManageVideos && showVideoManager && <TrainingVideoManager api={resolveApi()} />}

        {/* Controls */}
        <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 20 }}>
          <input
            placeholder="Search driver name..."
            value={search}
            onChange={e => setSearch(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13, width: 200 }}
          />
          <select
            value={tierFilter}
            onChange={e => setTierFilter(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13 }}
          >
            <option value="all">All Tiers</option>
            <option value="platinum">Platinum</option>
            <option value="gold">Gold</option>
            <option value="silver">Silver</option>
            <option value="bronze">Bronze</option>
            <option value="does_not_meet">Does Not Meet Minimum</option>
          </select>
          <input
            type="number"
            min="0"
            placeholder="Min routes (e.g. 100)"
            value={minRoutes}
            onChange={e => setMinRoutes(e.target.value)}
            style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 12px', fontSize: 13, width: 160 }}
          />
        </div>

        {/* Tier summary chips */}
        {data && (
          <div style={{ display: 'flex', gap: 10, marginBottom: 20, flexWrap: 'wrap' }}>
            {(['platinum', 'gold', 'silver', 'bronze', 'does_not_meet'] as const).map(t => (
              <div
                key={t}
                style={{ background: TIER_BG[t], border: `1px solid ${TIER_COLOR[t]}`, borderRadius: 8, padding: '6px 16px', cursor: 'pointer', opacity: tierFilter !== 'all' && tierFilter !== t ? 0.4 : 1 }}
                onClick={() => setTierFilter(prev => prev === t ? 'all' : t)}
              >
                <span style={{ color: TIER_COLOR[t], fontWeight: 700, fontSize: 13 }}>{TIER_LABEL[t]}</span>
                <span style={{ color: '#94a3b8', fontSize: 12, marginLeft: 6 }}>{tier_counts[t] ?? 0}</span>
              </div>
            ))}
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '6px 16px' }}>
              <span style={{ color: '#94a3b8', fontSize: 12 }}>Total: </span>
              <span style={{ color: '#e2e8f0', fontWeight: 700, fontSize: 13 }}>{data.drivers.length}</span>
            </div>
          </div>
        )}

        {/* No data */}
        {!loading && data && data.drivers.length === 0 && (
          <div style={{ background: '#1e293b', borderRadius: 10, padding: 32, textAlign: 'center', color: '#94a3b8' }}>
            No quality CSV data ingested yet. Drop the DSP Overview Dashboard CSV in #nday-operations-management.
          </div>
        )}

        {loading && (
          <div style={{ textAlign: 'center', padding: 40, color: '#60a5fa' }}>Loading rankings...</div>
        )}

        {/* Rankings table */}
        {!loading && data && sorted.length > 0 && (
          <div style={{ overflowX: 'auto' }}>
            <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
              <thead>
                <tr style={{ background: '#1a3c6e' }}>
                  <th style={{ ...th, color: '#fff' }}>#</th>
                  <th style={{ ...th, color: '#fff', textAlign: 'left' }}>Driver</th>
                  <th style={{ ...th, color: '#fff' }}>Tier</th>
                  <th style={{ ...th, color: '#fff', cursor: 'pointer' }} onClick={() => toggleSort('overall')}>
                    Overall Score{sortArrow('overall')}
                  </th>
                  <th style={{ ...th, color: '#fff', cursor: 'pointer' }} onClick={() => toggleSort('lifetime_routes')}>
                    Routes{sortArrow('lifetime_routes')}
                  </th>
                  <th style={{ ...th, color: '#fff', textAlign: 'left' }}>Focus Area</th>
                  <th style={{ ...th, color: '#fff' }}>Banked Bonus</th>
                  <th style={{ ...th, color: '#fff' }}></th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((d, i) => (
                  <DriverRow key={d.transporter_id || d.driver_name} d={d} rank={i + 1} banked={ledger[d.driver_name] ?? null} api={api} />
                ))}
              </tbody>
            </table>
          </div>
        )}

        {!loading && data && sorted.length === 0 && data.drivers.length > 0 && (
          <div style={{ textAlign: 'center', padding: 32, color: '#94a3b8' }}>No drivers match the current filter.</div>
        )}
      </div>
    </ProtectedRoute>
  );
}
