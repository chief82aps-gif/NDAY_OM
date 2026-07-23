import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/router';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface StatusInfo {
  transporter_id: string;
  transporter_name: string;
  week: string;
  stage: number;
  video_watched_at: string | null;
}

type Step = 'loading' | 'video' | 'sign' | 'done' | 'error';

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 560, boxShadow: '0 4px 24px rgba(0,0,0,0.4)' } as React.CSSProperties,
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6, fontWeight: 600 } as React.CSSProperties,
  input: { width: '100%', boxSizing: 'border-box' as const, background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: '10px 14px', color: '#f1f5f9', fontSize: 15, marginBottom: 14 },
  submit: (disabled: boolean) => ({
    width: '100%', background: disabled ? '#1e293b' : '#16a34a',
    color: '#fff', border: 'none', borderRadius: 8, padding: '14px 0',
    fontSize: 16, fontWeight: 700, cursor: disabled ? 'default' : 'pointer', marginTop: 8,
  } as React.CSSProperties),
};

export default function DvicTrainingPage() {
  const router = useRouter();
  const { token } = router.query as { token?: string };
  const api = resolveApi();

  const [step, setStep] = useState<Step>('loading');
  const [info, setInfo] = useState<StatusInfo | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [signatureName, setSignatureName] = useState('');
  const [signErr, setSignErr] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const videoRef = useRef<HTMLVideoElement>(null);

  useEffect(() => {
    if (!token) return;
    fetch(`${api}/dvic/training-status-by-token?token=${encodeURIComponent(token)}`)
      .then(r => {
        if (!r.ok) throw new Error(r.status === 401 ? 'expired' : 'invalid');
        return r.json();
      })
      .then((data: StatusInfo) => {
        setInfo(data);
        setStep(data.video_watched_at ? 'sign' : 'video');
      })
      .catch(err => {
        setErrorMsg(
          err.message === 'expired'
            ? 'This link has expired. Ask dispatch to send a new one.'
            : 'This link is invalid.'
        );
        setStep('error');
      });
  }, [token, api]);

  async function handleVideoEnded() {
    if (!token) return;
    try {
      await fetch(`${api}/dvic/training-video-watched`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token }),
      });
    } catch {
      // Non-fatal — the driver can still proceed; the ack endpoint re-checks server-side.
    }
    setStep('sign');
  }

  async function handleSign(e: React.FormEvent) {
    e.preventDefault();
    setSignErr('');
    if (!info) return;
    const normalize = (v: string) => v.trim().toLowerCase().replace(/\s+/g, ' ');
    if (!signatureName.trim()) { setSignErr('Please type your full name to sign.'); return; }
    if (normalize(signatureName) !== normalize(info.transporter_name)) {
      setSignErr(`Name must match exactly: "${info.transporter_name}"`);
      return;
    }
    setSubmitting(true);
    try {
      const res = await fetch(`${api}/dvic/acknowledge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          transporter_id: info.transporter_id,
          week: info.week,
          signature_name: signatureName.trim(),
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? 'Could not submit acknowledgment.');
      }
      setStep('done');
    } catch (err: unknown) {
      setSignErr(err instanceof Error ? err.message : 'Submission failed.');
    } finally {
      setSubmitting(false);
    }
  }

  if (step === 'loading') return (
    <div style={s.page}><div style={{ color: '#94a3b8' }}>Verifying your link…</div></div>
  );

  if (step === 'error') return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ color: '#f87171', margin: '0 0 8px' }}>Something went wrong</h2>
        <p style={{ color: '#94a3b8' }}>{errorMsg}</p>
      </div>
    </div>
  );

  if (step === 'done') return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Thanks, {info?.transporter_name?.split(' ')[0]}</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          Your acknowledgment has been recorded. Drive safe out there.
        </p>
      </div>
    </div>
  );

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ margin: '0 0 4px', color: '#f1f5f9' }}>DVIC Safety Training</h2>
        <p style={{ margin: '0 0 20px', color: '#64748b', fontSize: 13 }}>
          {info?.transporter_name} · Week {info?.week}
        </p>

        {step === 'video' && (
          <>
            <p style={{ color: '#cbd5e1', fontSize: 14, marginBottom: 14 }}>
              Please watch this short training video all the way through — it'll unlock the acknowledgment
              once it finishes.
            </p>
            <video
              ref={videoRef}
              controls
              controlsList="nodownload noplaybackrate"
              onEnded={handleVideoEnded}
              style={{ width: '100%', borderRadius: 8, background: '#000' }}
              src={`${api}/dvic/training-video-url`}
            />
          </>
        )}

        {step === 'sign' && (
          <form onSubmit={handleSign}>
            <div style={{ background: '#0f172a', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#94a3b8', marginBottom: 14 }}>
              ✅ Video watched. Type your full name below to acknowledge.
            </div>
            <label style={s.label}>Your Full Name</label>
            <input
              style={s.input}
              value={signatureName}
              onChange={e => setSignatureName(e.target.value)}
              placeholder={info?.transporter_name}
              autoComplete="name"
            />
            {signErr && <p style={{ color: '#f87171', fontSize: 13, margin: '-8px 0 10px' }}>{signErr}</p>}
            <button type="submit" disabled={submitting} style={s.submit(submitting)}>
              {submitting ? 'Submitting…' : 'Acknowledge →'}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}
