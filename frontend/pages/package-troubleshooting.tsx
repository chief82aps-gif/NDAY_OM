import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface GuideEntry {
  reason_code: string;
  steps: string | null;
  updated_by: string | null;
  updated_at: string | null;
  seen_in_data: boolean;
}

export default function PackageTroubleshootingPage() {
  const api = resolveApi();
  const [guide, setGuide] = useState<GuideEntry[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [savingCode, setSavingCode] = useState<string | null>(null);
  const [enteredBy, setEnteredBy] = useState('');
  const [newCode, setNewCode] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${api}/packages/troubleshooting-guide`);
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      const g: GuideEntry[] = data.guide ?? [];
      setGuide(g);
      setDrafts(prev => {
        const next = { ...prev };
        for (const entry of g) {
          if (next[entry.reason_code] === undefined) next[entry.reason_code] = entry.steps ?? '';
        }
        return next;
      });
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const save = async (reasonCode: string) => {
    const steps = (drafts[reasonCode] ?? '').trim();
    if (!steps) { setError('Steps cannot be empty.'); return; }
    setSavingCode(reasonCode);
    setError('');
    try {
      const res = await fetch(`${api}/packages/troubleshooting-guide`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reason_code: reasonCode, steps, updated_by: enteredBy || null }),
      });
      if (!res.ok) { setError('Save failed.'); return; }
      await load();
    } catch {
      setError('Network error.');
    } finally {
      setSavingCode(null);
    }
  };

  const addNewCode = () => {
    const code = newCode.trim().toUpperCase();
    if (!code) return;
    setGuide(prev => {
      const list = prev ?? [];
      if (list.some(g => g.reason_code === code)) return list;
      return [...list, { reason_code: code, steps: null, updated_by: null, updated_at: null, seen_in_data: false }]
        .sort((a, b) => a.reason_code.localeCompare(b.reason_code));
    });
    setDrafts(prev => ({ ...prev, [code]: prev[code] ?? '' }));
    setNewCode('');
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 8 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>📦 Package Marking Troubleshooting Guide</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Reference only — this does not turn on any automated driver DM. For each reason code, enter what a
              driver should be asked or checked before a package gets marked that way.
            </p>
          </div>

          <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px', margin: '16px 0', fontSize: 13, color: '#94a3b8' }}>
            Draft, pending the real resolution-process conversation with dispatch — see
            <code style={{ marginLeft: 4, color: '#e2e8f0' }}>Governance/PACKAGE_RTS_RESOLUTION_MODULE.md</code>.
          </div>

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 20 }}>
            <label style={{ fontSize: 13, color: '#94a3b8' }}>Entering as:</label>
            <input
              value={enteredBy}
              onChange={e => setEnteredBy(e.target.value)}
              placeholder="Your name"
              style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid #334155', background: '#0f172a', color: '#e2e8f0', fontSize: 13, width: 160 }}
            />
            <div style={{ flex: 1 }} />
            <input
              value={newCode}
              onChange={e => setNewCode(e.target.value)}
              placeholder="Add a reason code not listed yet"
              style={{ padding: '6px 10px', borderRadius: 6, border: '1px solid #334155', background: '#0f172a', color: '#e2e8f0', fontSize: 13, width: 260 }}
              onKeyDown={e => { if (e.key === 'Enter') addNewCode(); }}
            />
            <button
              onClick={addNewCode}
              style={{ padding: '6px 14px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: '1px solid #334155', background: '#1e293b', color: '#94a3b8' }}
            >
              + Add
            </button>
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {!loading && guide && guide.length === 0 && (
            <p style={{ color: '#64748b' }}>No reason codes seen in package data yet. Add one above to get started.</p>
          )}

          {!loading && guide && guide.map(entry => (
            <div key={entry.reason_code} style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 12, border: '1px solid #334155' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8, gap: 12 }}>
                <span style={{ fontWeight: 700, color: '#f1f5f9', fontSize: 14 }}>{entry.reason_code}</span>
                <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                  {!entry.seen_in_data && (
                    <span style={{ fontSize: 11, color: '#64748b', border: '1px solid #334155', borderRadius: 4, padding: '2px 6px' }}>
                      not yet seen in data
                    </span>
                  )}
                  {entry.updated_at && (
                    <span style={{ fontSize: 11, color: '#64748b' }}>
                      {entry.updated_by ? `${entry.updated_by} · ` : ''}{new Date(entry.updated_at).toLocaleString()}
                    </span>
                  )}
                </div>
              </div>
              <textarea
                value={drafts[entry.reason_code] ?? ''}
                onChange={e => setDrafts(prev => ({ ...prev, [entry.reason_code]: e.target.value }))}
                placeholder="What should a driver be asked or checked before marking a package this way?"
                rows={3}
                style={{ width: '100%', boxSizing: 'border-box', padding: 10, borderRadius: 6, border: '1px solid #334155', background: '#0f172a', color: '#e2e8f0', fontSize: 14, fontFamily: 'inherit', resize: 'vertical', marginBottom: 8 }}
              />
              <button
                onClick={() => save(entry.reason_code)}
                disabled={savingCode === entry.reason_code}
                style={{
                  padding: '6px 14px', borderRadius: 6, fontSize: 12, fontWeight: 600,
                  cursor: savingCode === entry.reason_code ? 'default' : 'pointer',
                  border: '1px solid #2563eb', background: savingCode === entry.reason_code ? '#1e293b' : '#2563eb',
                  color: '#fff', opacity: savingCode === entry.reason_code ? 0.6 : 1,
                }}
              >
                {savingCode === entry.reason_code ? 'Saving…' : 'Save'}
              </button>
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
