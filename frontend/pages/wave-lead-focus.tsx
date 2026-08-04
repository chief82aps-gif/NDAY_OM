import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/router';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface FocusArea {
  metric: string;
  score: number;
  video: string | null;
}

interface DriverFocus {
  roster_id: number;
  driver_name: string;
  overall: number | null;
  tier: string;
  safety: number | null;
  quality: number | null;
  attendance: number | null;
  gap_to_next_tier: number | null;
  focus_areas: FocusArea[];
}

const TIER_COLOR: Record<string, string> = {
  platinum: '#a78bfa', gold: '#facc15', silver: '#cbd5e1', bronze: '#fb923c',
  tin: '#94a3b8', lead: '#64748b', sawdust: '#f87171', gray: '#475569',
};
const TIER_ICON: Record<string, string> = {
  platinum: '💎', gold: '⭐', silver: '✨', bronze: '🔶', does_not_meet: '🔩', gray: '❔',
};

function tierLabel(tier: string): string {
  if (tier === 'gray') return 'No Data';
  if (tier === 'does_not_meet') return 'Does Not Meet Minimum';
  return tier.charAt(0).toUpperCase() + tier.slice(1);
}

export default function WaveLeadFocusPage() {
  const router = useRouter();
  const api = resolveApi();
  const [half, setHalf] = useState<'front' | 'back'>('front');
  const [drivers, setDrivers] = useState<DriverFocus[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  useEffect(() => {
    if (!router.isReady) return;
    const q = router.query.half;
    if (q === 'front' || q === 'back') setHalf(q);
  }, [router.isReady, router.query.half]);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const res = await fetch(`${api}/wave-lead/team-focus?half=${half}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.status === 403) {
        setError("You can only view your own team's focus page.");
        setDrivers(null);
        return;
      }
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      setDrivers(data.drivers ?? []);
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api, half]);

  useEffect(() => { load(); }, [load]);

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 20 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>🌊 Wave Lead — Team Focus</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Your team's performance, sorted by biggest bang for the buck — closest to their next tier first,
              since that's where a quick conversation is most likely to actually move the needle.
            </p>
          </div>

          <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
            {(['front', 'back'] as const).map(h => (
              <button
                key={h}
                onClick={() => setHalf(h)}
                style={{
                  padding: '8px 16px', borderRadius: 8, fontSize: 13, fontWeight: 600, cursor: 'pointer',
                  border: half === h ? '1px solid #2563eb' : '1px solid #334155',
                  background: half === h ? '#2563eb' : '#1e293b',
                  color: half === h ? '#fff' : '#94a3b8',
                }}
              >
                {h === 'front' ? 'Front Half' : 'Back Half'}
              </button>
            ))}
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {!loading && !error && drivers && drivers.length === 0 && (
            <p style={{ color: '#64748b' }}>No standing team members assigned yet for this half.</p>
          )}

          {!loading && !error && drivers && drivers.map((d, i) => (
            <div key={d.roster_id} style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 12, border: '1px solid #334155' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ color: '#475569', fontSize: 13, fontWeight: 700, width: 20 }}>#{i + 1}</span>
                  <span style={{ fontWeight: 700, color: '#f1f5f9', fontSize: 15 }}>{d.driver_name}</span>
                  <span style={{ fontSize: 12, color: TIER_COLOR[d.tier] ?? '#94a3b8' }}>
                    {TIER_ICON[d.tier] ?? ''} {tierLabel(d.tier)}
                  </span>
                </div>
                {d.gap_to_next_tier !== null ? (
                  <span style={{ fontSize: 12, color: '#4ade80', fontWeight: 600 }}>
                    {d.gap_to_next_tier.toFixed(1)} pts to next tier
                  </span>
                ) : d.tier === 'platinum' ? (
                  <span style={{ fontSize: 12, color: '#a78bfa' }}>Already Platinum</span>
                ) : (
                  <span style={{ fontSize: 12, color: '#64748b' }}>No data</span>
                )}
              </div>

              <div style={{ display: 'flex', gap: 20, fontSize: 13, color: '#94a3b8', marginBottom: 10 }}>
                <span>Overall: <strong style={{ color: '#e2e8f0' }}>{d.overall ?? '—'}</strong></span>
                <span>Safety: <strong style={{ color: '#e2e8f0' }}>{d.safety ?? '—'}</strong></span>
                <span>Quality: <strong style={{ color: '#e2e8f0' }}>{d.quality ?? '—'}</strong></span>
                <span>Attendance: <strong style={{ color: '#e2e8f0' }}>{d.attendance ?? '—'}</strong></span>
              </div>

              {d.focus_areas.length > 0 && (
                <div style={{ borderTop: '1px solid #334155', paddingTop: 10 }}>
                  <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase', marginBottom: 6 }}>
                    Improvement Focus
                  </div>
                  {d.focus_areas.map((f, fi) => (
                    <div key={fi} style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, marginBottom: 4 }}>
                      <span style={{ color: '#e2e8f0' }}>{f.metric} <span style={{ color: '#64748b' }}>({f.score})</span></span>
                      {f.video && <span style={{ color: '#60a5fa' }}>🎥 {f.video}</span>}
                    </div>
                  ))}
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
