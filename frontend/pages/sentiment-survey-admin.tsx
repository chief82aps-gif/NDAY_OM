import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface SentimentResponse {
  id: number;
  driver_name: string;
  submitted_at: string;
  feeling: string | null;
  van_equipment_issues: string | null;
  suggestions: string | null;
  treatment_concerns: string | null;
}

function fmtTime(t: string | null) {
  if (!t) return '—';
  try { return new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); }
  catch { return t; }
}

export default function SentimentSurveyAdminPage() {
  const api = resolveApi();
  const todayIso = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayIso);
  const [responses, setResponses] = useState<SentimentResponse[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const res = await fetch(`${api}/sentiment-survey/admin-report?survey_date=${date}`, {
        headers: token ? { Authorization: `Bearer ${token}` } : {},
      });
      if (res.status === 403) {
        setError('This report is restricted to owner/HR roles.');
        setResponses([]);
        return;
      }
      if (!res.ok) { setError('Failed to load.'); return; }
      const data = await res.json();
      setResponses(data.responses ?? []);
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  }, [api, date]);

  useEffect(() => { load(); }, [load]);

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Sentiment Survey — Admin Report</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Restricted to owner/HR. Driver identity is revealed here only — nowhere else in the app shows it.
            </p>
          </div>

          <div style={{ marginBottom: 20 }}>
            <input
              type="date"
              value={date}
              onChange={e => setDate(e.target.value)}
              style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14 }}
            />
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {!loading && !error && responses.length === 0 && (
            <p style={{ color: '#64748b' }}>No responses for this date.</p>
          )}

          {!loading && responses.map(r => (
            <div key={r.id} style={{ background: '#1e293b', borderRadius: 10, padding: 16, marginBottom: 12, border: '1px solid #334155' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 10 }}>
                <span style={{ fontWeight: 700, color: '#f1f5f9' }}>{r.driver_name}</span>
                <span style={{ fontSize: 12, color: '#64748b' }}>{fmtTime(r.submitted_at)}</span>
              </div>
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
                <div>
                  <div style={{ fontSize: 11, color: '#f59e0b', fontWeight: 700, textTransform: 'uppercase' }}>Treatment Concerns</div>
                  <div style={{ fontSize: 14, color: '#e2e8f0' }}>{r.treatment_concerns}</div>
                </div>
              )}
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
