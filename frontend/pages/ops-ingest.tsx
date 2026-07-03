import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

type IngestStatus = 'pending' | 'ingesting' | 'complete' | 'error' | 'skipped' | 'unsupported';

interface IngestJob {
  id: number;
  slack_file_id: string;
  file_name: string;
  detected_type: string;
  type_label: string;
  description: string | null;
  status: IngestStatus;
  result: Record<string, unknown> | null;
  error_message: string | null;
  detected_at: string;
  ingested_at: string | null;
}

const TYPE_COLORS: Record<string, string> = {
  quality_csv:      '#0ea5e9',
  cortex:           '#8b5cf6',
  dop:              '#f59e0b',
  driver_schedule:  '#10b981',
  route_sheets:     '#f97316',
  wst_zip:          '#6366f1',
  variable_invoice: '#ec4899',
  fleet_invoice:    '#ef4444',
  weekly_incentive: '#14b8a6',
  dsp_scorecard:    '#a855f7',
  pod_report:       '#84cc16',
  unknown:          '#94a3b8',
};

const STATUS_LABELS: Record<string, string> = {
  pending:     'Pending',
  ingesting:   'Ingesting…',
  complete:    'Complete',
  error:       'Error',
  skipped:     'Skipped',
  unsupported: 'No Handler Yet',
};

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('en-US', { month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', hour12: true });
}

function ResultSummary({ result }: { result: Record<string, unknown> | null }) {
  if (!result) return null;
  const { status, records, driver_count, week, message, report_path } = result as Record<string, unknown>;
  return (
    <div style={{ fontSize: 12, color: '#94a3b8', marginTop: 4 }}>
      {status === 'ingested' && (
        <>
          {(records || driver_count) ? <span>Records: <b>{String(records ?? driver_count)}</b>{week ? ` · Week ${week}` : ''}</span> : null}
          {report_path ? <span> · Report generated</span> : null}
        </>
      )}
      {status === 'already_ingested' && <span>Already ingested for week {String(week ?? '')}</span>}
      {status === 'unsupported' && <span style={{ color: '#f59e0b' }}>{String(message ?? 'Handler not yet built')}</span>}
      {status === 'error' && <span style={{ color: '#ef4444' }}>{String(message ?? 'Unknown error')}</span>}
    </div>
  );
}

export default function OpsIngestPage() {
  const [jobs, setJobs] = useState<IngestJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [scanning, setScanning] = useState(false);
  const [tab, setTab] = useState<'pending' | 'history'>('pending');
  const [actionId, setActionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);

  const api = resolveApi();

  const load = useCallback(async () => {
    try {
      const res = await fetch(`${api}/ops-ingest/jobs?limit=200`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setJobs(data.jobs ?? []);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load jobs');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const scanNow = async () => {
    setScanning(true);
    try {
      const res = await fetch(`${api}/ops-ingest/scan`, { method: 'POST' });
      const data = await res.json();
      await load();
      if (data.new_files_detected > 0) {
        alert(`Detected ${data.new_files_detected} new file(s):\n${(data.filenames as string[]).join('\n')}`);
      } else {
        alert('No new files found in the channel.');
      }
    } catch (e: unknown) {
      alert('Scan failed: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setScanning(false);
    }
  };

  const ingest = async (id: number) => {
    setActionId(id);
    try {
      const res = await fetch(`${api}/ops-ingest/jobs/${id}/ingest`, { method: 'POST' });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        alert('Ingest failed: ' + (err.detail ?? JSON.stringify(err)));
      }
      await load();
    } catch (e: unknown) {
      alert('Ingest error: ' + (e instanceof Error ? e.message : String(e)));
    } finally {
      setActionId(null);
    }
  };

  const skip = async (id: number) => {
    setActionId(id);
    try {
      await fetch(`${api}/ops-ingest/jobs/${id}/skip`, { method: 'POST' });
      await load();
    } finally {
      setActionId(null);
    }
  };

  const pending = jobs.filter(j => j.status === 'pending' || j.status === 'error');
  const history = jobs.filter(j => j.status !== 'pending' && j.status !== 'error');

  const displayed = tab === 'pending' ? pending : history;

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        {/* Header */}
        <div style={{ maxWidth: 960, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Ops Channel Ingest</h1>
              <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
                Drop a file in <b style={{ color: '#94a3b8' }}>#nday-operations-management</b> with a description — it appears here automatically.
              </p>
            </div>
            <button
              onClick={scanNow}
              disabled={scanning}
              style={{
                background: scanning ? '#1e293b' : '#0ea5e9',
                color: '#fff',
                border: 'none',
                borderRadius: 8,
                padding: '10px 20px',
                cursor: scanning ? 'default' : 'pointer',
                fontWeight: 600,
                fontSize: 14,
              }}
            >
              {scanning ? 'Scanning…' : 'Scan Now'}
            </button>
          </div>

          {/* Tabs */}
          <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
            {(['pending', 'history'] as const).map(t => (
              <button
                key={t}
                onClick={() => setTab(t)}
                style={{
                  background: tab === t ? '#1e40af' : '#1e293b',
                  color: tab === t ? '#fff' : '#94a3b8',
                  border: 'none',
                  borderRadius: 8,
                  padding: '8px 18px',
                  cursor: 'pointer',
                  fontWeight: 600,
                  fontSize: 14,
                }}
              >
                {t === 'pending' ? `Pending (${pending.length})` : `History (${history.length})`}
              </button>
            ))}
          </div>

          {/* Error */}
          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}

          {/* Loading */}
          {loading && <p style={{ color: '#64748b' }}>Loading…</p>}

          {/* Empty state */}
          {!loading && displayed.length === 0 && (
            <div style={{ textAlign: 'center', color: '#475569', padding: '60px 0' }}>
              {tab === 'pending'
                ? 'No files in the queue. Drop a file in #nday-operations-management and click Scan Now.'
                : 'No ingest history yet.'}
            </div>
          )}

          {/* Job cards */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            {displayed.map(job => {
              const typeColor = TYPE_COLORS[job.detected_type] ?? '#94a3b8';
              const busy = actionId === job.id;
              return (
                <div
                  key={job.id}
                  style={{
                    background: '#1e293b',
                    borderRadius: 10,
                    padding: 16,
                    border: `1px solid ${job.status === 'error' ? '#7f1d1d' : '#334155'}`,
                  }}
                >
                  <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 12 }}>
                    {/* Left: file info */}
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 4 }}>
                        <span
                          style={{
                            background: typeColor + '22',
                            color: typeColor,
                            border: `1px solid ${typeColor}55`,
                            borderRadius: 6,
                            padding: '2px 8px',
                            fontSize: 11,
                            fontWeight: 700,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {job.type_label}
                        </span>
                        <span
                          style={{
                            background: job.status === 'complete' ? '#052e16' : job.status === 'error' ? '#450a0a' : job.status === 'pending' ? '#172554' : '#1e293b',
                            color: job.status === 'complete' ? '#4ade80' : job.status === 'error' ? '#f87171' : job.status === 'pending' ? '#93c5fd' : '#64748b',
                            borderRadius: 6,
                            padding: '2px 8px',
                            fontSize: 11,
                            fontWeight: 600,
                          }}
                        >
                          {STATUS_LABELS[job.status] ?? job.status}
                        </span>
                      </div>

                      <div style={{ fontWeight: 600, fontSize: 15, color: '#f1f5f9', wordBreak: 'break-all' }}>
                        {job.file_name}
                      </div>

                      {job.description && (
                        <div style={{ fontSize: 13, color: '#94a3b8', marginTop: 4, fontStyle: 'italic' }}>
                          &ldquo;{job.description}&rdquo;
                        </div>
                      )}

                      <ResultSummary result={job.result} />

                      {job.error_message && (
                        <div style={{ fontSize: 12, color: '#f87171', marginTop: 4 }}>{job.error_message}</div>
                      )}

                      <div style={{ fontSize: 11, color: '#475569', marginTop: 6 }}>
                        Detected {fmt(job.detected_at)}
                        {job.ingested_at && <> · Ingested {fmt(job.ingested_at)}</>}
                      </div>
                    </div>

                    {/* Right: action buttons */}
                    {(job.status === 'pending' || job.status === 'error') && (
                      <div style={{ display: 'flex', flexDirection: 'column', gap: 8, flexShrink: 0 }}>
                        <button
                          onClick={() => ingest(job.id)}
                          disabled={busy}
                          style={{
                            background: busy ? '#1e293b' : '#16a34a',
                            color: '#fff',
                            border: 'none',
                            borderRadius: 7,
                            padding: '8px 16px',
                            cursor: busy ? 'default' : 'pointer',
                            fontWeight: 600,
                            fontSize: 13,
                            whiteSpace: 'nowrap',
                          }}
                        >
                          {busy ? 'Working…' : 'Ingest'}
                        </button>
                        <button
                          onClick={() => skip(job.id)}
                          disabled={busy}
                          style={{
                            background: 'transparent',
                            color: '#64748b',
                            border: '1px solid #334155',
                            borderRadius: 7,
                            padding: '7px 16px',
                            cursor: busy ? 'default' : 'pointer',
                            fontSize: 13,
                          }}
                        >
                          Skip
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
