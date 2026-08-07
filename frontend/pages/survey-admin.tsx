import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface QuestionDraft {
  question_text: string;
  question_type: 'multiple_choice' | 'true_false' | 'free_text';
  options: string;   // comma-separated in the UI, split on save
  correct_answer: string;
  points: number;
}

interface SurveySummary {
  id: number;
  title: string;
  is_quiz: boolean;
  status: string;
  question_count: number;
  assignment_count: number;
  completed_count: number;
  created_at: string | null;
}

interface StatusRow {
  roster_id: number;
  driver_name: string;
  nudge_count: number;
  completed_at: string | null;
  score_pct: number | null;
  passed: boolean | null;
}

interface DriverOption { id: number; payroll_name: string; is_active: boolean; }

const emptyQuestion = (): QuestionDraft => ({ question_text: '', question_type: 'multiple_choice', options: '', correct_answer: '', points: 1 });

const box: React.CSSProperties = { background: '#1e293b', borderRadius: 10, padding: 20, border: '1px solid #334155', marginBottom: 16 };
const input: React.CSSProperties = { padding: '8px 10px', borderRadius: 6, border: '1px solid #334155', background: '#0f172a', color: '#e2e8f0', fontSize: 13, width: '100%', boxSizing: 'border-box' };
const btn = (primary = false): React.CSSProperties => ({
  padding: '8px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer',
  border: primary ? '1px solid #2563eb' : '1px solid #334155',
  background: primary ? '#2563eb' : '#0f172a', color: primary ? '#fff' : '#94a3b8',
});

export default function SurveyAdminPage() {
  const api = resolveApi();
  const [surveys, setSurveys] = useState<SurveySummary[] | null>(null);
  const [drivers, setDrivers] = useState<DriverOption[]>([]);
  const [error, setError] = useState('');
  const [expanded, setExpanded] = useState<number | null>(null);
  const [statusRows, setStatusRows] = useState<StatusRow[] | null>(null);

  // Create form state
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [isQuiz, setIsQuiz] = useState(false);
  const [passingScore, setPassingScore] = useState(80);
  const [questions, setQuestions] = useState<QuestionDraft[]>([emptyQuestion()]);
  const [allActive, setAllActive] = useState(true);
  const [pickedDriverIds, setPickedDriverIds] = useState<number[]>([]);
  const [creating, setCreating] = useState(false);

  const loadSurveys = useCallback(async () => {
    try {
      const res = await fetch(`${api}/surveys`);
      const data = await res.json();
      setSurveys(data.surveys ?? []);
    } catch { setError('Failed to load surveys.'); }
  }, [api]);

  useEffect(() => { loadSurveys(); }, [loadSurveys]);
  useEffect(() => {
    fetch(`${api}/drivers`).then(r => r.json()).then(d => setDrivers((d.drivers ?? []).filter((x: DriverOption) => x.is_active))).catch(() => {});
  }, [api]);

  const addQuestion = () => setQuestions(prev => [...prev, emptyQuestion()]);
  const removeQuestion = (i: number) => setQuestions(prev => prev.filter((_, idx) => idx !== i));
  const updateQuestion = (i: number, patch: Partial<QuestionDraft>) =>
    setQuestions(prev => prev.map((q, idx) => idx === i ? { ...q, ...patch } : q));

  const createAndAssign = async () => {
    setError('');
    if (!title.trim()) { setError('Title is required.'); return; }
    if (questions.some(q => !q.question_text.trim())) { setError('Every question needs text.'); return; }
    if (isQuiz && questions.some(q => q.question_type !== 'free_text' && !q.correct_answer.trim())) {
      setError('Every graded (non-free-text) question needs a correct answer.'); return;
    }
    if (!allActive && pickedDriverIds.length === 0) { setError('Pick at least one driver, or choose "All active drivers".'); return; }

    setCreating(true);
    try {
      const createRes = await fetch(`${api}/surveys`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title, description: description || null, is_quiz: isQuiz,
          passing_score_pct: isQuiz ? passingScore : null,
          questions: questions.map(q => ({
            question_text: q.question_text, question_type: q.question_type,
            options: q.question_type === 'multiple_choice' ? q.options.split(',').map(s => s.trim()).filter(Boolean) : null,
            correct_answer: q.question_type === 'free_text' ? null : q.correct_answer,
            points: q.points,
          })),
        }),
      });
      if (!createRes.ok) { const b = await createRes.json().catch(() => ({})); throw new Error(b.detail || 'Create failed.'); }
      const created = await createRes.json();

      const assignRes = await fetch(`${api}/surveys/${created.id}/assign`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(allActive ? { all_active: true } : { roster_ids: pickedDriverIds }),
      });
      if (!assignRes.ok) throw new Error('Created, but assigning drivers failed.');

      setTitle(''); setDescription(''); setIsQuiz(false); setQuestions([emptyQuestion()]); setPickedDriverIds([]);
      await loadSurveys();
    } catch (err: any) {
      setError(err.message || 'Network error.');
    } finally {
      setCreating(false);
    }
  };

  const send = async (id: number) => {
    try {
      const res = await fetch(`${api}/surveys/${id}/send`, { method: 'POST' });
      if (!res.ok) throw new Error('Send failed.');
      await loadSurveys();
      if (expanded === id) await openStatus(id);
    } catch { setError('Send failed.'); }
  };

  const close = async (id: number) => {
    try {
      await fetch(`${api}/surveys/${id}/close`, { method: 'POST' });
      await loadSurveys();
    } catch { setError('Close failed.'); }
  };

  const openStatus = async (id: number) => {
    setExpanded(id);
    try {
      const res = await fetch(`${api}/surveys/${id}/status`);
      const data = await res.json();
      setStatusRows(data.all ?? []);
    } catch { setError('Failed to load status.'); }
  };

  return (
    <ProtectedRoute allowedRoles={['owner', 'hr', 'ops_manager', 'admin']}>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <h1 style={{ margin: '0 0 4px', fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>📋 Surveys & Quizzes</h1>
          <p style={{ margin: '0 0 20px', fontSize: 13, color: '#64748b' }}>
            Ad-hoc, forced-acknowledgment surveys/quizzes. Doesn't gate routing automatically —
            use the completion status below to decide who to hold back manually.
          </p>

          {error && <div style={{ ...box, borderColor: '#7f1d1d', background: '#3b1e1e', color: '#f87171' }}>{error}</div>}

          {/* Create form */}
          <div style={box}>
            <h2 style={{ margin: '0 0 12px', fontSize: 15, fontWeight: 700, color: '#f1f5f9' }}>New survey/quiz</h2>
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <input style={input} placeholder="Title" value={title} onChange={e => setTitle(e.target.value)} />
              <textarea style={{ ...input, resize: 'vertical' }} rows={2} placeholder="Description (optional)" value={description} onChange={e => setDescription(e.target.value)} />
              <label style={{ fontSize: 13, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 8 }}>
                <input type="checkbox" checked={isQuiz} onChange={e => setIsQuiz(e.target.checked)} /> Graded quiz (has a pass/fail score)
              </label>
              {isQuiz && (
                <label style={{ fontSize: 13, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 8 }}>
                  Passing score %:
                  <input type="number" min={0} max={100} style={{ ...input, width: 80 }} value={passingScore} onChange={e => setPassingScore(Number(e.target.value))} />
                </label>
              )}
            </div>

            <h3 style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 700, color: '#94a3b8' }}>Questions</h3>
            {questions.map((q, i) => (
              <div key={i} style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: 12, marginBottom: 10 }}>
                <div style={{ display: 'flex', gap: 8, marginBottom: 8 }}>
                  <input style={input} placeholder={`Question ${i + 1}`} value={q.question_text} onChange={e => updateQuestion(i, { question_text: e.target.value })} />
                  <select style={{ ...input, width: 160 }} value={q.question_type} onChange={e => updateQuestion(i, { question_type: e.target.value as QuestionDraft['question_type'] })}>
                    <option value="multiple_choice">Multiple choice</option>
                    <option value="true_false">True/False</option>
                    <option value="free_text">Free text (ungraded)</option>
                  </select>
                  <button style={btn()} onClick={() => removeQuestion(i)}>✕</button>
                </div>
                {q.question_type === 'multiple_choice' && (
                  <input style={{ ...input, marginBottom: 8 }} placeholder="Options, comma-separated" value={q.options} onChange={e => updateQuestion(i, { options: e.target.value })} />
                )}
                {q.question_type !== 'free_text' && isQuiz && (
                  <input style={{ ...input, marginBottom: 8 }} placeholder="Correct answer (must match an option exactly for multiple choice, or true/false)" value={q.correct_answer} onChange={e => updateQuestion(i, { correct_answer: e.target.value })} />
                )}
              </div>
            ))}
            <button style={btn()} onClick={addQuestion}>+ Add question</button>

            <h3 style={{ margin: '18px 0 8px', fontSize: 13, fontWeight: 700, color: '#94a3b8' }}>Assign to</h3>
            <label style={{ fontSize: 13, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <input type="radio" checked={allActive} onChange={() => setAllActive(true)} /> All active drivers
            </label>
            <label style={{ fontSize: 13, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
              <input type="radio" checked={!allActive} onChange={() => setAllActive(false)} /> Specific drivers
            </label>
            {!allActive && (
              <div style={{ maxHeight: 160, overflowY: 'auto', background: '#0f172a', border: '1px solid #334155', borderRadius: 6, padding: 8, marginBottom: 8 }}>
                {drivers.map(d => (
                  <label key={d.id} style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: '#e2e8f0', padding: '4px 0' }}>
                    <input type="checkbox" checked={pickedDriverIds.includes(d.id)}
                      onChange={e => setPickedDriverIds(prev => e.target.checked ? [...prev, d.id] : prev.filter(x => x !== d.id))} />
                    {d.payroll_name}
                  </label>
                ))}
              </div>
            )}

            <button style={btn(true)} onClick={createAndAssign} disabled={creating}>
              {creating ? 'Creating…' : 'Create & Assign'}
            </button>
          </div>

          {/* List */}
          {surveys && surveys.map(s => (
            <div key={s.id} style={box}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <span style={{ fontWeight: 700, color: '#f1f5f9' }}>{s.is_quiz ? '📝' : '📋'} {s.title}</span>
                  <span style={{ fontSize: 12, color: '#64748b', marginLeft: 8 }}>
                    {s.status} · {s.completed_count}/{s.assignment_count} completed · {s.question_count} question{s.question_count === 1 ? '' : 's'}
                  </span>
                </div>
                <div style={{ display: 'flex', gap: 8 }}>
                  <button style={btn()} onClick={() => openStatus(s.id)}>Status</button>
                  {s.status !== 'closed' && <button style={btn(true)} onClick={() => send(s.id)}>Send / Nudge Now</button>}
                  {s.status !== 'closed' && <button style={btn()} onClick={() => close(s.id)}>Close</button>}
                </div>
              </div>

              {expanded === s.id && statusRows && (
                <div style={{ marginTop: 12, overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ color: '#64748b', textAlign: 'left' }}>
                        <th style={{ padding: '4px 8px' }}>Driver</th>
                        <th style={{ padding: '4px 8px' }}>Nudges</th>
                        <th style={{ padding: '4px 8px' }}>Completed</th>
                        <th style={{ padding: '4px 8px' }}>Score</th>
                      </tr>
                    </thead>
                    <tbody>
                      {statusRows.map(r => (
                        <tr key={r.roster_id} style={{ borderTop: '1px solid #334155' }}>
                          <td style={{ padding: '6px 8px', color: '#e2e8f0' }}>{r.driver_name}</td>
                          <td style={{ padding: '6px 8px', color: '#94a3b8' }}>{r.nudge_count}</td>
                          <td style={{ padding: '6px 8px', color: r.completed_at ? '#4ade80' : '#f87171' }}>{r.completed_at ? '✅' : '— incomplete'}</td>
                          <td style={{ padding: '6px 8px', color: '#94a3b8' }}>{r.score_pct !== null ? `${r.score_pct}%${r.passed ? ' ✅' : ' ❌'}` : '—'}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
