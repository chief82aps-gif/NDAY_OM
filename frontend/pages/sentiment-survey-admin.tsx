import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface QuestionRating {
  key: string;
  text: string;
  rating: number | null;
  note: string | null;
}

interface SentimentResponse {
  id: number;
  driver_name: string;
  survey_date: string;
  submitted_at: string;
  feeling: string | null;
  van_equipment_issues: string | null;
  suggestions: string | null;
  treatment_concerns: string | null;
  ratings: QuestionRating[];
  responded_at: string | null;
  response_mode: string | null;
  response_text: string | null;
  has_slack_link: boolean;
}

type BlakeMode = 'noted' | 'noted_with_reason' | 'decline_with_reason';

const BLAKE_MODE_LABELS: Record<BlakeMode, string> = {
  noted: 'Noted (no elaboration)',
  noted_with_reason: 'Noted + reason',
  decline_with_reason: "Thank you for the suggestion... here's why",
};

interface QuestionStat {
  key: string;
  text: string;
  responses: number;
  average: number | null;
  favorable_rate: number | null;
}

interface AdminReport {
  month: string;
  response_count: number;
  question_stats: QuestionStat[];
  responses: SentimentResponse[];
}

function fmtDateTime(t: string | null) {
  if (!t) return '—';
  try {
    return new Date(t).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' });
  } catch { return t; }
}

function currentMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}`;
}

export default function SentimentSurveyAdminPage() {
  const api = resolveApi();
  const [month, setMonth] = useState(currentMonth());
  const [report, setReport] = useState<AdminReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [composerFor, setComposerFor] = useState<number | null>(null);
  const [composerMode, setComposerMode] = useState<BlakeMode>('decline_with_reason');
  const [composerReason, setComposerReason] = useState('');
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const res = await fetch(`${api}/sentiment-survey/admin-report?month=${month}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.status === 403) {
        setError('This report is restricted to owner/HR roles.');
        setReport(null);
        return;
      }
      if (!res.ok) { setError('Failed to load.'); return; }
      const data: AdminReport = await res.json();
      setReport(data);
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api, month]);

  useEffect(() => { load(); }, [load]);

  const responses = report?.responses ?? [];

  const openComposer = (id: number) => {
    setComposerFor(id);
    setComposerMode('decline_with_reason');
    setComposerReason('');
    setSendError('');
  };

  const closeComposer = () => {
    setComposerFor(null);
    setSendError('');
  };

  const sendBlakeResponse = async (responseId: number) => {
    if (composerMode !== 'noted' && !composerReason.trim()) {
      setSendError('A reason is required for this mode.');
      return;
    }
    setSending(true);
    setSendError('');
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const res = await fetch(`${api}/sentiment-survey/respond/${responseId}`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ mode: composerMode, reason: composerReason.trim() || null }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setSendError(d.detail ?? 'Failed to send.');
        return;
      }
      setComposerFor(null);
      await load();
    } catch {
      setSendError('Network error.');
    } finally {
      setSending(false);
    }
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Sentiment Survey — Monthly Report</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Restricted to owner/HR. Driver identity is revealed here only — nowhere else in the app shows it.
              Monthly, not daily — it takes drivers several days to answer, so a single day is usually near-empty.
            </p>
          </div>

          <div style={{ marginBottom: 20 }}>
            <input
              type="month"
              value={month}
              onChange={e => setMonth(e.target.value)}
              style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14 }}
            />
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {!loading && !error && report && (
            <>
              <div style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 20, border: '1px solid #334155' }}>
                <div style={{ fontSize: 13, color: '#94a3b8', marginBottom: 12 }}>
                  {report.response_count} response{report.response_count === 1 ? '' : 's'} this month
                </div>
                <div style={{ overflowX: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ color: '#64748b', textAlign: 'left', borderBottom: '1px solid #334155' }}>
                        <th style={{ padding: '4px 8px', fontWeight: 600 }}>Question</th>
                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>Responses</th>
                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>Avg (1-5)</th>
                        <th style={{ padding: '4px 8px', fontWeight: 600, textAlign: 'right' }}>% Favorable</th>
                      </tr>
                    </thead>
                    <tbody>
                      {report.question_stats.map(q => (
                        <tr key={q.key} style={{ borderBottom: '1px solid #1e293b' }}>
                          <td style={{ padding: '8px' }}>{q.text}</td>
                          <td style={{ padding: '8px', textAlign: 'right', color: '#94a3b8' }}>{q.responses}</td>
                          <td style={{ padding: '8px', textAlign: 'right', fontWeight: 700, color: q.average === null ? '#64748b' : q.average >= 4 ? '#4ade80' : q.average >= 3 ? '#facc15' : '#f87171' }}>
                            {q.average ?? '—'}
                          </td>
                          <td style={{ padding: '8px', textAlign: 'right', color: '#94a3b8' }}>
                            {q.favorable_rate === null ? '—' : `${q.favorable_rate}%`}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {responses.length === 0 && <p style={{ color: '#64748b' }}>No responses for this month.</p>}

              {responses.map(r => (
                <div key={r.id} style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 12, border: '1px solid #334155' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                    <span style={{ fontWeight: 700, color: '#f1f5f9' }}>{r.driver_name}</span>
                    <span style={{ fontSize: 12, color: '#64748b' }}>{fmtDateTime(r.submitted_at)}</span>
                  </div>

                  {r.ratings.map(rt => (
                    <div key={rt.key} style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, display: 'flex', gap: 8, alignItems: 'baseline' }}>
                        <span>{rt.text}</span>
                        {rt.rating !== null && (
                          <span style={{ color: rt.rating >= 4 ? '#4ade80' : rt.rating >= 3 ? '#facc15' : '#f87171', fontSize: 13 }}>
                            {rt.rating}/5
                          </span>
                        )}
                      </div>
                      {rt.note && <div style={{ fontSize: 14, color: '#e2e8f0' }}>{rt.note}</div>}
                    </div>
                  ))}

                  {r.feeling && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase' }}>Feeling</div>
                      <div style={{ fontSize: 14, color: '#e2e8f0' }}>{r.feeling}</div>
                    </div>
                  )}
                  {r.van_equipment_issues && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase' }}>Van / Equipment</div>
                      <div style={{ fontSize: 14, color: '#e2e8f0' }}>{r.van_equipment_issues}</div>
                    </div>
                  )}
                  {r.suggestions && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, color: '#64748b', fontWeight: 700, textTransform: 'uppercase' }}>Suggestions</div>
                      <div style={{ fontSize: 14, color: '#e2e8f0' }}>{r.suggestions}</div>
                    </div>
                  )}
                  {r.treatment_concerns && (
                    <div style={{ marginBottom: 8 }}>
                      <div style={{ fontSize: 11, color: '#f59e0b', fontWeight: 700, textTransform: 'uppercase' }}>Treatment Concerns</div>
                      <div style={{ fontSize: 14, color: '#e2e8f0' }}>{r.treatment_concerns}</div>
                    </div>
                  )}

                  <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid #334155' }}>
                    {r.responded_at ? (
                      <div style={{ fontSize: 13, color: '#4ade80' }}>
                        ✓ Responded as Blake ({BLAKE_MODE_LABELS[r.response_mode as BlakeMode] ?? r.response_mode}) — {fmtDateTime(r.responded_at)}
                        <div style={{ marginTop: 4, fontSize: 13, color: '#94a3b8', fontStyle: 'italic' }}>&ldquo;{r.response_text}&rdquo;</div>
                      </div>
                    ) : composerFor === r.id ? (
                      <div style={{ background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: 12 }}>
                        <select
                          value={composerMode}
                          onChange={e => setComposerMode(e.target.value as BlakeMode)}
                          style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13, marginBottom: 8, width: '100%' }}
                        >
                          {(Object.keys(BLAKE_MODE_LABELS) as BlakeMode[]).map(m => (
                            <option key={m} value={m}>{BLAKE_MODE_LABELS[m]}</option>
                          ))}
                        </select>
                        {composerMode !== 'noted' && (
                          <textarea
                            value={composerReason}
                            onChange={e => setComposerReason(e.target.value)}
                            placeholder="Reason / body of the response..."
                            style={{ width: '100%', boxSizing: 'border-box', background: '#1e293b', border: '1px solid #334155', borderRadius: 6, padding: '8px 10px', color: '#e2e8f0', fontSize: 13, minHeight: 70, marginBottom: 8, fontFamily: 'inherit' }}
                          />
                        )}
                        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 8 }}>
                          Preview: &ldquo;{composerMode === 'noted' ? 'Noted.' : composerMode === 'noted_with_reason' ? `Noted. ${composerReason || '...'}` : `Thank you for the suggestion, I see where you're coming from — unfortunately we cannot do this, and here's why: ${composerReason || '...'}`}&rdquo;
                        </div>
                        {sendError && <div style={{ color: '#f87171', fontSize: 12, marginBottom: 8 }}>{sendError}</div>}
                        <div style={{ display: 'flex', gap: 8 }}>
                          <button
                            onClick={() => sendBlakeResponse(r.id)}
                            disabled={sending}
                            style={{ background: '#16a34a', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 13, fontWeight: 600, cursor: sending ? 'default' : 'pointer' }}
                          >
                            {sending ? 'Sending…' : 'Send as Blake'}
                          </button>
                          <button
                            onClick={closeComposer}
                            style={{ background: 'transparent', color: '#94a3b8', border: '1px solid #334155', borderRadius: 6, padding: '6px 14px', fontSize: 13, cursor: 'pointer' }}
                          >
                            Cancel
                          </button>
                        </div>
                      </div>
                    ) : r.has_slack_link ? (
                      <button
                        onClick={() => openComposer(r.id)}
                        style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 14px', fontSize: 13, cursor: 'pointer' }}
                      >
                        💬 Respond as Blake
                      </button>
                    ) : (
                      <span style={{ fontSize: 12, color: '#64748b' }}>Driver not Slack-linked — can&apos;t reply directly.</span>
                    )}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
