'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import ProtectedRoute from '../../../components/ProtectedRoute';

function resolveApi() {
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://127.0.0.1:8001';
  }
  return '';
}

const REASON_LABELS: Record<string, string> = {
  sick: 'Sick', personal: 'Personal', family: 'Family Emergency',
  weather: 'Weather', transportation: 'Transportation', other: 'Other',
};

function fmt(iso: string | null) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleString('en-US', { dateStyle: 'medium', timeStyle: 'short' }); }
  catch { return iso; }
}

interface Event {
  id: number;
  driver_name: string;
  event_date: string;
  reason_code: string;
  notes: string | null;
  call_time: string | null;
  hours_before_shift: number | null;
  compliant: boolean | null;
  scheduled_wave: string | null;
  signature_name: string | null;
  signature_at: string | null;
  manager_signature_name: string | null;
  manager_signature_at: string | null;
  created_at: string | null;
}

export default function CalloutReviewPage() {
  const router = useRouter();
  const { id } = router.query;
  const api = resolveApi();

  const [event, setEvent] = useState<Event | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [managerName, setManagerName] = useState('');
  const [signErr, setSignErr] = useState('');
  const [signing, setSigning] = useState(false);
  const [signed, setSigned] = useState(false);

  useEffect(() => {
    if (!id) return;
    fetch(`${api}/attendance/events/${id}`)
      .then(r => { if (!r.ok) throw new Error('Event not found'); return r.json(); })
      .then(d => { setEvent(d); setSigned(!!d.manager_signature_name); })
      .catch(e => setError(e.message))
      .finally(() => setLoading(false));
  }, [id, api]);

  async function handleSign(e: React.FormEvent) {
    e.preventDefault();
    setSignErr('');
    if (!managerName.trim()) { setSignErr('Please type your full name to sign.'); return; }
    setSigning(true);
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const res = await fetch(`${api}/attendance/events/${id}/manager-sign`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ manager_name: managerName.trim() }),
      });
      if (!res.ok) { const d = await res.json(); throw new Error(d.detail ?? 'Sign failed'); }
      const d = await res.json();
      setEvent(prev => prev ? { ...prev, manager_signature_name: d.manager_signature_name, manager_signature_at: new Date().toISOString() } : prev);
      setSigned(true);
    } catch (err: unknown) {
      setSignErr(err instanceof Error ? err.message : 'Error signing.');
    } finally {
      setSigning(false);
    }
  }

  if (loading) return (
    <ProtectedRoute allowedRoles={['ops_manager', 'admin']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: '#64748b' }}>Loading writeup…</p>
      </div>
    </ProtectedRoute>
  );

  if (error || !event) return (
    <ProtectedRoute allowedRoles={['ops_manager', 'admin']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <p style={{ color: '#f87171' }}>{error || 'Event not found.'}</p>
      </div>
    </ProtectedRoute>
  );

  const today = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });

  return (
    <ProtectedRoute allowedRoles={['ops_manager', 'admin']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 680, margin: '0 auto' }}>

          <div style={{ marginBottom: 24 }}>
            <p style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase', letterSpacing: 2, margin: 0 }}>New Day Logistics</p>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#f1f5f9', margin: '4px 0 0' }}>Manager Review — Absence Writeup</h1>
            <p style={{ fontSize: 13, color: '#64748b', margin: '4px 0 0' }}>Event #{event.id}</p>
          </div>

          {/* Writeup document */}
          <div style={{ background: '#fff', borderRadius: 12, padding: 28, color: '#1e293b', marginBottom: 24 }}>
            <div style={{ textAlign: 'center', borderBottom: '1px solid #e2e8f0', paddingBottom: 16, marginBottom: 16 }}>
              <p style={{ fontWeight: 700, fontSize: 16, margin: 0 }}>NEW DAY LOGISTICS LLC</p>
              <p style={{ fontWeight: 600, color: '#475569', margin: '2px 0 0' }}>Absence Notification</p>
              <p style={{ fontSize: 12, color: '#94a3b8', margin: '2px 0 0' }}>HRM-023.1 — Driver Self-Report</p>
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '6px 0', fontSize: 13, marginBottom: 16 }}>
              {[
                ['Employee', event.driver_name],
                ['Date of Absence', event.event_date],
                ['Submitted', fmt(event.created_at)],
                ['Call Time', fmt(event.call_time)],
              ].map(([label, value]) => (
                <>
                  <span style={{ color: '#64748b' }}>{label}:</span>
                  <span style={{ fontWeight: 600 }}>{value}</span>
                </>
              ))}
            </div>

            <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: 14, marginBottom: 14 }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>1. Reason for Absence</p>
              <p style={{ fontSize: 13, margin: 0 }}>{REASON_LABELS[event.reason_code] ?? event.reason_code}</p>
              {event.notes && <p style={{ fontSize: 12, color: '#475569', fontStyle: 'italic', marginTop: 4 }}>"{event.notes}"</p>}
            </div>

            <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: 14, marginBottom: 14 }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>2. 4-Hour Rule Compliance</p>
              {event.hours_before_shift !== null ? (
                <p style={{ fontSize: 13, margin: 0, color: event.compliant ? '#16a34a' : '#dc2626', fontWeight: 600 }}>
                  {event.compliant ? '✓ Compliant' : '✗ Non-Compliant'} — called {Math.abs(event.hours_before_shift).toFixed(1)}h before shift
                </p>
              ) : (
                <p style={{ fontSize: 13, color: '#64748b', margin: 0 }}>Wave time not provided — compliance not calculated</p>
              )}
            </div>

            <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: 14, marginBottom: 14 }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>3. Driver Signature</p>
              {event.signature_name ? (
                <p style={{ fontSize: 14, fontStyle: 'italic', margin: 0 }}>
                  {event.signature_name} <span style={{ fontSize: 11, color: '#94a3b8' }}>— {fmt(event.signature_at)}</span>
                </p>
              ) : (
                <p style={{ fontSize: 13, color: '#f59e0b', margin: 0 }}>⚠️ Driver did not sign</p>
              )}
            </div>

            <div style={{ borderTop: '1px solid #e2e8f0', paddingTop: 14 }}>
              <p style={{ fontSize: 11, fontWeight: 700, color: '#64748b', textTransform: 'uppercase', letterSpacing: 1, marginBottom: 6 }}>4. Manager Countersignature</p>
              {event.manager_signature_name ? (
                <p style={{ fontSize: 14, fontStyle: 'italic', margin: 0, color: '#16a34a' }}>
                  ✓ {event.manager_signature_name} <span style={{ fontSize: 11, color: '#94a3b8' }}>— {fmt(event.manager_signature_at)}</span>
                </p>
              ) : (
                <p style={{ fontSize: 13, color: '#f59e0b', margin: 0 }}>⚠️ Awaiting manager signature</p>
              )}
            </div>
          </div>

          {/* Sign form or already-signed confirmation */}
          {signed ? (
            <div style={{ background: '#14532d22', border: '1px solid #16a34a55', borderRadius: 12, padding: 20, textAlign: 'center' }}>
              <p style={{ fontSize: 20 }}>✅</p>
              <p style={{ fontWeight: 700, color: '#4ade80', margin: '4px 0' }}>Writeup Countersigned</p>
              <p style={{ fontSize: 13, color: '#64748b', margin: 0 }}>Signed by {event.manager_signature_name}</p>
              <button
                onClick={() => router.push('/admin/callout-review')}
                style={{ marginTop: 16, background: '#1e293b', color: '#94a3b8', border: 'none', borderRadius: 8, padding: '8px 20px', cursor: 'pointer', fontSize: 13 }}
              >
                ← Back to Pending Reviews
              </button>
            </div>
          ) : (
            <form onSubmit={handleSign} style={{ background: '#1e293b', borderRadius: 12, padding: 24 }}>
              <p style={{ fontWeight: 600, color: '#f1f5f9', margin: '0 0 4px' }}>Manager Electronic Signature</p>
              <p style={{ fontSize: 13, color: '#64748b', margin: '0 0 16px' }}>
                By typing your full name, you confirm you have reviewed this absence writeup and acknowledge it in accordance with NDL HRM-023.1.
              </p>
              <input
                type="text"
                value={managerName}
                onChange={e => setManagerName(e.target.value)}
                placeholder="Type your full name to sign…"
                style={{ width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: '12px 16px', color: '#f1f5f9', fontSize: 15, boxSizing: 'border-box', fontStyle: 'italic' }}
              />
              {signErr && <p style={{ color: '#f87171', fontSize: 13, margin: '8px 0 0' }}>{signErr}</p>}
              <button
                type="submit"
                disabled={signing}
                style={{ marginTop: 16, width: '100%', background: signing ? '#334155' : '#1d4ed8', color: '#fff', border: 'none', borderRadius: 10, padding: '16px', fontSize: 16, fontWeight: 700, cursor: signing ? 'wait' : 'pointer' }}
              >
                {signing ? 'Signing…' : '✍️  Sign & Countersign Writeup'}
              </button>
            </form>
          )}

          <p style={{ textAlign: 'center', fontSize: 12, color: '#334155', marginTop: 20 }}>
            {today} · New Day Logistics LLC · HRM-023.1
          </p>
        </div>
      </div>
    </ProtectedRoute>
  );
}
