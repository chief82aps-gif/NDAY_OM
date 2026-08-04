import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

interface Driver {
  id: number;
  payroll_name: string;
  is_active: boolean;
  source: string;
  last_seen_on_schedule: string | null;
  flagged_inactive: boolean;
  flagged_inactive_at: string | null;
  slack_member_id: string | null;
  slack_display_name: string | null;
  slack_verified: boolean;
  phone: string | null;
  ssn_last4: string | null;
}

const SOURCE_LABELS: Record<string, string> = {
  adp_import: 'ADP Import',
  schedule_upload: 'Schedule Upload',
  hr_module: 'HR Module',
};

const SOURCE_COLORS: Record<string, string> = {
  adp_import: '#8b5cf6',
  schedule_upload: '#0ea5e9',
  hr_module: '#10b981',
};

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const inputStyle: React.CSSProperties = {
  width: '100%', boxSizing: 'border-box', background: '#0f172a', color: '#e2e8f0',
  border: '1px solid #334155', borderRadius: 8, padding: '8px 12px', fontSize: 14,
};

function fmt(iso: string | null): string {
  if (!iso) return '—';
  return new Date(iso).toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

interface EditForm {
  phone: string;
  ssn_last4: string;
  slack_member_id: string;
  slack_display_name: string;
  is_active: boolean;
}

interface ImportMatchSample {
  driver: string;
  score: number;
  pin?: string;
  slack_id?: string;
  slack_display?: string;
  method?: string;
}

interface TerminatedDriver {
  driver: string;
  score: number;
  status: string;
}

interface ImportResult {
  status: string;
  roster_size: number;
  ssn: { matched: number; unmatched: number; sample: ImportMatchSample[] } | null;
  slack: { matched: number; unmatched: number; sample: ImportMatchSample[] } | null;
  terminated: { count: number; drivers: TerminatedDriver[] } | null;
}

function toForm(d: Driver): EditForm {
  return {
    phone: d.phone ?? '',
    ssn_last4: d.ssn_last4 ?? '',
    slack_member_id: d.slack_member_id ?? '',
    slack_display_name: d.slack_display_name ?? '',
    is_active: d.is_active,
  };
}

export default function DriversPage() {
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'all' | 'flagged' | 'inactive'>('all');

  const [editing, setEditing] = useState<Driver | null>(null);
  const [form, setForm] = useState<EditForm | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);

  const [showImport, setShowImport] = useState(false);
  const [ssnFile, setSsnFile] = useState<File | null>(null);
  const [slackFile, setSlackFile] = useState<File | null>(null);
  const [associateFile, setAssociateFile] = useState<File | null>(null);
  const [dryRun, setDryRun] = useState(true);
  const [importing, setImporting] = useState(false);
  const [importError, setImportError] = useState<string | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);

  const api = resolveApi();

  const runImport = async () => {
    if (!ssnFile && !slackFile && !associateFile) {
      setImportError('Provide at least an SSN export, a Slack export, or an Associate Data export.');
      return;
    }
    setImporting(true);
    setImportError(null);
    try {
      const body = new FormData();
      if (ssnFile) body.append('ssn_file', ssnFile);
      if (slackFile) body.append('slack_file', slackFile);
      if (associateFile) body.append('associate_file', associateFile);
      const res = await fetch(`${api}/drivers/import-ssn-slack?dry_run=${dryRun}`, {
        method: 'POST',
        body,
      });
      if (!res.ok) throw new Error(await res.text());
      const data: ImportResult = await res.json();
      setImportResult(data);
      if (!dryRun) await load();
    } catch (e: unknown) {
      setImportError(e instanceof Error ? e.message : 'Import failed.');
      setImportResult(null);
    } finally {
      setImporting(false);
    }
  };

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${api}/drivers?include_inactive=true`, { cache: 'no-store' });
      if (!res.ok) throw new Error(await res.text());
      const data = await res.json();
      setDrivers(data.drivers ?? []);
      setError(null);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load drivers');
    } finally {
      setLoading(false);
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const openEdit = (d: Driver) => {
    setEditing(d);
    setForm(toForm(d));
    setSaveError(null);
  };

  const closeEdit = () => {
    setEditing(null);
    setForm(null);
    setSaveError(null);
  };

  const save = async () => {
    if (!editing || !form) return;
    setSaving(true);
    setSaveError(null);
    try {
      const res = await fetch(`${api}/drivers/${editing.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          phone: form.phone,
          ssn_last4: form.ssn_last4,
          slack_member_id: form.slack_member_id,
          slack_display_name: form.slack_display_name || null,
          is_active: form.is_active,
          updated_by: 'dispatch',
        }),
      });
      if (!res.ok) throw new Error(await res.text());
      closeEdit();
      await load();
    } catch (e: unknown) {
      setSaveError(e instanceof Error ? e.message : 'Failed to save.');
    } finally {
      setSaving(false);
    }
  };

  const active = drivers.filter(d => d.is_active);
  const displayed =
    filter === 'flagged' ? active.filter(d => d.flagged_inactive) :
    filter === 'inactive' ? drivers.filter(d => !d.is_active) :
    active;

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 1000, margin: '0 auto' }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 24 }}>
            <div>
              <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>Driver Profiles</h1>
              <p style={{ margin: '4px 0 0', fontSize: 14, color: '#64748b' }}>
                Interim source of truth, fed by ADP import and schedule uploads — a future HR module will own create/terminate.
              </p>
            </div>
            <div style={{ display: 'flex', gap: 8 }}>
              <button
                onClick={() => setShowImport(v => !v)}
                style={{
                  background: showImport ? '#1e40af' : '#334155', color: '#fff', border: 'none', borderRadius: 8,
                  padding: '10px 20px', cursor: 'pointer', fontWeight: 600, fontSize: 14,
                }}
              >
                📥 Import SSN/Slack Data
              </button>
              <button
                onClick={load}
                disabled={loading}
                style={{
                  background: loading ? '#1e293b' : '#0ea5e9', color: '#fff', border: 'none', borderRadius: 8,
                  padding: '10px 20px', cursor: loading ? 'default' : 'pointer', fontWeight: 600, fontSize: 14,
                }}
              >
                {loading ? 'Loading…' : 'Refresh'}
              </button>
            </div>
          </div>

          {showImport && (
            <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 12, padding: 20, marginBottom: 24 }}>
              <h2 style={{ margin: '0 0 4px', fontSize: 16, fontWeight: 700, color: '#f1f5f9' }}>
                SSN / Slack / Associate Data Import
              </h2>
              <p style={{ margin: '0 0 16px', fontSize: 13, color: '#64748b' }}>
                Fuzzy-matches fresh exports against the active roster. Runs as a dry run first so you can review
                matches before writing anything — flip off "Dry run" and re-run once the sample looks right.
                Associate Data also auto-deactivates anyone it shows as not Active (never deletes — historic data
                stays, and they're welcomed back if they return).
              </p>

              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(220px, 1fr))', gap: 14, marginBottom: 14 }}>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>SSN Export</label>
                  <input
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    onChange={(e) => setSsnFile(e.target.files?.[0] ?? null)}
                    style={{ ...inputStyle, padding: '6px 8px' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Slack Member Export</label>
                  <input
                    type="file"
                    accept=".xlsx,.xls,.csv"
                    onChange={(e) => setSlackFile(e.target.files?.[0] ?? null)}
                    style={{ ...inputStyle, padding: '6px 8px' }}
                  />
                </div>
                <div>
                  <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Associate Data (optional)</label>
                  <input
                    type="file"
                    accept=".csv"
                    onChange={(e) => setAssociateFile(e.target.files?.[0] ?? null)}
                    style={{ ...inputStyle, padding: '6px 8px' }}
                  />
                </div>
              </div>

              <div style={{ display: 'flex', alignItems: 'center', gap: 16, marginBottom: 16 }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 14, color: '#e2e8f0', cursor: 'pointer' }}>
                  <input type="checkbox" checked={dryRun} onChange={(e) => setDryRun(e.target.checked)} />
                  Dry run (preview only, no changes written)
                </label>
                <button
                  onClick={runImport}
                  disabled={importing || (!ssnFile && !slackFile && !associateFile)}
                  style={{
                    background: importing ? '#1e293b' : dryRun ? '#0ea5e9' : '#dc2626', color: '#fff', border: 'none',
                    borderRadius: 8, padding: '10px 20px', cursor: importing ? 'default' : 'pointer', fontWeight: 600, fontSize: 14,
                  }}
                >
                  {importing ? 'Running…' : dryRun ? 'Preview Matches' : 'Run Import (writes changes)'}
                </button>
              </div>

              {importError && (
                <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, fontSize: 13, color: '#fca5a5', marginBottom: 14 }}>
                  {importError}
                </div>
              )}

              {importResult && (
                <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
                  <div style={{ fontSize: 13, color: importResult.status === 'applied' ? '#10b981' : '#94a3b8' }}>
                    {importResult.status === 'applied' ? '✅ Changes written' : '👁 Preview only — nothing written'} · {importResult.roster_size} active drivers checked
                  </div>
                  {importResult.ssn && (
                    <div style={{ background: '#0f172a', borderRadius: 8, padding: 12, fontSize: 13 }}>
                      <strong style={{ color: '#e2e8f0' }}>SSN / PIN:</strong>{' '}
                      <span style={{ color: '#10b981' }}>{importResult.ssn.matched} matched</span>,{' '}
                      <span style={{ color: '#f59e0b' }}>{importResult.ssn.unmatched} unmatched</span>
                    </div>
                  )}
                  {importResult.slack && (
                    <div style={{ background: '#0f172a', borderRadius: 8, padding: 12, fontSize: 13 }}>
                      <strong style={{ color: '#e2e8f0' }}>Slack:</strong>{' '}
                      <span style={{ color: '#10b981' }}>{importResult.slack.matched} matched</span>,{' '}
                      <span style={{ color: '#f59e0b' }}>{importResult.slack.unmatched} unmatched</span>
                      {importResult.slack.sample.length > 0 && (
                        <div style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
                          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                            <thead>
                              <tr style={{ color: '#64748b', textAlign: 'left' }}>
                                <th style={{ padding: '4px 8px' }}>Driver</th>
                                <th style={{ padding: '4px 8px' }}>Slack Name</th>
                                <th style={{ padding: '4px 8px' }}>Method</th>
                                <th style={{ padding: '4px 8px' }}>Score</th>
                              </tr>
                            </thead>
                            <tbody>
                              {importResult.slack.sample.map((row, i) => (
                                <tr key={i} style={{ borderTop: '1px solid #1e293b', color: '#cbd5e1' }}>
                                  <td style={{ padding: '4px 8px' }}>{row.driver}</td>
                                  <td style={{ padding: '4px 8px' }}>{row.slack_display ?? '—'}</td>
                                  <td style={{ padding: '4px 8px' }}>{row.method ?? '—'}</td>
                                  <td style={{ padding: '4px 8px' }}>{row.score}</td>
                                </tr>
                              ))}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                  {importResult.terminated && importResult.terminated.count > 0 && (
                    <div style={{ background: '#0f172a', border: '1px solid #7f1d1d33', borderRadius: 8, padding: 12, fontSize: 13 }}>
                      <strong style={{ color: '#ef4444' }}>⚠ Auto-terminated (non-Active in Associate Data):</strong>{' '}
                      <span style={{ color: '#ef4444' }}>{importResult.terminated.count} driver(s)</span>
                      <div style={{ marginTop: 8, maxHeight: 220, overflowY: 'auto' }}>
                        <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12 }}>
                          <thead>
                            <tr style={{ color: '#64748b', textAlign: 'left' }}>
                              <th style={{ padding: '4px 8px' }}>Driver</th>
                              <th style={{ padding: '4px 8px' }}>Associate Status</th>
                              <th style={{ padding: '4px 8px' }}>Score</th>
                            </tr>
                          </thead>
                          <tbody>
                            {importResult.terminated.drivers.map((row, i) => (
                              <tr key={i} style={{ borderTop: '1px solid #1e293b', color: '#cbd5e1' }}>
                                <td style={{ padding: '4px 8px' }}>{row.driver}</td>
                                <td style={{ padding: '4px 8px' }}>{row.status}</td>
                                <td style={{ padding: '4px 8px' }}>{row.score}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          <div style={{ display: 'flex', gap: 4, marginBottom: 24 }}>
            {([
              ['all', `All Active (${active.length})`],
              ['flagged', `Flagged Inactive (${active.filter(d => d.flagged_inactive).length})`],
              ['inactive', `Terminated (${drivers.filter(d => !d.is_active).length})`],
            ] as const).map(([key, label]) => (
              <button
                key={key}
                onClick={() => setFilter(key)}
                style={{
                  background: filter === key ? '#1e40af' : '#1e293b', color: filter === key ? '#fff' : '#94a3b8',
                  border: 'none', borderRadius: 8, padding: '8px 18px', cursor: 'pointer', fontWeight: 600, fontSize: 14,
                }}
              >
                {label}
              </button>
            ))}
          </div>

          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}

          {!loading && displayed.length === 0 && !error && (
            <div style={{ textAlign: 'center', color: '#475569', padding: '60px 0' }}>Nothing to show.</div>
          )}

          {displayed.length > 0 && (
            <div style={{ background: '#1e293b', borderRadius: 12, overflow: 'hidden', border: '1px solid #334155' }}>
              <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 14 }}>
                <thead>
                  <tr style={{ background: '#0f172a', textAlign: 'left' }}>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Driver</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Source</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Last Seen on Schedule</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Slack</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}>Status</th>
                    <th style={{ padding: '12px 16px', color: '#94a3b8', fontWeight: 600 }}></th>
                  </tr>
                </thead>
                <tbody>
                  {displayed.map((d) => (
                    <tr key={d.id} style={{ borderTop: '1px solid #334155' }}>
                      <td style={{ padding: '12px 16px', color: '#e2e8f0' }}>{d.payroll_name}</td>
                      <td style={{ padding: '12px 16px' }}>
                        <span style={{
                          display: 'inline-block', padding: '3px 10px', borderRadius: 999, fontSize: 12,
                          fontWeight: 600, color: '#fff', background: SOURCE_COLORS[d.source] ?? '#64748b',
                        }}>
                          {SOURCE_LABELS[d.source] ?? d.source}
                        </span>
                      </td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>{fmt(d.last_seen_on_schedule)}</td>
                      <td style={{ padding: '12px 16px', color: '#94a3b8' }}>
                        {d.slack_verified ? `✅ ${d.slack_display_name || 'Verified'}` : d.slack_member_id ? 'Linked' : '—'}
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        {!d.is_active ? (
                          <span style={{ color: '#ef4444', fontWeight: 600 }}>Terminated</span>
                        ) : d.flagged_inactive ? (
                          <span style={{ color: '#f59e0b', fontWeight: 600 }}>⚠ Not seen 30+ days</span>
                        ) : (
                          <span style={{ color: '#10b981' }}>Active</span>
                        )}
                      </td>
                      <td style={{ padding: '12px 16px' }}>
                        <button
                          onClick={() => openEdit(d)}
                          style={{
                            background: '#334155', color: '#e2e8f0', border: 'none', borderRadius: 6,
                            padding: '6px 14px', cursor: 'pointer', fontWeight: 600, fontSize: 13,
                          }}
                        >
                          Edit
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {editing && form && (
          <div
            onClick={closeEdit}
            style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.6)',
              display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 50,
            }}
          >
            <div
              onClick={(e) => e.stopPropagation()}
              style={{
                background: '#1e293b', border: '1px solid #334155', borderRadius: 12,
                padding: 24, width: 420, maxWidth: '90vw',
              }}
            >
              <h2 style={{ margin: '0 0 4px', fontSize: 18, fontWeight: 700, color: '#f1f5f9' }}>
                Edit {editing.payroll_name}
              </h2>
              <p style={{ margin: '0 0 20px', fontSize: 13, color: '#64748b' }}>
                Leave a field as-is to keep it unchanged. Clear the Slack ID field to remove a bad link.
              </p>

              <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', marginBottom: 4 }}>Phone</label>
              <input
                value={form.phone}
                onChange={(e) => setForm({ ...form, phone: e.target.value })}
                style={inputStyle}
              />

              <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', margin: '14px 0 4px' }}>Callout PIN (last 4)</label>
              <input
                value={form.ssn_last4}
                onChange={(e) => setForm({ ...form, ssn_last4: e.target.value })}
                maxLength={4}
                style={inputStyle}
              />

              <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', margin: '14px 0 4px' }}>Slack User ID</label>
              <input
                value={form.slack_member_id}
                onChange={(e) => setForm({ ...form, slack_member_id: e.target.value })}
                placeholder="U0XXXXXXXXX"
                style={inputStyle}
              />

              <label style={{ display: 'block', fontSize: 12, color: '#94a3b8', margin: '14px 0 4px' }}>Slack Display Name</label>
              <input
                value={form.slack_display_name}
                onChange={(e) => setForm({ ...form, slack_display_name: e.target.value })}
                style={inputStyle}
              />

              <label style={{ display: 'flex', alignItems: 'center', gap: 8, marginTop: 16, fontSize: 14, color: '#e2e8f0', cursor: 'pointer' }}>
                <input
                  type="checkbox"
                  checked={form.is_active}
                  onChange={(e) => setForm({ ...form, is_active: e.target.checked })}
                />
                Active {!form.is_active && <span style={{ color: '#ef4444' }}>(unchecked = terminated)</span>}
              </label>

              {saveError && (
                <div style={{ marginTop: 14, background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 10, fontSize: 13, color: '#fca5a5' }}>
                  {saveError}
                </div>
              )}

              <div style={{ display: 'flex', gap: 8, marginTop: 20, justifyContent: 'flex-end' }}>
                <button
                  onClick={closeEdit}
                  disabled={saving}
                  style={{ background: '#334155', color: '#e2e8f0', border: 'none', borderRadius: 8, padding: '10px 18px', cursor: 'pointer', fontWeight: 600, fontSize: 14 }}
                >
                  Cancel
                </button>
                <button
                  onClick={save}
                  disabled={saving}
                  style={{ background: saving ? '#1e293b' : '#0ea5e9', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 18px', cursor: saving ? 'default' : 'pointer', fontWeight: 600, fontSize: 14 }}
                >
                  {saving ? 'Saving…' : 'Save'}
                </button>
              </div>
            </div>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
