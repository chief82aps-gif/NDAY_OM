import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';

function resolveApi(): string {
  // Relative in production — proxied to the Render backend by next.config.ts's
  // rewrites (same fix as eod.tsx; direct browser fetches to onrender.com
  // are unreliable from some networks, surfacing as "Failed to fetch").
  if (typeof window !== 'undefined' && window.location.hostname === 'localhost') {
    return 'http://127.0.0.1:8001';
  }
  return '';
}

type Step = 'loading' | 'form' | 'done' | 'already_done' | 'error';

interface SentimentQuestion {
  key: string;
  text: string;
}

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 520, boxShadow: '0 4px 24px rgba(0,0,0,0.4)' } as React.CSSProperties,
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6, fontWeight: 600 } as React.CSSProperties,
  textarea: {
    width: '100%', boxSizing: 'border-box' as const, background: '#0f172a', border: '1px solid #334155',
    borderRadius: 8, padding: '10px 14px', color: '#f1f5f9', fontSize: 14, marginBottom: 18,
    minHeight: 70, fontFamily: 'inherit', resize: 'vertical' as const,
  },
  questionBlock: { background: '#0f172a', border: '1px solid #334155', borderRadius: 10, padding: 14, marginBottom: 14 } as React.CSSProperties,
  questionText: { fontSize: 14, color: '#f1f5f9', margin: '0 0 10px', fontWeight: 600 } as React.CSSProperties,
  ratingRow: { display: 'flex', gap: 8, marginBottom: 10 } as React.CSSProperties,
  ratingBtn: (active: boolean) => ({
    flex: 1, padding: '8px 0', borderRadius: 6, border: active ? '1px solid #16a34a' : '1px solid #334155',
    background: active ? '#16a34a' : '#1e293b', color: active ? '#fff' : '#94a3b8',
    fontSize: 14, fontWeight: 700, cursor: 'pointer',
  } as React.CSSProperties),
  ratingCaption: { display: 'flex', justifyContent: 'space-between', fontSize: 10, color: '#64748b', marginBottom: 10 } as React.CSSProperties,
  noteInput: {
    width: '100%', boxSizing: 'border-box' as const, background: '#1e293b', border: '1px solid #334155',
    borderRadius: 6, padding: '8px 10px', color: '#f1f5f9', fontSize: 13, fontFamily: 'inherit',
    resize: 'vertical' as const, minHeight: 44,
  } as React.CSSProperties,
  submit: (disabled: boolean) => ({
    width: '100%', background: disabled ? '#1e293b' : '#16a34a',
    color: '#fff', border: 'none', borderRadius: 8, padding: '14px 0',
    fontSize: 16, fontWeight: 700, cursor: disabled ? 'default' : 'pointer',
  } as React.CSSProperties),
};

export default function SentimentSurveyPage() {
  const router = useRouter();
  const { token } = router.query as { token?: string };
  const api = resolveApi();

  const [step, setStep] = useState<Step>('loading');
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const [feeling, setFeeling] = useState('');
  const [vanEquipmentIssues, setVanEquipmentIssues] = useState('');
  const [suggestions, setSuggestions] = useState('');
  const [treatmentConcerns, setTreatmentConcerns] = useState('');

  const [questions, setQuestions] = useState<SentimentQuestion[]>([]);
  const [ratings, setRatings] = useState<Record<string, number | null>>({});
  const [notes, setNotes] = useState<Record<string, string>>({});

  useEffect(() => {
    fetch(`${api}/sentiment-survey/questions`)
      .then(r => r.json())
      .then((data: { questions: SentimentQuestion[] }) => setQuestions(data.questions ?? []))
      .catch(() => setQuestions([]));
  }, [api]);

  useEffect(() => {
    if (!token) return;
    fetch(`${api}/sentiment-survey/status-by-token?token=${encodeURIComponent(token)}`)
      .then(r => {
        if (!r.ok) throw new Error('invalid');
        return r.json();
      })
      .then((data: { already_submitted: boolean }) => {
        setStep(data.already_submitted ? 'already_done' : 'form');
      })
      .catch(() => {
        setErrorMsg('This link has expired or is invalid.');
        setStep('error');
      });
  }, [token, api]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!token) return;
    setSubmitting(true);
    setErrorMsg('');
    try {
      const ratingPayload: Record<string, number | string | null> = {};
      for (const q of questions) {
        ratingPayload[`rating_${q.key}`] = ratings[q.key] ?? null;
        ratingPayload[`note_${q.key}`] = (notes[q.key] ?? '').trim() || null;
      }

      const res = await fetch(`${api}/sentiment-survey/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          feeling: feeling.trim() || null,
          van_equipment_issues: vanEquipmentIssues.trim() || null,
          suggestions: suggestions.trim() || null,
          treatment_concerns: treatmentConcerns.trim() || null,
          ...ratingPayload,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? 'Could not submit.');
      }
      setStep('done');
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Submission failed.');
    } finally {
      setSubmitting(false);
    }
  }

  if (step === 'loading') return (
    <div style={s.page}><div style={{ color: '#94a3b8' }}>Loading…</div></div>
  );

  if (step === 'error') return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ color: '#f87171', margin: '0 0 8px' }}>Something went wrong</h2>
        <p style={{ color: '#94a3b8' }}>{errorMsg}</p>
      </div>
    </div>
  );

  if (step === 'already_done') return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Already Submitted</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>Thanks — you already checked in for this shift.</p>
      </div>
    </div>
  );

  if (step === 'done') return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Thanks for sharing</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          Your check-in has been recorded. Drive safe out there.
        </p>
      </div>
    </div>
  );

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ margin: '0 0 4px', color: '#f1f5f9' }}>Quick Check-In</h2>
        <p style={{ margin: '0 0 20px', color: '#64748b', fontSize: 13 }}>
          Totally optional, and not shown with your name attached — just an honest read on how things are going.
          Leave anything blank you'd rather skip.
        </p>
        <form onSubmit={handleSubmit}>
          {questions.map(q => (
            <div key={q.key} style={s.questionBlock}>
              <p style={s.questionText}>{q.text}</p>
              <div style={s.ratingRow}>
                {[1, 2, 3, 4, 5].map(n => (
                  <button
                    key={n}
                    type="button"
                    style={s.ratingBtn(ratings[q.key] === n)}
                    onClick={() => setRatings(p => ({ ...p, [q.key]: p[q.key] === n ? null : n }))}
                  >
                    {n}
                  </button>
                ))}
              </div>
              <div style={s.ratingCaption}>
                <span>Least positive</span>
                <span>Most positive</span>
              </div>
              <textarea
                style={s.noteInput}
                placeholder="Anything you'd like to add? (optional)"
                value={notes[q.key] ?? ''}
                onChange={e => setNotes(p => ({ ...p, [q.key]: e.target.value }))}
              />
            </div>
          ))}

          <label style={s.label}>How are you feeling about work today? Anything on your mind?</label>
          <textarea style={s.textarea} value={feeling} onChange={e => setFeeling(e.target.value)} />

          <label style={s.label}>Any issues with your van, equipment, or route today?</label>
          <textarea style={s.textarea} value={vanEquipmentIssues} onChange={e => setVanEquipmentIssues(e.target.value)} />

          <label style={s.label}>Any suggestions for how we could do things better?</label>
          <textarea style={s.textarea} value={suggestions} onChange={e => setSuggestions(e.target.value)} />

          <label style={s.label}>Anything else you'd like management to know — including how you've been treated?</label>
          <textarea style={s.textarea} value={treatmentConcerns} onChange={e => setTreatmentConcerns(e.target.value)} />

          {errorMsg && <p style={{ color: '#f87171', fontSize: 13, margin: '-8px 0 14px' }}>{errorMsg}</p>}
          <button type="submit" disabled={submitting} style={s.submit(submitting)}>
            {submitting ? 'Submitting…' : 'Submit'}
          </button>
        </form>
      </div>
    </div>
  );
}
