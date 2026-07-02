'use client';

import { useState, useEffect, useCallback } from 'react';
import Link from 'next/link';
import PageHeader from '../../components/PageHeader';
import { ProtectedRoute } from '../../components/ProtectedRoute';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8000';
}

interface IngestLog {
  file_type: string;
  filename: string;
  status: string;
  records: number;
  processed_at: string | null;
  error: string | null;
}

interface Assignment {
  id: number;
  route_code: string;
  driver_name: string;
  van_number: string;
  stage_location: string;
  wave: string;
  packages: number | null;
  dm_sent: boolean;
  dm_sent_at: string | null;
  acknowledged: boolean;
  acknowledged_at: string | null;
}

interface StatusData {
  date: string;
  ingest_logs: IngestLog[];
  summary: {
    total: number;
    dms_sent: number;
    acknowledged: number;
    no_slack: number;
  };
  assignments: Assignment[];
}

function Badge({ ok, label }: { ok: boolean; label: string }) {
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
        ok
          ? 'bg-green-100 text-green-700'
          : 'bg-slate-100 text-slate-500'
      }`}
    >
      {label}
    </span>
  );
}

export default function DailyNotifyPage() {
  const [status, setStatus] = useState<StatusData | null>(null);
  const [loading, setLoading] = useState(true);
  const [checking, setChecking] = useState(false);
  const [sendingDms, setSendingDms] = useState(false);
  const [resending, setResending] = useState<number | null>(null);
  const [feedback, setFeedback] = useState('');
  const [filterAck, setFilterAck] = useState<'all' | 'pending' | 'confirmed'>('all');

  const today = new Date().toLocaleDateString('en-CA'); // YYYY-MM-DD

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(`${resolveApi()}/daily-notify/status`);
      if (res.ok) setStatus(await res.json());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchStatus();
    const id = setInterval(fetchStatus, 30_000); // auto-refresh every 30s
    return () => clearInterval(id);
  }, [fetchStatus]);

  const runCheck = async () => {
    setChecking(true);
    setFeedback('');
    try {
      const res = await fetch(`${resolveApi()}/daily-notify/check`, { method: 'POST' });
      const data = await res.json();
      const dms = data.dms || {};
      setFeedback(
        `Check complete — ${data.assignments ?? 0} assignments built, ` +
        `${dms.sent ?? 0} DMs sent, ${dms.skipped ?? 0} skipped (no Slack ID).`
      );
      await fetchStatus();
    } catch {
      setFeedback('Network error — please try again.');
    } finally {
      setChecking(false);
    }
  };

  const sendDms = async () => {
    setSendingDms(true);
    setFeedback('');
    try {
      const res = await fetch(`${resolveApi()}/daily-notify/send-dms`, { method: 'POST' });
      const data = await res.json();
      setFeedback(`Sent ${data.sent} DMs, ${data.skipped} skipped.`);
      await fetchStatus();
    } catch {
      setFeedback('Network error.');
    } finally {
      setSendingDms(false);
    }
  };

  const resendDm = async (id: number, name: string) => {
    setResending(id);
    setFeedback('');
    try {
      const res = await fetch(`${resolveApi()}/daily-notify/resend-dm/${id}`, { method: 'POST' });
      if (res.ok) {
        setFeedback(`DM resent to ${name}.`);
        await fetchStatus();
      } else {
        const err = await res.json();
        setFeedback(`Failed: ${err.detail}`);
      }
    } catch {
      setFeedback('Network error.');
    } finally {
      setResending(null);
    }
  };

  const filtered = (status?.assignments ?? []).filter((a) => {
    if (filterAck === 'confirmed') return a.acknowledged;
    if (filterAck === 'pending') return !a.acknowledged;
    return true;
  });

  const dopLog = status?.ingest_logs.find((l) => l.file_type === 'dop');
  const rsLog = status?.ingest_logs.find((l) => l.file_type === 'route_sheet');

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Daily Route Notifications" showBack />

        <main className="max-w-4xl mx-auto px-4 py-8 space-y-6">

          {/* Date + actions */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <div className="flex items-center justify-between flex-wrap gap-3">
              <div>
                <p className="text-xs text-slate-400 uppercase tracking-wide font-semibold mb-0.5">
                  Today
                </p>
                <p className="text-lg font-bold text-slate-800">{today}</p>
              </div>
              <div className="flex gap-2 flex-wrap">
                <button
                  onClick={runCheck}
                  disabled={checking}
                  className="px-4 py-2 bg-blue-600 text-white rounded font-semibold text-sm hover:bg-blue-700 disabled:opacity-50"
                >
                  {checking ? 'Checking…' : '🔍 Check Slack & Send DMs'}
                </button>
                <button
                  onClick={sendDms}
                  disabled={sendingDms}
                  className="px-4 py-2 bg-green-600 text-white rounded font-semibold text-sm hover:bg-green-700 disabled:opacity-50"
                >
                  {sendingDms ? 'Sending…' : '📨 Send Pending DMs'}
                </button>
              </div>
            </div>
            {feedback && (
              <p className="mt-3 text-sm text-slate-700 font-medium">{feedback}</p>
            )}
          </div>

          {/* Ingest status */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
            <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
              File Ingest Status
            </h2>
            <div className="grid grid-cols-2 gap-4">
              {[
                { label: 'DOP (.xlsx)', log: dopLog },
                { label: 'Route Sheet (.pdf)', log: rsLog },
              ].map(({ label, log }) => (
                <div
                  key={label}
                  className={`rounded-lg border p-3 ${
                    log?.status === 'success'
                      ? 'border-green-200 bg-green-50'
                      : log?.status === 'failed'
                      ? 'border-red-200 bg-red-50'
                      : 'border-slate-200 bg-slate-50'
                  }`}
                >
                  <p className="text-xs font-semibold text-slate-500 uppercase">{label}</p>
                  {log ? (
                    <>
                      <p className="text-sm font-medium text-slate-800 mt-0.5 truncate">{log.filename}</p>
                      <p className="text-xs text-slate-500">
                        {log.records ?? 0} records ·{' '}
                        {log.status === 'success' ? '✅ OK' : `❌ ${log.error ?? 'failed'}`}
                      </p>
                    </>
                  ) : (
                    <p className="text-sm text-slate-400 mt-1">Not yet found in channel</p>
                  )}
                </div>
              ))}
            </div>
          </div>

          {/* Roll call summary */}
          {status && status.summary.total > 0 && (
            <div className="grid grid-cols-3 gap-4">
              {[
                {
                  label: 'Drivers',
                  value: status.summary.total,
                  color: 'text-slate-800',
                },
                {
                  label: 'DMs Sent',
                  value: status.summary.dms_sent,
                  color: 'text-blue-700',
                },
                {
                  label: 'Confirmed',
                  value: status.summary.acknowledged,
                  color: 'text-green-700',
                },
              ].map(({ label, value, color }) => (
                <div
                  key={label}
                  className="bg-white rounded-xl border border-slate-200 shadow-sm p-4 text-center"
                >
                  <p className={`text-3xl font-bold ${color}`}>{value}</p>
                  <p className="text-xs text-slate-500 mt-1">{label}</p>
                </div>
              ))}
            </div>
          )}

          {/* Attendance filter + table */}
          {loading ? (
            <p className="text-slate-400">Loading…</p>
          ) : !status || status.summary.total === 0 ? (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-8 text-center text-slate-400">
              <p className="text-4xl mb-3">📋</p>
              <p className="font-semibold text-slate-600">No assignments yet for today.</p>
              <p className="text-sm mt-1">
                Click <strong>Check Slack &amp; Send DMs</strong> once the DOP and Route Sheet
                are posted to <code>#dlv3-nday-info</code>.
              </p>
            </div>
          ) : (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm">
              {/* Filter tabs */}
              <div className="flex border-b border-slate-200 px-4">
                {(['all', 'pending', 'confirmed'] as const).map((f) => (
                  <button
                    key={f}
                    onClick={() => setFilterAck(f)}
                    className={`py-3 px-4 text-sm font-semibold border-b-2 -mb-px transition ${
                      filterAck === f
                        ? 'border-blue-600 text-blue-700'
                        : 'border-transparent text-slate-500 hover:text-slate-700'
                    }`}
                  >
                    {f === 'all'
                      ? `All (${status.summary.total})`
                      : f === 'pending'
                      ? `Pending (${status.summary.total - status.summary.acknowledged})`
                      : `Confirmed (${status.summary.acknowledged})`}
                  </button>
                ))}
              </div>

              {/* Driver rows */}
              <div className="divide-y divide-slate-100">
                {filtered.map((a) => (
                  <div key={a.id} className="px-4 py-3 flex items-center gap-4 flex-wrap">
                    {/* Name + route */}
                    <div className="flex-1 min-w-40">
                      <p className="font-semibold text-slate-800 text-sm">
                        {a.driver_name || <span className="text-slate-400 italic">No driver</span>}
                      </p>
                      <p className="text-xs text-slate-500">
                        {a.route_code}
                        {a.wave ? ` · ${a.wave}` : ''}
                        {a.stage_location ? ` · ${a.stage_location}` : ''}
                        {a.van_number ? ` · Van ${a.van_number}` : ''}
                        {a.packages ? ` · ${a.packages} pkgs` : ''}
                      </p>
                    </div>

                    {/* DM status */}
                    <div className="flex items-center gap-2 flex-shrink-0">
                      <Badge ok={a.dm_sent} label={a.dm_sent ? 'DM Sent' : 'Not Sent'} />
                      {a.acknowledged ? (
                        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-700">
                          ✅ Confirmed
                        </span>
                      ) : (
                        <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-amber-100 text-amber-700">
                          ⏳ Pending
                        </span>
                      )}
                    </div>

                    {/* Resend button */}
                    {a.driver_name && (
                      <button
                        onClick={() => resendDm(a.id, a.driver_name)}
                        disabled={resending === a.id}
                        className="px-2 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50 disabled:opacity-50 flex-shrink-0"
                      >
                        {resending === a.id ? '…' : 'Resend'}
                      </button>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* No-Slack-ID warning */}
          {status && status.summary.no_slack > 0 && (
            <div className="bg-amber-50 border border-amber-200 rounded-xl p-4 text-sm text-amber-800">
              <p className="font-semibold mb-1">
                ⚠ {status.summary.no_slack} driver
                {status.summary.no_slack !== 1 ? 's' : ''} could not receive a DM
              </p>
              <p>
                Their Slack ID is not linked or not verified. Go to{' '}
                <Link href="/rescue/roster" className="underline font-medium">
                  Driver Roster
                </Link>{' '}
                to set it up.
              </p>
            </div>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
