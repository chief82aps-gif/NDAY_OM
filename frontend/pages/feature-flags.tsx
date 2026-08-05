import { useState, useCallback, useEffect } from 'react';
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
  description: string;
  category: string;
  enabled: boolean;
  source: 'database' | 'env_default';
  updated_at: string | null;
  updated_by: string | null;
}

export default function FeatureFlagsPage() {
  const api = resolveApi();
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [error, setError] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const authHeaders = (): HeadersInit => {
    const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
    return token ? { Authorization: `Bearer ${token}` } : {};
  };

  const load = useCallback(async () => {
    setError('');
    try {
      const res = await fetch(`${api}/feature-flags`, { headers: authHeaders() });
      if (res.status === 403) {
        setError('Admin access required.');
        return;
      }
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      setFlags(data.flags ?? []);
    } catch {
      setError('Network error.');
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const toggle = async (key: string, enabled: boolean) => {
    setBusyKey(key);
    try {
      const res = await fetch(`${api}/feature-flags/${key}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ enabled }),
      });
      if (!res.ok) { setError(`Failed to update ${key}.`); return; }
      await load();
    } catch {
      setError('Network error.');
    } finally {
      setBusyKey(null);
    }
  };

  const categories = flags ? Array.from(new Set(flags.map(f => f.category))) : [];

  return (
    <ProtectedRoute allowedRoles={['admin', 'super_user']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 800, margin: '0 auto' }}>
          <div style={{ marginBottom: 20 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>🚦 Feature Flags</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Toggle features live — no redeploy needed. A toggle here overrides the Render environment
              variable of the same name; leaving it untouched falls back to Render's value.
            </p>
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {!error && !flags && <p style={{ color: '#64748b' }}>Loading…</p>}

          {categories.map(category => (
            <div key={category} style={{ marginBottom: 24 }}>
              <h2 style={{ fontSize: 13, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: 8 }}>
                {category}
              </h2>
              {flags!.filter(f => f.category === category).map(f => (
                <div
                  key={f.key}
                  style={{
                    background: '#1e293b', borderRadius: 10, padding: 14, marginBottom: 8,
                    border: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 16,
                  }}
                >
                  <div style={{ flex: 1 }}>
                    <div style={{ fontWeight: 600, color: '#f1f5f9', fontSize: 14 }}>{f.label}</div>
                    <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 2 }}>{f.description}</div>
                    <div style={{ fontSize: 11, color: '#475569', marginTop: 4 }}>
                      {f.key} · {f.source === 'database'
                        ? `Overridden here${f.updated_by ? ` by ${f.updated_by}` : ''}${f.updated_at ? ` · ${new Date(f.updated_at).toLocaleString()}` : ''}`
                        : "Using Render's value (not yet overridden here)"}
                    </div>
                  </div>
                  <button
                    onClick={() => toggle(f.key, !f.enabled)}
                    disabled={busyKey === f.key}
                    style={{
                      padding: '8px 16px', borderRadius: 20, fontSize: 13, fontWeight: 700, cursor: busyKey === f.key ? 'wait' : 'pointer',
                      border: 'none', minWidth: 64,
                      background: f.enabled ? '#16a34a' : '#334155',
                      color: f.enabled ? '#fff' : '#94a3b8',
                      opacity: busyKey === f.key ? 0.6 : 1,
                    }}
                  >
                    {f.enabled ? 'ON' : 'OFF'}
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
