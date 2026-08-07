import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface Question {
  id: number;
  question_text: string;
  question_type: 'multiple_choice' | 'true_false' | 'free_text';
  options: string[] | null;
}

interface SurveyData {
  survey_id: number;
  title: string;
  description: string | null;
  is_quiz: boolean;
  driver_name: string;
  already_submitted: boolean;
  score_pct: number | null;
  passed: boolean | null;
  questions: Question[];
}

export default function SurveyPage() {
  const router = useRouter();
  const api = resolveApi();
  const [token, setToken] = useState<string | null>(null);
  const [data, setData] = useState<SurveyData | null>(null);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<{ score_pct: number | null; passed: boolean | null } | null>(null);

  useEffect(() => {
    if (!router.isReady) return;
    const t = typeof router.query.token === 'string' ? router.query.token : null;
    setToken(t);
    if (!t) {
      setError('This link is missing its token.');
      setLoading(false);
      return;
    }
    fetch(`${api}/surveys/lookup?token=${encodeURIComponent(t)}`)
      .then(async res => {
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw new Error(body.detail || 'This link is invalid or has expired.');
        }
        return res.json();
      })
      .then((d: SurveyData) => setData(d))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  }, [router.isReady, router.query.token, api]);

  const submit = async () => {
    if (!token || !data) return;
    const unanswered = data.questions.filter(q => !answers[q.id]?.trim());
    if (unanswered.length > 0) {
      setError(`Please answer all questions — ${unanswered.length} remaining.`);
      return;
    }
    setSubmitting(true);
    setError('');
    try {
      const stringAnswers: Record<string, string> = {};
      Object.entries(answers).forEach(([k, v]) => { stringAnswers[k] = v; });
      const res = await fetch(`${api}/surveys/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token, answers: stringAnswers }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        throw new Error(body.detail || 'Submit failed.');
      }
      const body = await res.json();
      setResult({ score_pct: body.score_pct, passed: body.passed });
    } catch (err: any) {
      setError(err.message || 'Network error.');
    } finally {
      setSubmitting(false);
    }
  };

  const cardStyle: React.CSSProperties = {
    background: '#1e293b', borderRadius: 12, padding: 24, border: '1px solid #334155', marginBottom: 16,
  };

  return (
    <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 20px' }}>
      <div style={{ maxWidth: 640, margin: '0 auto' }}>
        {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

        {!loading && error && !data && (
          <div style={{ ...cardStyle, borderColor: '#7f1d1d', background: '#3b1e1e', color: '#f87171' }}>{error}</div>
        )}

        {!loading && data && (
          <>
            <div style={{ marginBottom: 20 }}>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>
                {data.is_quiz ? '📝' : '📋'} {data.title}
              </h1>
              {data.description && <p style={{ margin: '6px 0 0', fontSize: 14, color: '#94a3b8' }}>{data.description}</p>}
              <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>Hi {data.driver_name.split(' ')[0]} — please complete this below.</p>
            </div>

            {(data.already_submitted || result) && (
              <div style={{ ...cardStyle, borderColor: '#14532d', background: '#0f2418' }}>
                <p style={{ margin: 0, fontWeight: 700, color: '#4ade80' }}>✅ Already submitted — thanks!</p>
                {(result?.score_pct ?? data.score_pct) !== null && data.is_quiz && (
                  <p style={{ margin: '8px 0 0', fontSize: 14, color: '#e2e8f0' }}>
                    Score: {(result?.score_pct ?? data.score_pct)}% — {(result?.passed ?? data.passed) ? 'Passed' : 'Did not pass'}
                  </p>
                )}
              </div>
            )}

            {!data.already_submitted && !result && (
              <>
                {data.questions.map((q, i) => (
                  <div key={q.id} style={cardStyle}>
                    <p style={{ margin: '0 0 12px', fontWeight: 600, color: '#f1f5f9' }}>{i + 1}. {q.question_text}</p>
                    {q.question_type === 'true_false' && (
                      <div style={{ display: 'flex', gap: 8 }}>
                        {['true', 'false'].map(opt => (
                          <button key={opt} onClick={() => setAnswers(prev => ({ ...prev, [q.id]: opt }))}
                            style={{
                              flex: 1, padding: '10px 16px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer',
                              border: answers[q.id] === opt ? '1px solid #2563eb' : '1px solid #334155',
                              background: answers[q.id] === opt ? '#2563eb' : '#0f172a',
                              color: answers[q.id] === opt ? '#fff' : '#94a3b8', textTransform: 'capitalize',
                            }}>{opt}</button>
                        ))}
                      </div>
                    )}
                    {q.question_type === 'multiple_choice' && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                        {(q.options || []).map(opt => (
                          <button key={opt} onClick={() => setAnswers(prev => ({ ...prev, [q.id]: opt }))}
                            style={{
                              textAlign: 'left', padding: '10px 16px', borderRadius: 8, fontSize: 14, fontWeight: 600, cursor: 'pointer',
                              border: answers[q.id] === opt ? '1px solid #2563eb' : '1px solid #334155',
                              background: answers[q.id] === opt ? '#2563eb' : '#0f172a',
                              color: answers[q.id] === opt ? '#fff' : '#94a3b8',
                            }}>{opt}</button>
                        ))}
                      </div>
                    )}
                    {q.question_type === 'free_text' && (
                      <textarea
                        value={answers[q.id] || ''}
                        onChange={e => setAnswers(prev => ({ ...prev, [q.id]: e.target.value }))}
                        rows={3}
                        placeholder="Type your answer…"
                        style={{ width: '100%', boxSizing: 'border-box', padding: 10, borderRadius: 8, border: '1px solid #334155', background: '#0f172a', color: '#e2e8f0', fontSize: 14, fontFamily: 'inherit', resize: 'vertical' }}
                      />
                    )}
                  </div>
                ))}

                {error && (
                  <div style={{ ...cardStyle, borderColor: '#7f1d1d', background: '#3b1e1e', color: '#f87171' }}>{error}</div>
                )}

                <button
                  onClick={submit}
                  disabled={submitting}
                  style={{
                    width: '100%', padding: '14px', borderRadius: 8, fontSize: 15, fontWeight: 700, cursor: submitting ? 'default' : 'pointer',
                    border: 'none', background: submitting ? '#1e293b' : '#2563eb', color: '#fff', opacity: submitting ? 0.6 : 1,
                  }}
                >
                  {submitting ? 'Submitting…' : 'Submit'}
                </button>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}
