import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface EodResponse {
  id: number;
  survey_date: string;
  submitted_at: string;
  driver_name: string;
  transporter_id: string;
  van_number: string | null;
  wave: string | null;
  role: string | null;
  clocked_in_on_time: boolean | null;
  actual_clock_in_time: string | null;
  clock_in_reason: string | null;
  van_issues: boolean;
  van_issue_description: string | null;
  incident_occurred: boolean;
  incident_report_filed: boolean | null;
  injury_occurred: boolean;
  injury_report_submitted: boolean | null;
  medical_review_completed: boolean | null;
  post_trip_dvic_completed: boolean | null;
  gas_level: string | null;
  packages_rts: number;
  route_issues: boolean;
  route_issue_description: string | null;
  performed_sweep: boolean;
  sweep_details: string | null;
  took_lunch: boolean;
  lunch_clock_out: string | null;
  lunch_clock_in: string | null;
  clock_out_time: string | null;
  pockets_checked: boolean | null;
  needs_management_contact: boolean;
  all_equipment_present: boolean | null;
  missing_equipment: string | null;
  flags: string[];
}

interface MissingDriver {
  driver_name: string;
  van: string | null;
  wave: string | null;
}

function flagBadge(flag: string) {
  const map: Record<string, [string, string]> = {
    incident:    ['Incident', '#dc2626'],
    injury:      ['Injury', '#dc2626'],
    van_issue:   ['Van Issue', '#f59e0b'],
    mgmt_contact: ['Needs Mgmt', '#f59e0b'],
    dvic_missed: ['DVIC Missed', '#f97316'],
  };
  const [label, color] = map[flag] ?? [flag, '#64748b'];
  return (
    <span key={flag} style={{
      background: color + '22', color, border: `1px solid ${color}55`,
      borderRadius: 6, padding: '2px 8px', fontSize: 11, fontWeight: 700, marginRight: 4,
    }}>{label}</span>
  );
}

function yn(v: boolean | null, yesLabel = 'Yes', noLabel = 'No') {
  if (v === null || v === undefined) return <span style={{ color: '#475569' }}>—</span>;
  return <span style={{ color: v ? '#4ade80' : '#f87171', fontWeight: 600 }}>{v ? yesLabel : noLabel}</span>;
}

function fmt(t: string | null) {
  if (!t) return '—';
  try { return new Date(t).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' }); }
  catch { return t; }
}

export default function EodAdminPage() {
  const api = resolveApi();
  const todayIso = new Date().toISOString().slice(0, 10);
  const [date, setDate] = useState(todayIso);
  const [responses, setResponses] = useState<EodResponse[]>([]);
  const [missing, setMissing] = useState<MissingDriver[]>([]);
  const [loading, setLoading] = useState(false);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [tab, setTab] = useState<'responses' | 'missing' | 'flags'>('responses');

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const [rRes, mRes] = await Promise.all([
        fetch(`${api}/eod-survey/responses?survey_date=${date}`),
        fetch(`${api}/eod-survey/missing?survey_date=${date}`),
      ]);
      setResponses(rRes.ok ? await rRes.json() : []);
      setMissing(mRes.ok ? await mRes.json() : []);
    } finally {
      setLoading(false);
    }
  }, [api, date]);

  useEffect(() => { load(); }, [load]);

  const flagged = responses.filter(r => r.flags.length > 0);
  const completed = responses.length;
  const total = completed + missing.length;

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>

          {/* Header */}
          <div style={{ marginBottom: 20 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>End of Day Survey</h1>
            <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>Driver check-out responses and outstanding items</p>
          </div>

          {/* Date picker + stats */}
          <div style={{ display: 'flex', gap: 16, alignItems: 'center', marginBottom: 20, flexWrap: 'wrap' }}>
            <input
              type="date" value={date} onChange={e => setDate(e.target.value)}
              style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14 }}
            />
            {[
              { label: 'Submitted', value: completed, color: '#4ade80' },
              { label: 'Missing', value: missing.length, color: missing.length > 0 ? '#f59e0b' : '#4ade80' },
              { label: 'Needs Attention', value: flagged.length, color: flagged.length > 0 ? '#ef4444' : '#4ade80' },
            ].map(s => (
              <div key={s.label} style={{ background: '#1e293b', borderRadius: 8, padding: '8px 16px', minWidth: 110 }}>
                <div style={{ fontSize: 11, color: '#64748b' }}>{s.label}</div>
                <div style={{ fontSize: 22, fontWeight: 700, color: s.color }}>
                  {s.value}{s.label === 'Submitted' && total > 0 ? <span style={{ fontSize: 13, color: '#64748b' }}> / {total}</span> : ''}
                </div>
              </div>
            ))}
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
            {[
              { key: 'responses', label: `Responses (${completed})` },
              { key: 'missing', label: `Not Submitted (${missing.length})` },
              { key: 'flags', label: `Flags (${flagged.length})` },
            ].map(t => (
              <button key={t.key} onClick={() => setTab(t.key as typeof tab)} style={{
                background: tab === t.key ? '#1e40af' : '#1e293b',
                color: tab === t.key ? '#fff' : '#94a3b8',
                border: 'none', borderRadius: 8, padding: '8px 18px',
                cursor: 'pointer', fontWeight: 600, fontSize: 14,
              }}>{t.label}</button>
            ))}
          </div>

          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {/* Responses tab */}
          {!loading && tab === 'responses' && (
            <div>
              {responses.length === 0 ? (
                <p style={{ color: '#64748b' }}>No submissions yet for {date}.</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {responses.map(r => (
                    <div key={r.id} style={{
                      background: '#1e293b', borderRadius: 10,
                      border: r.flags.length > 0 ? '1px solid #f59e0b44' : '1px solid #1e293b',
                    }}>
                      <div
                        onClick={() => setExpanded(expanded === r.id ? null : r.id)}
                        style={{ padding: '12px 16px', cursor: 'pointer', display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'wrap', gap: 8 }}
                      >
                        <div>
                          <span style={{ fontWeight: 700, color: '#f1f5f9', marginRight: 12 }}>{r.driver_name}</span>
                          {r.flags.map(flagBadge)}
                        </div>
                        <div style={{ fontSize: 12, color: '#64748b', display: 'flex', gap: 16 }}>
                          {r.van_number && <span>Van {r.van_number}</span>}
                          {r.wave && <span>Wave {r.wave}</span>}
                          <span>Out: {r.clock_out_time || '—'}</span>
                          <span>RTS: {r.packages_rts}</span>
                          <span style={{ color: '#475569' }}>Submitted {fmt(r.submitted_at)}</span>
                        </div>
                      </div>

                      {expanded === r.id && (
                        <div style={{ padding: '0 16px 16px', borderTop: '1px solid #334155' }}>
                          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))', gap: 12, marginTop: 12 }}>
                            {[
                              ['Clocked in on time', yn(r.clocked_in_on_time)],
                              r.clocked_in_on_time === false ? ['Actual clock-in', r.actual_clock_in_time || '—'] : null,
                              ['Van issues', yn(r.van_issues, 'Yes ⚠️', 'No')],
                              r.van_issues ? ['Van issue', r.van_issue_description || '—'] : null,
                              ['Incident', yn(r.incident_occurred, 'Yes 🚨', 'No')],
                              r.incident_occurred ? ['Report filed', yn(r.incident_report_filed)] : null,
                              ['Injury', yn(r.injury_occurred, 'Yes 🚨', 'No')],
                              r.injury_occurred ? ['Injury report', yn(r.injury_report_submitted)] : null,
                              r.injury_occurred ? ['Medical review', yn(r.medical_review_completed)] : null,
                              ['Post-trip DVIC', yn(r.post_trip_dvic_completed)],
                              ['Gas level', r.gas_level || '—'],
                              ['Packages RTS', String(r.packages_rts)],
                              ['Route issues', yn(r.route_issues, 'Yes ⚠️', 'No')],
                              r.route_issues ? ['Route issue', r.route_issue_description || '—'] : null,
                              ['Sweep', yn(r.performed_sweep)],
                              r.performed_sweep ? ['Sweep details', r.sweep_details || '—'] : null,
                              ['Lunch break', yn(r.took_lunch)],
                              r.took_lunch ? ['Lunch out/in', `${r.lunch_clock_out || '?'} → ${r.lunch_clock_in || '?'}`] : null,
                              ['Clock out', r.clock_out_time || '—'],
                              ['Pockets checked', yn(r.pockets_checked)],
                              ['All equipment present', yn(r.all_equipment_present)],
                              r.all_equipment_present === false ? ['Missing', r.missing_equipment || '—'] : null,
                              ['Needs mgmt contact', yn(r.needs_management_contact, 'Yes 👔', 'No')],
                            ].filter((x): x is [string, string] => x !== null).map(([label, value]) => (
                              <div key={String(label)} style={{ background: '#0f172a', borderRadius: 8, padding: '8px 12px' }}>
                                <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>{label}</div>
                                <div style={{ fontSize: 14, color: '#e2e8f0' }}>{value}</div>
                              </div>
                            ))}
                          </div>
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Missing tab */}
          {!loading && tab === 'missing' && (
            <div>
              {missing.length === 0 ? (
                <p style={{ color: '#4ade80' }}>✅ All scheduled drivers have submitted for {date}.</p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                  <thead>
                    <tr style={{ color: '#64748b', textAlign: 'left', borderBottom: '1px solid #334155' }}>
                      {['Driver', 'Van', 'Wave'].map(h => <th key={h} style={{ padding: '8px 12px' }}>{h}</th>)}
                    </tr>
                  </thead>
                  <tbody>
                    {missing.map((m, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td style={{ padding: '10px 12px', color: '#f59e0b', fontWeight: 600 }}>{m.driver_name}</td>
                        <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{m.van || '—'}</td>
                        <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{m.wave || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}

          {/* Flags tab */}
          {!loading && tab === 'flags' && (
            <div>
              {flagged.length === 0 ? (
                <p style={{ color: '#4ade80' }}>✅ No items needing attention for {date}.</p>
              ) : (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                  {flagged.map(r => (
                    <div key={r.id} style={{ background: '#1e293b', borderRadius: 10, padding: 16, border: '1px solid #f59e0b44' }}>
                      <div style={{ fontWeight: 700, color: '#f1f5f9', marginBottom: 8 }}>{r.driver_name}</div>
                      <div style={{ marginBottom: 8 }}>{r.flags.map(flagBadge)}</div>
                      {r.van_issues && r.van_issue_description && (
                        <div style={{ fontSize: 13, color: '#fbbf24', marginTop: 4 }}>🔧 {r.van_issue_description}</div>
                      )}
                      {r.incident_occurred && (
                        <div style={{ fontSize: 13, color: '#f87171', marginTop: 4 }}>
                          🚨 Incident — report filed: {r.incident_report_filed ? 'Yes' : 'No'}
                        </div>
                      )}
                      {r.injury_occurred && (
                        <div style={{ fontSize: 13, color: '#f87171', marginTop: 4 }}>
                          🩺 Injury — report: {r.injury_report_submitted ? 'Yes' : 'No'} · medical review: {r.medical_review_completed ? 'Yes' : 'No'}
                        </div>
                      )}
                      {r.needs_management_contact && (
                        <div style={{ fontSize: 13, color: '#fbbf24', marginTop: 4 }}>
                          👔 Driver requested management contact
                        </div>
                      )}
                      {r.post_trip_dvic_completed === false && (
                        <div style={{ fontSize: 13, color: '#f97316', marginTop: 4 }}>
                          ⚠️ Post-trip DVIC not completed
                        </div>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
