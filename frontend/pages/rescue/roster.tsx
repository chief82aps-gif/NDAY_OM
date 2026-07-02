'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
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

interface RosterEntry {
  position_id: string;
  payroll_name: string;
  position_code: string;
  hire_date: string | null;
  is_active: boolean;
  phone: string | null;
  slack_member_id: string | null;
  slack_display_name: string | null;
  slack_verified: boolean;
  slack_verified_at: string | null;
}

export default function RosterPage() {
  const router = useRouter();
  const [roster, setRoster] = useState<RosterEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [editing, setEditing] = useState<string | null>(null);        // position_id being edited (slack)
  const [inputVal, setInputVal] = useState('');
  const [editingPhone, setEditingPhone] = useState<string | null>(null); // position_id being edited (phone)
  const [phoneVal, setPhoneVal] = useState('');
  const [saving, setSaving] = useState<string | null>(null);
  const [testing, setTesting] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<Record<string, { ok: boolean; msg: string }>>({});

  const fetchRoster = async () => {
    setLoading(true);
    try {
      const res = await fetch(`${resolveApi()}/rescue/roster`);
      if (res.ok) setRoster(await res.json());
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchRoster(); }, []);

  const setMsg = (posId: string, ok: boolean, msg: string) =>
    setFeedback((prev) => ({ ...prev, [posId]: { ok, msg } }));

  const saveSlackId = async (posId: string) => {
    const val = inputVal.trim();
    if (!val) return;
    setSaving(posId);
    setFeedback((prev) => { const n = { ...prev }; delete n[posId]; return n; });
    try {
      const res = await fetch(`${resolveApi()}/rescue/roster/${posId}/slack`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ slack_member_id: val }),
      });
      const data = await res.json();
      if (res.ok) {
        setMsg(posId, true, `✅ Verified — Slack name: ${data.slack_display_name}`);
        setEditing(null);
        await fetchRoster();
      } else {
        setMsg(posId, false, `❌ ${data.detail ?? 'Verification failed'}`);
      }
    } catch {
      setMsg(posId, false, '❌ Network error');
    } finally {
      setSaving(null);
    }
  };

  const savePhone = async (posId: string) => {
    setSaving(posId);
    setFeedback((prev) => { const n = { ...prev }; delete n[posId]; return n; });
    try {
      const res = await fetch(`${resolveApi()}/rescue/roster/${posId}/phone`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ phone: phoneVal }),
      });
      if (res.ok) {
        setMsg(posId, true, phoneVal.trim() ? `✅ Phone saved: ${phoneVal.trim()}` : 'Phone number removed.');
        setEditingPhone(null);
        await fetchRoster();
      } else {
        setMsg(posId, false, '❌ Failed to save phone number.');
      }
    } catch {
      setMsg(posId, false, '❌ Network error');
    } finally {
      setSaving(null);
    }
  };

  const unlinkSlack = async (posId: string) => {
    setSaving(posId);
    try {
      const res = await fetch(`${resolveApi()}/rescue/roster/${posId}/slack`, { method: 'DELETE' });
      if (res.ok) {
        setMsg(posId, true, 'Slack ID removed.');
        await fetchRoster();
      }
    } catch {
      setMsg(posId, false, '❌ Network error');
    } finally {
      setSaving(null);
    }
  };

  const sendTestDM = async (posId: string) => {
    setTesting(posId);
    setFeedback((prev) => { const n = { ...prev }; delete n[posId]; return n; });
    try {
      const res = await fetch(`${resolveApi()}/rescue/roster/${posId}/slack/test-dm`, { method: 'POST' });
      const data = await res.json();
      if (res.ok) {
        setMsg(posId, true, '📨 Test DM sent — driver should receive it within seconds.');
      } else {
        setMsg(posId, false, `❌ ${data.detail ?? 'DM failed'}`);
      }
    } catch {
      setMsg(posId, false, '❌ Network error');
    } finally {
      setTesting(null);
    }
  };

  const drivers = roster.filter((r) => r.position_code === '000004-Driver');
  const others  = roster.filter((r) => r.position_code !== '000004-Driver');

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Driver Roster — Slack Setup" showBack />

        <main className="max-w-3xl mx-auto px-4 py-8">

          {/* How-to banner */}
          <div className="bg-blue-50 border border-blue-200 rounded-xl p-4 mb-6 text-sm text-blue-800">
            <p className="font-semibold mb-1">How to find a driver's Slack Member ID</p>
            <ol className="list-decimal list-inside space-y-0.5 text-blue-700">
              <li>Open Slack and find the driver's profile</li>
              <li>Click <strong>⋯ More</strong> on their profile card</li>
              <li>Click <strong>Copy member ID</strong> — it starts with <code className="bg-blue-100 px-1 rounded">U</code> (e.g. <code className="bg-blue-100 px-1 rounded">U012AB3CD</code>)</li>
              <li>Paste it below and click <strong>Verify &amp; Save</strong></li>
            </ol>
          </div>

          {loading ? (
            <p className="text-slate-400">Loading roster...</p>
          ) : (
            <>
              <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
                Drivers ({drivers.length})
              </h2>

              <div className="space-y-3 mb-8">
                {drivers.map((entry) => {
                  const fb = feedback[entry.position_id];
                  const isEditing = editing === entry.position_id;

                  return (
                    <div key={entry.position_id} className="bg-white rounded-xl border border-slate-200 shadow-sm p-4">
                      <div className="flex items-center justify-between flex-wrap gap-2">
                        <div>
                          <p className="font-semibold text-slate-800">{entry.payroll_name}</p>
                          <p className="text-xs text-slate-400">{entry.position_id} · Hire {entry.hire_date ?? '—'}</p>
                        </div>

                        {/* Slack status badge */}
                        {entry.slack_verified ? (
                          <span className="flex items-center gap-1.5 px-2.5 py-1 bg-green-50 border border-green-200 rounded-full text-xs font-semibold text-green-700">
                            <span className="w-2 h-2 rounded-full bg-green-500 inline-block" />
                            Linked
                          </span>
                        ) : entry.slack_member_id ? (
                          <span className="px-2.5 py-1 bg-yellow-50 border border-yellow-200 rounded-full text-xs font-semibold text-yellow-700">
                            ⚠ Unverified
                          </span>
                        ) : (
                          <span className="px-2.5 py-1 bg-slate-100 border border-slate-200 rounded-full text-xs text-slate-500">
                            Not linked
                          </span>
                        )}
                      </div>

                      {/* Phone number */}
                      {editingPhone === entry.position_id ? (
                        <div className="mt-3 flex gap-2">
                          <input
                            autoFocus
                            type="tel"
                            value={phoneVal}
                            onChange={(e) => setPhoneVal(e.target.value)}
                            placeholder="(555) 867-5309"
                            className="flex-1 border border-slate-300 rounded px-3 py-1.5 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                            onKeyDown={(e) => { if (e.key === 'Enter') savePhone(entry.position_id); if (e.key === 'Escape') setEditingPhone(null); }}
                          />
                          <button
                            onClick={() => savePhone(entry.position_id)}
                            disabled={saving === entry.position_id}
                            className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded font-semibold hover:bg-blue-700 disabled:opacity-50"
                          >
                            {saving === entry.position_id ? 'Saving...' : 'Save'}
                          </button>
                          <button onClick={() => setEditingPhone(null)} className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-50">Cancel</button>
                        </div>
                      ) : (
                        <div className="mt-2 flex items-center gap-3">
                          <span className="text-sm text-slate-600">
                            📞 {entry.phone ? <strong>{entry.phone}</strong> : <span className="text-slate-400">No phone on file</span>}
                          </span>
                          <button
                            onClick={() => { setEditingPhone(entry.position_id); setPhoneVal(entry.phone ?? ''); }}
                            className="text-xs text-blue-600 hover:underline"
                          >
                            {entry.phone ? 'Edit' : '+ Add'}
                          </button>
                        </div>
                      )}

                      {/* Verified details */}
                      {entry.slack_verified && entry.slack_display_name && (
                        <p className="text-sm text-slate-600 mt-2">
                          Slack: <strong>{entry.slack_display_name}</strong>
                          <span className="text-slate-400 ml-1">({entry.slack_member_id})</span>
                        </p>
                      )}

                      {/* Feedback message */}
                      {fb && (
                        <p className={`text-sm mt-2 font-medium ${fb.ok ? 'text-green-700' : 'text-red-600'}`}>
                          {fb.msg}
                        </p>
                      )}

                      {/* Edit input */}
                      {isEditing && (
                        <div className="mt-3 flex gap-2">
                          <input
                            autoFocus
                            type="text"
                            value={inputVal}
                            onChange={(e) => setInputVal(e.target.value)}
                            placeholder="U012AB3CD"
                            className="flex-1 border border-slate-300 rounded px-3 py-1.5 text-sm font-mono focus:outline-none focus:ring-2 focus:ring-blue-400"
                            onKeyDown={(e) => { if (e.key === 'Enter') saveSlackId(entry.position_id); if (e.key === 'Escape') setEditing(null); }}
                          />
                          <button
                            onClick={() => saveSlackId(entry.position_id)}
                            disabled={saving === entry.position_id}
                            className="px-3 py-1.5 bg-blue-600 text-white text-sm rounded font-semibold hover:bg-blue-700 disabled:opacity-50"
                          >
                            {saving === entry.position_id ? 'Verifying...' : 'Verify & Save'}
                          </button>
                          <button
                            onClick={() => { setEditing(null); setInputVal(''); }}
                            className="px-3 py-1.5 border border-slate-300 text-sm rounded hover:bg-slate-50"
                          >
                            Cancel
                          </button>
                        </div>
                      )}

                      {/* Action buttons */}
                      {!isEditing && (
                        <div className="mt-3 flex gap-2 flex-wrap">
                          <button
                            onClick={() => { setEditing(entry.position_id); setInputVal(entry.slack_member_id ?? ''); }}
                            className="px-3 py-1 text-xs border border-slate-300 rounded hover:bg-slate-50 font-medium"
                          >
                            {entry.slack_member_id ? 'Change Slack ID' : '+ Add Slack ID'}
                          </button>

                          {entry.slack_verified && (
                            <button
                              onClick={() => sendTestDM(entry.position_id)}
                              disabled={testing === entry.position_id}
                              className="px-3 py-1 text-xs bg-amber-500 text-white rounded font-semibold hover:bg-amber-600 disabled:opacity-50"
                            >
                              {testing === entry.position_id ? 'Sending...' : 'Send Test DM'}
                            </button>
                          )}

                          {entry.slack_member_id && (
                            <button
                              onClick={() => unlinkSlack(entry.position_id)}
                              disabled={saving === entry.position_id}
                              className="px-3 py-1 text-xs text-red-500 border border-red-200 rounded hover:bg-red-50 disabled:opacity-50"
                            >
                              Remove
                            </button>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>

              {others.length > 0 && (
                <>
                  <h2 className="text-sm font-semibold text-slate-500 uppercase tracking-wide mb-3">
                    Other Staff ({others.length})
                  </h2>
                  <div className="space-y-2">
                    {others.map((entry) => (
                      <div key={entry.position_id} className="bg-white rounded-lg border border-slate-200 px-4 py-3 text-sm text-slate-600 flex justify-between">
                        <span>{entry.payroll_name}</span>
                        <span className="text-slate-400">{entry.position_code}</span>
                      </div>
                    ))}
                  </div>
                </>
              )}
            </>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
