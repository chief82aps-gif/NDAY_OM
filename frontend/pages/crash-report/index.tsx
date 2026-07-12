import { useState } from 'react';
import { useRouter } from 'next/router';
import ProtectedRoute from '../../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

export default function CrashReportStartPage() {
  const router = useRouter();
  const api = resolveApi();
  const [driverName, setDriverName] = useState('');
  const [submittedBy, setSubmittedBy] = useState('');
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const generate = async () => {
    if (!driverName.trim()) {
      setError('Enter the driver’s name.');
      return;
    }
    setCreating(true);
    setError(null);
    try {
      const res = await fetch(`${api}/crash-report/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_name: driverName.trim(), submitted_by: submittedBy.trim() || undefined }),
      });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      router.push(`/crash-report/${data.report.id}`);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to create report');
    } finally {
      setCreating(false);
    }
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 520, margin: '0 auto' }}>
          <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Generate Crash Report</h1>
          <p style={{ margin: '4px 0 24px', fontSize: 14, color: '#64748b' }}>
            Creates a draft report prepopulated with today&apos;s van, DSP code, and station for this driver.
            The driver completes the rest on the next screen.
          </p>

          <label style={{ display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6 }}>Driver Name</label>
          <input
            value={driverName}
            onChange={(e) => setDriverName(e.target.value)}
            placeholder="Last, First"
            style={{
              width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid #334155',
              background: '#1e293b', color: '#e2e8f0', fontSize: 14, marginBottom: 16,
            }}
          />

          <label style={{ display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6 }}>Your Name (generating this report)</label>
          <input
            value={submittedBy}
            onChange={(e) => setSubmittedBy(e.target.value)}
            placeholder="Manager / Dispatcher name"
            style={{
              width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid #334155',
              background: '#1e293b', color: '#e2e8f0', fontSize: 14, marginBottom: 20,
            }}
          />

          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}

          <button
            onClick={generate}
            disabled={creating}
            style={{
              background: creating ? '#1e293b' : '#ef4444',
              color: '#fff', border: 'none', borderRadius: 8, padding: '12px 24px',
              cursor: creating ? 'default' : 'pointer', fontWeight: 700, fontSize: 15, width: '100%',
            }}
          >
            {creating ? 'Creating…' : 'Generate Crash Report'}
          </button>
        </div>
      </div>
    </ProtectedRoute>
  );
}
