import { useState, useCallback, useEffect } from 'react';
import Link from 'next/link';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface Flag {
  key: string;
  label: string;
  enabled: boolean;
  source: 'database' | 'env_default';
}

interface GlitchReport {
  id: number;
  reporter_name: string | null;
  description: string;
  reported_at: string | null;
}

interface RedemptionRequest {
  id: number;
  driver: string;
  item: string;
  point_cost: number;
  is_cash_out: boolean;
}

const TOOLS = [
  { href: '/feature-flags', label: '🚦 Feature Flags', description: 'Toggle features live, no redeploy needed.' },
  { href: '/admin', label: '⚙️ Admin Panel', description: 'Manage users, credentials, and system settings.' },
  { href: '/swag-store-admin', label: '🎁 Swag Store', description: 'Catalog + pending NDAY Points redemptions.' },
  { href: '/glitch-reports', label: '🐛 Glitch Reports', description: 'Bugs flagged from every Home tab.' },
];

export default function AdminHomePage() {
  const api = resolveApi();
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [glitches, setGlitches] = useState<GlitchReport[] | null>(null);
  const [redemptions, setRedemptions] = useState<RedemptionRequest[] | null>(null);
  const [error, setError] = useState('');

  const authHeaders = (): HeadersInit => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const load = useCallback(async () => {
    setError('');
    try {
      const [flagsRes, glitchRes, redeemRes] = await Promise.all([
        fetch(`${api}/feature-flags`, { headers: authHeaders() }),
        fetch(`${api}/glitch-reports?status=open`),
        fetch(`${api}/nday-points/pending-redemptions`),
      ]);
      if (flagsRes.ok) setFlags((await flagsRes.json()).flags ?? []);
      if (glitchRes.ok) setGlitches((await glitchRes.json()).reports ?? []);
      if (redeemRes.ok) setRedemptions((await redeemRes.json()).requests ?? []);
    } catch {
      setError('Some data failed to load.');
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const onFlags = flags?.filter(f => f.enabled) ?? [];

  const cardStyle: React.CSSProperties = {
    background: '#1e293b', borderRadius: 10, padding: 18, border: '1px solid #334155',
  };

  return (
    <ProtectedRoute allowedRoles={['admin']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 960, margin: '0 auto' }}>
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ margin: 0, fontSize: 24, fontWeight: 700, color: '#f1f5f9' }}>🛡️ Admin Home</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>Only visible to the admin role.</p>
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          <h2 style={{ fontSize: 13, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>
            Tools
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginBottom: 28 }}>
            {TOOLS.map(t => (
              <Link key={t.href} href={t.href} style={{ textDecoration: 'none' }}>
                <div style={{ ...cardStyle, cursor: 'pointer', height: '100%' }}>
                  <div style={{ fontWeight: 700, color: '#f1f5f9', fontSize: 15, marginBottom: 4 }}>{t.label}</div>
                  <div style={{ fontSize: 12, color: '#94a3b8' }}>{t.description}</div>
                </div>
              </Link>
            ))}
          </div>

          <h2 style={{ fontSize: 13, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 10 }}>
            System Overview
          </h2>
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(280px, 1fr))', gap: 12 }}>
            <div style={cardStyle}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#f1f5f9', marginBottom: 8 }}>
                🚦 {onFlags.length} flag{onFlags.length !== 1 ? 's' : ''} currently ON
              </div>
              {flags === null && <div style={{ fontSize: 12, color: '#64748b' }}>Loading…</div>}
              {onFlags.length === 0 && flags !== null && <div style={{ fontSize: 12, color: '#64748b' }}>Nothing flipped on yet.</div>}
              {onFlags.slice(0, 6).map(f => (
                <div key={f.key} style={{ fontSize: 12, color: '#94a3b8', padding: '3px 0' }}>
                  • {f.label} {f.source === 'database' && <span style={{ color: '#4ade80' }}>(overridden here)</span>}
                </div>
              ))}
              {onFlags.length > 6 && (
                <div style={{ fontSize: 11, color: '#64748b', marginTop: 4 }}>+ {onFlags.length - 6} more</div>
              )}
              <Link href="/feature-flags" style={{ fontSize: 12, color: '#60a5fa', display: 'inline-block', marginTop: 8 }}>
                Manage flags →
              </Link>
            </div>

            <div style={cardStyle}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#f1f5f9', marginBottom: 8 }}>
                🐛 {glitches?.length ?? 0} open glitch report{(glitches?.length ?? 0) !== 1 ? 's' : ''}
              </div>
              {glitches === null && <div style={{ fontSize: 12, color: '#64748b' }}>Loading…</div>}
              {glitches?.length === 0 && <div style={{ fontSize: 12, color: '#64748b' }}>Nothing open.</div>}
              {glitches?.slice(0, 4).map(g => (
                <div key={g.id} style={{ fontSize: 12, color: '#94a3b8', padding: '3px 0' }}>
                  • {g.reporter_name || 'Unknown'}: {g.description.slice(0, 60)}{g.description.length > 60 ? '…' : ''}
                </div>
              ))}
              <Link href="/glitch-reports" style={{ fontSize: 12, color: '#60a5fa', display: 'inline-block', marginTop: 8 }}>
                View all →
              </Link>
            </div>

            <div style={cardStyle}>
              <div style={{ fontSize: 13, fontWeight: 700, color: '#f1f5f9', marginBottom: 8 }}>
                🎁 {redemptions?.length ?? 0} pending redemption{(redemptions?.length ?? 0) !== 1 ? 's' : ''}
              </div>
              {redemptions === null && <div style={{ fontSize: 12, color: '#64748b' }}>Loading…</div>}
              {redemptions?.length === 0 && <div style={{ fontSize: 12, color: '#64748b' }}>Nothing pending.</div>}
              {redemptions?.slice(0, 4).map(r => (
                <div key={r.id} style={{ fontSize: 12, color: '#94a3b8', padding: '3px 0' }}>
                  • {r.driver} — {r.item} ({r.point_cost} pts{r.is_cash_out ? ', cash-out' : ''})
                </div>
              ))}
              <Link href="/swag-store-admin" style={{ fontSize: 12, color: '#60a5fa', display: 'inline-block', marginTop: 8 }}>
                Fulfill requests →
              </Link>
            </div>
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
