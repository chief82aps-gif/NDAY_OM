import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';

interface ViolationRow {
  start_date: string | null;
  duration_seconds: number | null;
  fleet_type: string | null;
  vin: string | null;
}

interface ViolationData {
  transporter_id: string;
  transporter_name: string;
  week: string;
  violation_count: number;
  violations: ViolationRow[];
  already_acknowledged: boolean;
  acknowledged_at: string | null;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

type Step = 'loading' | 'error' | 'review' | 'sign' | 'done' | 'already_done';

export default function DvicAckPage() {
  const router = useRouter();
  const { tid, week } = router.query as { tid?: string; week?: string };

  const [step, setStep] = useState<Step>('loading');
  const [data, setData] = useState<ViolationData | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [sigName, setSigName] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const api = resolveApi();

  useEffect(() => {
    if (!tid || !week) return;
    fetch(`${api}/dvic/violations-for-ack/${tid}?week=${encodeURIComponent(week)}`)
      .then(r => r.ok ? r.json() : r.json().then(e => Promise.reject(e.detail || 'Not found')))
      .then((d: ViolationData) => {
        setData(d);
        setStep(d.already_acknowledged ? 'already_done' : 'review');
      })
      .catch((e: unknown) => {
        setErrorMsg(typeof e === 'string' ? e : 'Could not load your violation records. The link may be expired.');
        setStep('error');
      });
  }, [tid, week, api]);

  const submit = async () => {
    if (!sigName.trim() || !data) return;
    setSubmitting(true);
    try {
      const res = await fetch(`${api}/dvic/acknowledge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          transporter_id: data.transporter_id,
          week: data.week,
          signature_name: sigName.trim(),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      setStep('done');
    } catch (e: unknown) {
      alert('Submission failed: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSubmitting(false);
    }
  };

  const weekLabel = (w: string) => w.replace('-W', ' Week ').replace('2026 Week ', 'Week ');

  const container: React.CSSProperties = {
    minHeight: '100vh',
    background: '#0f172a',
    color: '#e2e8f0',
    fontFamily: 'system-ui, sans-serif',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    padding: '24px 16px',
  };

  const card: React.CSSProperties = {
    background: '#1e293b',
    borderRadius: 12,
    padding: 32,
    maxWidth: 520,
    width: '100%',
    boxShadow: '0 4px 24px rgba(0,0,0,0.4)',
  };

  if (step === 'loading') return (
    <div style={container}><p style={{ color: '#64748b' }}>Loading…</p></div>
  );

  if (step === 'error') return (
    <div style={container}>
      <div style={{ ...card, borderColor: '#7f1d1d', border: '1px solid' }}>
        <h2 style={{ color: '#f87171', margin: '0 0 12px' }}>Link Not Found</h2>
        <p style={{ color: '#94a3b8' }}>{errorMsg}</p>
      </div>
    </div>
  );

  if (step === 'already_done') return (
    <div style={container}>
      <div style={{ ...card }}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 16 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Already Acknowledged</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          {data?.transporter_name}, you already signed this notice for {weekLabel(data?.week || '')}
          {data?.acknowledged_at ? ` on ${new Date(data.acknowledged_at).toLocaleDateString()}` : ''}.
        </p>
      </div>
    </div>
  );

  if (step === 'done') return (
    <div style={container}>
      <div style={{ ...card }}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 16 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Acknowledgment Received</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          Thank you, {data?.transporter_name}. Your acknowledgment has been recorded and management has been notified.
        </p>
        <p style={{ color: '#64748b', textAlign: 'center', fontSize: 13 }}>
          Please ensure your next vehicle inspection meets the 90-second minimum requirement. Your safety matters.
        </p>
      </div>
    </div>
  );

  if (!data) return null;

  const urgency = data.violation_count >= 5 ? 'error' : data.violation_count >= 3 ? 'warning' : 'notice';
  const urgencyColor = urgency === 'error' ? '#ef4444' : urgency === 'warning' ? '#f59e0b' : '#60a5fa';
  const urgencyLabel = urgency === 'error' ? '🚨 Urgent Safety Notice' : urgency === 'warning' ? '⚠️ Safety Warning' : '👋 Safety Notice';

  return (
    <div style={container}>
      <div style={{ ...card, border: `1px solid ${urgencyColor}44` }}>

        {/* Header */}
        <div style={{ marginBottom: 20 }}>
          <div style={{
            display: 'inline-block',
            background: urgencyColor + '22', color: urgencyColor,
            border: `1px solid ${urgencyColor}55`, borderRadius: 6,
            padding: '4px 12px', fontSize: 12, fontWeight: 700, marginBottom: 12,
          }}>
            {urgencyLabel}
          </div>
          <h2 style={{ margin: '0 0 4px', color: '#f1f5f9', fontSize: 20 }}>
            Hi {data.transporter_name.split(' ')[0]},
          </h2>
          <p style={{ margin: 0, color: '#94a3b8', fontSize: 14 }}>
            NDAY Management has flagged your vehicle inspection records for {weekLabel(data.week)}.
          </p>
        </div>

        {/* What happened */}
        <div style={{ background: '#0f172a', borderRadius: 8, padding: 16, marginBottom: 20 }}>
          <p style={{ margin: '0 0 8px', color: '#f1f5f9', fontWeight: 600 }}>
            {data.violation_count} pre-trip inspection{data.violation_count !== 1 ? 's' : ''} completed in under 90 seconds
          </p>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
            <thead>
              <tr style={{ color: '#64748b' }}>
                <th style={{ textAlign: 'left', padding: '4px 0' }}>Date</th>
                <th style={{ textAlign: 'left', padding: '4px 0' }}>Duration</th>
                <th style={{ textAlign: 'left', padding: '4px 0' }}>Vehicle</th>
              </tr>
            </thead>
            <tbody>
              {data.violations.map((v, i) => (
                <tr key={i} style={{ borderTop: '1px solid #1e293b' }}>
                  <td style={{ padding: '6px 0', color: '#e2e8f0' }}>{v.start_date || '—'}</td>
                  <td style={{ padding: '6px 0', color: urgencyColor, fontWeight: 700 }}>{v.duration_seconds}s</td>
                  <td style={{ padding: '6px 0', color: '#94a3b8', fontSize: 12 }}>{v.fleet_type || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>

        {/* Safety message */}
        <div style={{ background: '#172554', borderRadius: 8, padding: 14, marginBottom: 20, fontSize: 14, color: '#93c5fd' }}>
          Amazon requires a minimum of <strong>90 seconds</strong> to properly complete a pre-trip vehicle inspection.
          A rushed inspection may miss critical safety issues — brakes, lights, tires, mirrors.
          Your safety and the safety of others on the road depends on a thorough inspection every single shift.
        </div>

        {/* Sign */}
        {step === 'review' && (
          <>
            <p style={{ color: '#94a3b8', fontSize: 13, marginBottom: 12 }}>
              By typing your full name below and clicking <strong>Sign &amp; Acknowledge</strong>, you confirm that
              you have read this notice and understand the safety requirement.
            </p>
            <input
              type="text"
              placeholder="Type your full name to sign"
              value={sigName}
              onChange={e => setSigName(e.target.value)}
              style={{
                width: '100%', boxSizing: 'border-box',
                background: '#0f172a', border: '1px solid #334155',
                borderRadius: 8, padding: '10px 14px', color: '#f1f5f9',
                fontSize: 15, marginBottom: 12,
              }}
            />
            <button
              onClick={submit}
              disabled={!sigName.trim() || submitting}
              style={{
                width: '100%',
                background: !sigName.trim() || submitting ? '#1e293b' : '#16a34a',
                color: '#fff', border: 'none', borderRadius: 8,
                padding: '12px 0', fontSize: 15, fontWeight: 700,
                cursor: !sigName.trim() || submitting ? 'default' : 'pointer',
              }}
            >
              {submitting ? 'Submitting…' : 'Sign & Acknowledge'}
            </button>
          </>
        )}
      </div>
    </div>
  );
}
