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

interface PilotDriver {
  roster_id: number;
  payroll_name: string;
  is_active: boolean | null;
  slack_member_id: string | null;
  reachable: boolean;
}

interface PilotState {
  pilot_active: boolean;
  count: number;
  roster_ids: number[];
  drivers: PilotDriver[];
  note: string;
}

interface RosterDriver { id: number; payroll_name: string }

export default function FeatureFlagsPage() {
  const api = resolveApi();
  const [flags, setFlags] = useState<Flag[] | null>(null);
  const [error, setError] = useState('');
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [pilot, setPilot] = useState<PilotState | null>(null);
  const [roster, setRoster] = useState<RosterDriver[]>([]);
  const [picked, setPicked] = useState<number[]>([]);
  const [pilotBusy, setPilotBusy] = useState(false);
  const [pilotMsg, setPilotMsg] = useState('');

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

  const loadPilot = useCallback(async () => {
    try {
      const [p, r] = await Promise.all([
        fetch(`${api}/pilot/roster`).then(x => x.json()),
        fetch(`${api}/attendance/roster-list`).then(x => x.json()),
      ]);
      setPilot(p);
      setPicked(p.roster_ids ?? []);
      setRoster(r.drivers ?? []);
    } catch { /* panel just stays empty; the flags page still works */ }
  }, [api]);

  const savePilot = async (ids: number[]) => {
    setPilotBusy(true); setPilotMsg('');
    try {
      const res = await fetch(`${api}/pilot/roster`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...authHeaders() },
        body: JSON.stringify({ roster_ids: ids }),
      });
      if (!res.ok) {
        const b = await res.json().catch(() => ({}));
        throw new Error(typeof b.detail === 'string' ? b.detail : `Save failed (HTTP ${res.status}).`);
      }
      const p: PilotState = await res.json();
      setPilot(p); setPicked(p.roster_ids ?? []);
      const unreachable = (p.drivers ?? []).filter(d => !d.reachable);
      setPilotMsg(
        p.pilot_active
          ? `Pilot set to ${p.count} driver(s).` +
            (unreachable.length
              ? ` ⚠️ ${unreachable.map(d => d.payroll_name).join(', ')} not reachable — they will receive nothing.`
              : ' All reachable.')
          : 'Pilot cleared — piloted features now behave normally for everyone.'
      );
    } catch (e: any) { setPilotMsg(e.message || 'Save failed.'); }
    finally { setPilotBusy(false); }
  };

  useEffect(() => { load(); loadPilot(); }, [load, loadPilot]);

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

          {/* Pilot roster — deliberately ABOVE the flags. Choosing WHO a
              feature reaches has to come before switching it on: flipping a
              flag with no pilot set sends to every driver. */}
          <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 10, padding: 18, marginBottom: 24 }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700, color: '#f1f5f9' }}>🎯 Pilot Drivers</h2>
            <p style={{ margin: '4px 0 12px', fontSize: 12, color: '#94a3b8' }}>
              Tick drivers to limit piloted features to them only. With nobody ticked, features go to
              <strong> everyone</strong>. This restricts who a feature reaches — it does not switch anything on.
            </p>

            <div style={{
              padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 13,
              background: pilot?.pilot_active ? '#14251b' : '#0f172a',
              border: `1px solid ${pilot?.pilot_active ? '#166534' : '#334155'}`,
              color: pilot?.pilot_active ? '#4ade80' : '#94a3b8',
            }}>
              {pilot
                ? (pilot.pilot_active
                    ? `Pilot ACTIVE — ${pilot.count} driver(s): ${pilot.drivers.map(d => d.payroll_name).join(', ')}`
                    : 'No pilot set — piloted features reach every driver.')
                : 'Loading pilot…'}
            </div>

            {pilot && pilot.drivers.some(d => !d.reachable) && (
              <div style={{ padding: '8px 12px', borderRadius: 6, marginBottom: 12, fontSize: 13,
                            background: '#3b2f1e', border: '1px solid #854d0e', color: '#fbbf24' }}>
                ⚠️ Not reachable (no Slack link or inactive):{' '}
                {pilot.drivers.filter(d => !d.reachable).map(d => d.payroll_name).join(', ')} — they will receive nothing.
              </div>
            )}

            <div style={{ maxHeight: 220, overflowY: 'auto', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, padding: 10 }}>
              {roster.length === 0 && <span style={{ color: '#64748b', fontSize: 13 }}>Loading drivers…</span>}
              {roster.map(d => (
                <label key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#e2e8f0', padding: '3px 0' }}>
                  <input
                    type="checkbox"
                    checked={picked.includes(d.id)}
                    onChange={e => setPicked(prev => e.target.checked ? [...prev, d.id] : prev.filter(x => x !== d.id))}
                  />
                  {d.payroll_name}
                </label>
              ))}
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginTop: 12, flexWrap: 'wrap' }}>
              <button
                onClick={() => savePilot(picked)}
                disabled={pilotBusy}
                style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid #2563eb', background: '#2563eb',
                         color: '#fff', fontSize: 13, fontWeight: 600, cursor: pilotBusy ? 'default' : 'pointer', opacity: pilotBusy ? 0.6 : 1 }}
              >{pilotBusy ? 'Saving…' : `Save pilot (${picked.length})`}</button>
              <button
                onClick={() => { setPicked([]); savePilot([]); }}
                disabled={pilotBusy}
                style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid #334155', background: '#0f172a',
                         color: '#94a3b8', fontSize: 13, fontWeight: 600, cursor: pilotBusy ? 'default' : 'pointer' }}
              >Clear pilot (send to everyone)</button>
              {pilotMsg && <span style={{ fontSize: 12, color: pilotMsg.includes('⚠️') || pilotMsg.includes('failed') ? '#fbbf24' : '#4ade80' }}>{pilotMsg}</span>}
            </div>
          </div>

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
