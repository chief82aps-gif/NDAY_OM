import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface DriverSummary {
  transporter_id: string;
  transporter_name: string;
  violation_count: number;
  avg_duration_seconds: number | null;
  min_duration_seconds: number | null;
  fleet_types: string[];
  dates: string[];
  acknowledged: boolean;
  slack_member_id: string | null;
}

interface WeekData {
  week: string;
  total_violations: number;
  unique_drivers: number;
  date_range_start: string | null;
  date_range_end: string | null;
  imported_at: string | null;
  drivers: DriverSummary[];
  message?: string;
}

interface WeekMeta {
  week: string;
  source_file: string;
  total_violations: number;
  unique_drivers: number;
  imported_at: string;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

function levelColor(count: number): string {
  if (count >= 5) return '#ef4444';
  if (count >= 3) return '#f59e0b';
  return '#60a5fa';
}

function levelLabel(count: number): string {
  if (count >= 5) return 'URGENT';
  if (count >= 3) return 'WARNING';
  return 'NOTICE';
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

export default function DvicPage() {
  const [weekData, setWeekData] = useState<WeekData | null>(null);
  const [weeks, setWeeks] = useState<WeekMeta[]>([]);
  const [selectedWeek, setSelectedWeek] = useState<string>('');
  const [tab, setTab] = useState<'violations' | 'acks'>('violations');
  const [loading, setLoading] = useState(true);
  const [dmStatus, setDmStatus] = useState<Record<string, string>>({});
  const [sendingAll, setSendingAll] = useState(false);
  const [acks, setAcks] = useState<unknown[]>([]);
  const api = resolveApi();

  const loadWeeks = useCallback(async () => {
    try {
      const res = await fetch(`${api}/dvic/weeks`);
      const data: WeekMeta[] = await res.json();
      setWeeks(data);
      if (data.length > 0 && !selectedWeek) setSelectedWeek(data[0].week);
    } catch { /* handled silently */ }
  }, [api, selectedWeek]);

  const loadWeekData = useCallback(async (week: string) => {
    if (!week) return;
    setLoading(true);
    try {
      const res = await fetch(`${api}/dvic/violations?week=${encodeURIComponent(week)}`);
      const data = await res.json();
      setWeekData(data);
    } catch { /* handled silently */ } finally {
      setLoading(false);
    }
  }, [api]);

  const loadAcks = useCallback(async () => {
    try {
      const res = await fetch(`${api}/dvic/acknowledgments${selectedWeek ? `?week=${selectedWeek}` : ''}`);
      const data = await res.json();
      setAcks(data);
    } catch { /* handled silently */ }
  }, [api, selectedWeek]);

  useEffect(() => { loadWeeks(); }, [loadWeeks]);
  useEffect(() => { if (selectedWeek) { loadWeekData(selectedWeek); loadAcks(); } }, [selectedWeek, loadWeekData, loadAcks]);

  const sendDm = async (tid: string) => {
    setDmStatus(s => ({ ...s, [tid]: 'sending' }));
    try {
      const res = await fetch(`${api}/dvic/send-dm/${tid}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week: selectedWeek }),
      });
      const data = await res.json();
      setDmStatus(s => ({ ...s, [tid]: data.status }));
    } catch {
      setDmStatus(s => ({ ...s, [tid]: 'failed' }));
    }
  };

  const sendAllDms = async () => {
    setSendingAll(true);
    try {
      const res = await fetch(`${api}/dvic/send-all-dms`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ week: selectedWeek }),
      });
      const data = await res.json() as { sent: number; total_drivers: number };
      alert(`DMs sent: ${data.sent} of ${data.total_drivers} drivers`);
      await loadWeekData(selectedWeek);
    } catch (e: unknown) {
      alert('Failed: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setSendingAll(false);
    }
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>

          {/* Header */}
          <div style={{ marginBottom: 24 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>
              DVIC Pre-Trip Violations
            </h1>
            <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
              Drivers who completed their vehicle inspection in under 90 seconds
            </p>
          </div>

          {/* Week selector + actions */}
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', marginBottom: 24, flexWrap: 'wrap' }}>
            <select
              value={selectedWeek}
              onChange={e => setSelectedWeek(e.target.value)}
              style={{ background: '#1e293b', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14 }}
            >
              {weeks.length === 0 && <option value="">No data — upload a DVIC file first</option>}
              {weeks.map(w => (
                <option key={w.week} value={w.week}>
                  {w.week} · {w.unique_drivers} drivers · {w.total_violations} violations
                </option>
              ))}
            </select>

            {weekData && weekData.drivers && (
              <button
                onClick={sendAllDms}
                disabled={sendingAll}
                style={{
                  background: sendingAll ? '#1e293b' : '#7c3aed',
                  color: '#fff', border: 'none', borderRadius: 8,
                  padding: '8px 18px', cursor: sendingAll ? 'default' : 'pointer',
                  fontWeight: 600, fontSize: 14,
                }}
              >
                {sendingAll ? 'Sending…' : `Send All DMs (${weekData.drivers.length} drivers)`}
              </button>
            )}
          </div>

          {/* Stats bar */}
          {weekData && !weekData.message && (
            <div style={{ display: 'flex', gap: 16, marginBottom: 24, flexWrap: 'wrap' }}>
              {[
                { label: 'Total Violations', value: weekData.total_violations },
                { label: 'Drivers Flagged', value: weekData.unique_drivers },
                { label: 'Week', value: weekData.week },
                { label: 'Date Range', value: weekData.date_range_start ? `${fmt(weekData.date_range_start)} – ${fmt(weekData.date_range_end)}` : '—' },
                { label: 'Acknowledged', value: weekData.drivers.filter(d => d.acknowledged).length + ' / ' + weekData.unique_drivers },
              ].map(stat => (
                <div key={stat.label} style={{ background: '#1e293b', borderRadius: 8, padding: '10px 16px', minWidth: 120 }}>
                  <div style={{ fontSize: 11, color: '#64748b', marginBottom: 2 }}>{stat.label}</div>
                  <div style={{ fontSize: 18, fontWeight: 700, color: '#f1f5f9' }}>{stat.value}</div>
                </div>
              ))}
            </div>
          )}

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 20 }}>
            {(['violations', 'acks'] as const).map(t => (
              <button key={t} onClick={() => setTab(t)} style={{
                background: tab === t ? '#1e40af' : '#1e293b',
                color: tab === t ? '#fff' : '#94a3b8',
                border: 'none', borderRadius: 8, padding: '8px 18px',
                cursor: 'pointer', fontWeight: 600, fontSize: 14,
              }}>
                {t === 'violations' ? 'Driver Violations' : 'Acknowledgments'}
              </button>
            ))}
          </div>

          {/* Violations tab */}
          {tab === 'violations' && (
            <>
              {loading && <p style={{ color: '#64748b' }}>Loading…</p>}
              {!loading && weekData?.message && (
                <p style={{ color: '#64748b' }}>{weekData.message}</p>
              )}
              {!loading && weekData?.drivers && (
                <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(340px, 1fr))', gap: 12 }}>
                  {weekData.drivers.map(d => {
                    const color = levelColor(d.violation_count);
                    const dmSt = dmStatus[d.transporter_id];
                    return (
                      <div key={d.transporter_id} style={{
                        background: '#1e293b', borderRadius: 10, padding: 16,
                        border: `1px solid ${color}44`,
                      }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
                          <div>
                            <div style={{ fontWeight: 700, fontSize: 15, color: '#f1f5f9' }}>{d.transporter_name}</div>
                            <div style={{ fontSize: 11, color: '#64748b' }}>{d.transporter_id}</div>
                          </div>
                          <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 }}>
                            <span style={{
                              background: color + '22', color, border: `1px solid ${color}55`,
                              borderRadius: 6, padding: '2px 8px', fontSize: 11, fontWeight: 700,
                            }}>
                              {levelLabel(d.violation_count)}
                            </span>
                            {d.acknowledged && (
                              <span style={{ background: '#052e16', color: '#4ade80', borderRadius: 6, padding: '2px 8px', fontSize: 10 }}>
                                ✓ Signed
                              </span>
                            )}
                          </div>
                        </div>

                        <div style={{ display: 'flex', gap: 16, marginBottom: 10, fontSize: 13 }}>
                          <div>
                            <span style={{ color: '#64748b' }}>Violations: </span>
                            <span style={{ color, fontWeight: 700 }}>{d.violation_count}</span>
                          </div>
                          {d.avg_duration_seconds && (
                            <div>
                              <span style={{ color: '#64748b' }}>Avg: </span>
                              <span style={{ color: '#e2e8f0' }}>{d.avg_duration_seconds}s</span>
                            </div>
                          )}
                          {d.min_duration_seconds && (
                            <div>
                              <span style={{ color: '#64748b' }}>Min: </span>
                              <span style={{ color: '#f87171' }}>{d.min_duration_seconds}s</span>
                            </div>
                          )}
                        </div>

                        <div style={{ fontSize: 12, color: '#64748b', marginBottom: 10 }}>
                          {d.fleet_types.join(', ')} · {d.dates.length > 1 ? `${d.dates.length} days` : d.dates[0] || ''}
                        </div>

                        <div style={{ display: 'flex', gap: 8 }}>
                          <button
                            onClick={() => sendDm(d.transporter_id)}
                            disabled={!d.slack_member_id || dmSt === 'sending' || dmSt === 'sent'}
                            style={{
                              flex: 1,
                              background: dmSt === 'sent' ? '#052e16' : !d.slack_member_id ? '#1e293b' : '#4f46e5',
                              color: dmSt === 'sent' ? '#4ade80' : '#fff',
                              border: 'none', borderRadius: 6,
                              padding: '7px 0', cursor: (!d.slack_member_id || dmSt === 'sent') ? 'default' : 'pointer',
                              fontSize: 12, fontWeight: 600,
                            }}
                          >
                            {dmSt === 'sending' ? 'Sending…' : dmSt === 'sent' ? '✓ DM Sent' : dmSt === 'failed' ? '✗ Failed' : d.slack_member_id ? 'Send DM' : 'No Slack ID'}
                          </button>
                          <a
                            href={`/dvic-ack?tid=${d.transporter_id}&week=${selectedWeek}`}
                            target="_blank"
                            rel="noopener noreferrer"
                            style={{
                              background: '#1e293b', border: '1px solid #334155',
                              color: '#94a3b8', borderRadius: 6, padding: '7px 12px',
                              fontSize: 12, textDecoration: 'none', whiteSpace: 'nowrap',
                            }}
                          >
                            Ack Link
                          </a>
                        </div>
                      </div>
                    );
                  })}
                </div>
              )}
            </>
          )}

          {/* Acknowledgments tab */}
          {tab === 'acks' && (
            <div>
              {(acks as Record<string, unknown>[]).length === 0 ? (
                <p style={{ color: '#64748b' }}>No acknowledgments yet for this week.</p>
              ) : (
                <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                  <thead>
                    <tr style={{ color: '#64748b', textAlign: 'left', borderBottom: '1px solid #334155' }}>
                      {['Driver', 'Week', 'Violations', 'Signature', 'Signed At'].map(h => (
                        <th key={h} style={{ padding: '8px 12px', fontWeight: 600 }}>{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {(acks as Record<string, unknown>[]).map((a, i) => (
                      <tr key={i} style={{ borderBottom: '1px solid #1e293b' }}>
                        <td style={{ padding: '10px 12px', color: '#f1f5f9', fontWeight: 600 }}>{String(a.transporter_name ?? '')}</td>
                        <td style={{ padding: '10px 12px', color: '#94a3b8' }}>{String(a.week ?? '')}</td>
                        <td style={{ padding: '10px 12px', color: '#f59e0b' }}>{String(a.violation_count ?? '')}</td>
                        <td style={{ padding: '10px 12px', color: '#4ade80', fontStyle: 'italic' }}>&ldquo;{String(a.signature_name ?? '')}&rdquo;</td>
                        <td style={{ padding: '10px 12px', color: '#64748b' }}>{fmt(String(a.acknowledged_at ?? ''))}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
          )}
        </div>
      </div>
    </ProtectedRoute>
  );
}
