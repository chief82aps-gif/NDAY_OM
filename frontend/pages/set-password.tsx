import { useState } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

type Step = 'form' | 'done' | 'error';

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 420, marginTop: 60, boxShadow: '0 4px 24px rgba(0,0,0,0.4)' } as React.CSSProperties,
  title: { fontSize: 20, fontWeight: 700, marginBottom: 6 } as React.CSSProperties,
  subtitle: { fontSize: 14, color: '#94a3b8', marginBottom: 20 } as React.CSSProperties,
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6, fontWeight: 600 } as React.CSSProperties,
  input: { width: '100%', boxSizing: 'border-box' as const, background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: '10px 14px', color: '#f1f5f9', fontSize: 15, marginBottom: 14 },
  submit: (disabled: boolean) => ({
    width: '100%', background: disabled ? '#1e293b' : '#16a34a',
    color: '#fff', border: 'none', borderRadius: 8, padding: '14px 0',
    fontSize: 16, fontWeight: 700, cursor: disabled ? 'default' : 'pointer', marginTop: 8,
  } as React.CSSProperties),
  error: { background: '#450a0a', color: '#fca5a5', borderRadius: 8, padding: '10px 14px', fontSize: 14, marginBottom: 14 } as React.CSSProperties,
  successIcon: { fontSize: 40, marginBottom: 12 } as React.CSSProperties,
  link: { color: '#60a5fa', fontWeight: 600 } as React.CSSProperties,
};

export default function SetPasswordPage() {
  const router = useRouter();
  const { token } = router.query as { token?: string };
  const api = resolveApi();

  const [step, setStep] = useState<Step>('form');
  const [password, setPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [username, setUsername] = useState('');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setErrorMsg('');

    if (!token) {
      setErrorMsg('This link is missing its token — please use the link from your Slack DM.');
      return;
    }
    if (password.length < 6) {
      setErrorMsg('Password must be at least 6 characters.');
      return;
    }
    if (password !== confirmPassword) {
      setErrorMsg('Passwords do not match.');
      return;
    }

    setSubmitting(true);
    try {
      const res = await fetch(`${api}/auth/set-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, new_password: password }),
      });
      const data = await res.json();
      if (!res.ok) {
        throw new Error(data.detail || 'Failed to set password');
      }
      setUsername(data.username || '');
      setStep('done');
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Failed to set password');
      setStep('error');
    } finally {
      setSubmitting(false);
    }
  };

  if (step === 'done') {
    return (
      <div style={s.page}>
        <div style={{ ...s.card, textAlign: 'center' }}>
          <div style={s.successIcon}>✅</div>
          <div style={s.title}>Password set</div>
          <div style={s.subtitle}>
            {username ? `Your account (${username}) is ready.` : 'Your account is ready.'} You can log in now.
          </div>
          <Link href="/login" style={s.link}>Go to Login →</Link>
        </div>
      </div>
    );
  }

  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={s.title}>Set Your Password</div>
        <div style={s.subtitle}>Choose a password to activate your account.</div>

        {errorMsg && <div style={s.error}>{errorMsg}</div>}

        <form onSubmit={handleSubmit}>
          <label style={s.label}>New Password</label>
          <input
            style={s.input}
            type="password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            placeholder="At least 6 characters"
            autoComplete="new-password"
          />
          <label style={s.label}>Confirm Password</label>
          <input
            style={s.input}
            type="password"
            value={confirmPassword}
            onChange={(e) => setConfirmPassword(e.target.value)}
            placeholder="Re-enter password"
            autoComplete="new-password"
          />
          <button type="submit" style={s.submit(submitting)} disabled={submitting}>
            {submitting ? 'Setting password…' : 'Set Password'}
          </button>
        </form>
      </div>
    </div>
  );
}
