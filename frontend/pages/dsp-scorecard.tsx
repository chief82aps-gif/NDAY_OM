import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface Metric {
  slug: string;
  label: string;
  category: string;
  value_numeric: number | null;
  standing: string | null;
  weight_pct: number | null;
  is_disputable: boolean;
  dispute_note: string | null;
}

interface WeekDetail {
  week: string;
  source_file: string | null;
  imported_at: string | null;
  overall_score: number | null;
  overall_standing: string | null;
  safety_standing: string | null;
  delivery_quality_standing: string | null;
  pickup_quality_standing: string | null;
  team_fleet_standing: string | null;
  focus_areas: string[];
  dc_adjustment_note: string | null;
  slack_posted: boolean;
  metrics: Metric[];
}

interface WeekMeta {
  week: string;
  overall_score: number | null;
  overall_standing: string | null;
  imported_at: string | null;
  slack_posted: boolean;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const STANDING_RANK: Record<string, number> = {
  Poor: 0, Fair: 1, Great: 2, Fantastic: 3, 'Fantastic Plus': 4,
};

function standingColor(s: string | null): string {
  if (!s) return '#64748b';
  const r = STANDING_RANK[s] ?? -1;
  if (r >= 3) return '#4ade80';
  if (r === 2) return '#facc15';
  if (r === 1) return '#f97316';
  return '#ef4444';
}

function standingEmoji(s: string | null): string {
  if (!s) return '⬜';
  const r = STANDING_RANK[s] ?? -1;
  if (r >= 4) return '🌟';
  if (r === 3) return '✅';
  if (r === 2) return '🟡';
  if (r === 1) return '🟠';
  return '🔴';
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

const CATEGORIES = [
  { key: 'safety', label: 'Safety & Compliance' },
  { key: 'delivery_quality', label: 'Delivery Quality' },
  { key: 'pickup_quality', label: 'Pickup Quality' },
  { key: 'team_fleet', label: 'Team & Fleet' },
];

const CAT_STANDING: Record<string, keyof WeekDetail> = {
  safety: 'safety_standing',
  delivery_quality: 'delivery_quality_standing',
  pickup_quality: 'pickup_quality_standing',
  team_fleet: 'team_fleet_standing',
};

export default function DspScorecardPage() {
  const [weeks, setWeeks] = useState<WeekMeta[]>([]);
  const [selectedWeek, setSelectedWeek] = useState('');
  const [detail, setDetail] = useState<WeekDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [reposting, setReposting] = useState(false);
  const [tab, setTab] = useState<'overview' | 'metrics' | 'disputes'>('overview');
  const api = resolveApi();

  const loadWeeks = useCallback(async () => {
    const res = await fetch(`${api}/dsp-scorecard-weekly/weeks`);
    const data: WeekMeta[] = await res.json();
    setWeeks(data);
    if (data.length > 0 && !selectedWeek) setSelectedWeek(data[0].week);
  }, [api, selectedWeek]);

  const loadDetail = useCallback(async (week: string) => {
    setLoading(true);
    setDetail(null);
    try {
      const res = await fetch(`${api}/dsp-scorecard-weekly/week/${encodeURIComponent(week)}`);
      if (res.ok) setDetail(await res.json());
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { loadWeeks(); }, [loadWeeks]);
  useEffect(() => { if (selectedWeek) loadDetail(selectedWeek); }, [selectedWeek, loadDetail]);

  const repostSlack = async () => {
    setReposting(true);
    try {
      await fetch(`${api}/dsp-scorecard-weekly/week/${encodeURIComponent(selectedWeek)}/repost-slack`, { method: 'POST' });
      await loadWeeks();
      alert('Summary re-posted to #nday-mgt');
    } finally {
      setReposting(false);
    }
  };

  const disputeMetrics = detail?.metrics.filter(m => m.is_disputable && m.dispute_note) ?? [];
  const belowFantastic = detail?.metrics.filter(m => (STANDING_RANK[m.standing ?? ''] ?? 0) < STANDING_RANK['Fantastic']) ?? [];

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>

          {/* Header */}
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>
              DSP Scorecard — Weekly
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
              Amazon Delivery Excellence scorecard ingested from #nday-operations-management
            </p>
          </div>

          {/* Week selector */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 24, flexWrap: 'wrap' }}>
            <select
              value={selectedWeek}
              onChange={e => setSelectedWeek(e.target.value)}
              style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14 }}
            >
              {weeks.length === 0 && <option value="">No scorecards yet — drop PDF in #nday-ops-management</option>}
              {weeks.map(w => (
                <option key={w.week} value={w.week}>
                  {w.week} · {w.overall_standing ?? '?'} {w.overall_score != null ? `(${w.overall_score})` : ''}
                </option>
              ))}
            </select>

            {detail && (
              <button
                onClick={repostSlack}
                disabled={reposting}
                style={{
                  background: reposting ? '#1e293b' : '#0f4c81',
                  color: '#fff', border: 'none', borderRadius: 8,
                  padding: '8px 18px', cursor: reposting ? 'default' : 'pointer',
                  fontWeight: 600, fontSize: 14,
                }}
              >
                {reposting ? 'Posting…' : 'Re-post to #nday-mgt'}
              </button>
            )}
          </div>

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {detail && !loading && (
            <>
              {/* Overall score hero */}
              <div style={{
                background: '#1e293b', borderRadius: 12, padding: '20px 24px',
                marginBottom: 20, display: 'flex', gap: 32, alignItems: 'center', flexWrap: 'wrap',
                border: `1px solid ${standingColor(detail.overall_standing)}44`,
              }}>
                <div>
                  <div style={{ fontSize: 13, color: '#64748b', marginBottom: 4 }}>Overall Score</div>
                  <div style={{ fontSize: 44, fontWeight: 800, color: standingColor(detail.overall_standing), lineHeight: 1 }}>
                    {detail.overall_score != null ? detail.overall_score.toFixed(1) : '—'}
                  </div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: standingColor(detail.overall_standing), marginTop: 4 }}>
                    {standingEmoji(detail.overall_standing)} {detail.overall_standing}
                  </div>
                </div>
                <div style={{ flex: 1, display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 12 }}>
                  {CATEGORIES.map(cat => {
                    const standing = detail[CAT_STANDING[cat.key]] as string | null;
                    return (
                      <div key={cat.key} style={{ background: '#0f172a', borderRadius: 8, padding: '10px 14px' }}>
                        <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>{cat.label}</div>
                        <div style={{ fontWeight: 700, color: standingColor(standing), fontSize: 15 }}>
                          {standingEmoji(standing)} {standing ?? '—'}
                        </div>
                      </div>
                    );
                  })}
                </div>
                <div style={{ fontSize: 12, color: '#64748b', alignSelf: 'flex-start' }}>
                  <div>Uploaded: {fmt(detail.imported_at)}</div>
                  <div style={{ color: detail.slack_posted ? '#4ade80' : '#f59e0b' }}>
                    {detail.slack_posted ? '✓ Posted to #nday-mgt' : '⚠ Not yet posted'}
                  </div>
                </div>
              </div>

              {/* Amazon focus areas */}
              {detail.focus_areas?.length > 0 && (
                <div style={{ background: '#172554', borderRadius: 8, padding: '12px 16px', marginBottom: 20, fontSize: 14, color: '#93c5fd' }}>
                  <strong>Amazon Focus Areas:</strong>{' '}
                  {detail.focus_areas.map((fa, i) => <span key={fa}>{i + 1}. {fa}{i < detail.focus_areas.length - 1 ? '  ' : ''}</span>)}
                </div>
              )}

              {/* DC adjustment note */}
              {detail.dc_adjustment_note && (
                <div style={{ background: '#1c2a1e', borderRadius: 8, padding: '10px 16px', marginBottom: 20, fontSize: 13, color: '#86efac' }}>
                  ℹ️ {detail.dc_adjustment_note}
                </div>
              )}

              {/* Tabs */}
              <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
                {(['overview', 'metrics', 'disputes'] as const).map(t => (
                  <button key={t} onClick={() => setTab(t)} style={{
                    background: tab === t ? '#1e40af' : '#1e293b',
                    color: tab === t ? '#fff' : '#94a3b8',
                    border: 'none', borderRadius: 8, padding: '8px 18px',
                    cursor: 'pointer', fontWeight: 600, fontSize: 14,
                  }}>
                    {t === 'overview' ? 'Overview' : t === 'metrics' ? `All Metrics (${detail.metrics.length})` : `Disputes (${disputeMetrics.length})`}
                  </button>
                ))}
              </div>

              {/* Overview tab */}
              {tab === 'overview' && (
                <div>
                  {belowFantastic.length === 0 ? (
                    <div style={{ color: '#4ade80', fontSize: 15 }}>✅ All metrics at Fantastic — clean week!</div>
                  ) : (
                    <>
                      <h3 style={{ color: '#f1f5f9', fontSize: 15, marginTop: 0, marginBottom: 12 }}>Below Fantastic</h3>
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 10 }}>
                        {belowFantastic.map(m => (
                          <div key={m.slug} style={{
                            background: '#1e293b', borderRadius: 8, padding: '12px 16px',
                            border: `1px solid ${standingColor(m.standing)}44`,
                          }}>
                            <div style={{ fontSize: 11, color: '#64748b', marginBottom: 4 }}>
                              {m.label} {m.weight_pct ? `· ${m.weight_pct}%` : ''}
                            </div>
                            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                              <span style={{ fontWeight: 700, fontSize: 16, color: '#f1f5f9' }}>
                                {m.value_numeric != null ? m.value_numeric : '—'}
                              </span>
                              <span style={{
                                background: standingColor(m.standing) + '22',
                                color: standingColor(m.standing),
                                border: `1px solid ${standingColor(m.standing)}55`,
                                borderRadius: 6, padding: '2px 10px', fontSize: 11, fontWeight: 700,
                              }}>
                                {m.standing}
                              </span>
                            </div>
                          </div>
                        ))}
                      </div>
                    </>
                  )}
                </div>
              )}

              {/* All metrics tab */}
              {tab === 'metrics' && (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                  <thead>
                    <tr style={{ color: '#64748b', textAlign: 'left', borderBottom: '1px solid #334155' }}>
                      {['Metric', 'Category', 'Value', 'Standing', 'Weight'].map(h => (
                        <th key={h} style={{ padding: '8px 12px', fontWeight: 600 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {detail.metrics.map(m => (
                      <tr key={m.slug} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td style={{ padding: '10px 12px', color: '#f1f5f9', fontWeight: 600 }}>{m.label}</td>
                        <td style={{ padding: '10px 12px', color: '#94a3b8', fontSize: 12 }}>{m.category}</td>
                        <td style={{ padding: '10px 12px', color: '#e2e8f0' }}>{m.value_numeric != null ? m.value_numeric : '—'}</td>
                        <td style={{ padding: '10px 12px' }}>
                          <span style={{ color: standingColor(m.standing), fontWeight: 700 }}>
                            {standingEmoji(m.standing)} {m.standing}
                          </span>
                        </td>
                        <td style={{ padding: '10px 12px', color: '#64748b' }}>{m.weight_pct != null ? `${m.weight_pct}%` : '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}

              {/* Disputes tab */}
              {tab === 'disputes' && (
                <div>
                  {disputeMetrics.length === 0 ? (
                    <p style={{ color: '#64748b' }}>No disputable metrics for this week.</p>
                  ) : (
                    <div style={{ display: 'flex', flexDirection: 'column', gap: 14 }}>
                      {disputeMetrics.map(m => (
                        <div key={m.slug} style={{
                          background: '#1e293b', borderRadius: 10, padding: 18,
                          border: `1px solid ${standingColor(m.standing)}44`,
                        }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
                            <div style={{ fontWeight: 700, fontSize: 15, color: '#f1f5f9' }}>{m.label}</div>
                            <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                              <span style={{ color: '#64748b', fontSize: 12 }}>{m.weight_pct != null ? `${m.weight_pct}% weight` : ''}</span>
                              <span style={{
                                background: standingColor(m.standing) + '22',
                                color: standingColor(m.standing),
                                border: `1px solid ${standingColor(m.standing)}55`,
                                borderRadius: 6, padding: '2px 10px', fontSize: 11, fontWeight: 700,
                              }}>
                                {m.standing}
                              </span>
                            </div>
                          </div>
                          <div style={{ color: '#93c5fd', fontSize: 14, lineHeight: 1.6 }}>
                            🔎 {m.dispute_note}
                          </div>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
