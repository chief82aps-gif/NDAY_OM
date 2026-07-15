'use client';

import { useState, useEffect, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface Submission {
  id: number;
  log_date: string;
  da_count: number | null;
  okami_count: number | null;
  capacity_base: number | null;
  capacity_4x4: number | null;
  capacity_total: number | null;
  van_count: number | null;
  frt: number | null;
  submitted_by: string | null;
  created_at: string;
  finalized_at: string | null;
  finalized_by: string | null;
  required_da_count: number | null;
  da_status: 'ok' | 'short' | null;
  required_van_count: number | null;
  effective_available_vans: number | null;
  van_status: 'ok' | 'short' | null;
  van_deficit: number | null;
  grounded_vans_snapshot: { vin: string; vehicle_name: string | null }[] | null;
  frt_breached: boolean | null;
}

function StatusPill({ status }: { status: 'ok' | 'short' | null }) {
  if (!status) return null;
  return (
    <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
      status === 'ok' ? 'bg-green-100 text-green-700' : 'bg-red-100 text-red-700'
    }`}>
      {status === 'ok' ? 'OK' : 'SHORT'}
    </span>
  );
}

export default function OkamiCapacityPage() {
  const { user } = useAuth();

  const [today, setToday] = useState<Submission | null>(null);
  const [loadingToday, setLoadingToday] = useState(true);

  const [form, setForm] = useState({
    da_count: '', okami_count: '', capacity_base: '', capacity_4x4: '', van_count: '', frt: '',
  });
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [justSubmitted, setJustSubmitted] = useState(false);

  const [finalizing, setFinalizing] = useState(false);
  const [finalizeError, setFinalizeError] = useState('');

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  const loadToday = useCallback(async () => {
    setLoadingToday(true);
    try {
      const res = await fetch(`${resolveApi()}/okami-capacity/today`);
      if (res.ok) {
        const data = await res.json();
        setToday(data.submission ?? null);
      }
    } finally {
      setLoadingToday(false);
    }
  }, []);

  useEffect(() => { loadToday(); }, [loadToday]);

  const base = form.capacity_base ? parseInt(form.capacity_base, 10) : null;
  const fourByFour = form.capacity_4x4 ? parseInt(form.capacity_4x4, 10) : null;
  const computedTotal = base !== null && fourByFour !== null ? base + fourByFour : null;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setJustSubmitted(false);

    if (!form.da_count && !form.okami_count && !form.capacity_base && !form.van_count) {
      setError('Enter at least one number before submitting.');
      return;
    }

    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        submitted_by: user?.username ?? 'ops',
      };
      if (form.da_count) body.da_count = parseInt(form.da_count, 10);
      if (form.okami_count) body.okami_count = parseInt(form.okami_count, 10);
      if (form.capacity_base) body.capacity_base = parseInt(form.capacity_base, 10);
      if (form.capacity_4x4) body.capacity_4x4 = parseInt(form.capacity_4x4, 10);
      if (form.van_count) body.van_count = parseInt(form.van_count, 10);
      if (form.frt) body.frt = parseInt(form.frt, 10);

      const res = await fetch(`${resolveApi()}/okami-capacity`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        setError('Failed to log capacity numbers.');
        return;
      }

      setJustSubmitted(true);
      setForm({ da_count: '', okami_count: '', capacity_base: '', capacity_4x4: '', van_count: '', frt: '' });
      await loadToday();
    } catch {
      setError('Network error — please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleFinalize = async () => {
    setFinalizeError('');
    setFinalizing(true);
    try {
      const res = await fetch(`${resolveApi()}/okami-capacity/finalize`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ log_id: today?.id, finalized_by: user?.username ?? 'ops' }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => null);
        setFinalizeError(err?.detail ?? 'Failed to finalize.');
        return;
      }
      await loadToday();
    } catch {
      setFinalizeError('Network error — please try again.');
    } finally {
      setFinalizing(false);
    }
  };

  const isFinalized = !!today?.finalized_at;

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Okami Capacity" showBack />

        <main className="max-w-lg mx-auto px-4 py-8">
          {loadingToday ? (
            <p className="text-slate-400 text-sm mb-4">Checking today&apos;s status...</p>
          ) : today ? (
            <div className="bg-green-50 border border-green-200 rounded-lg p-4 mb-6 text-sm">
              <p className="font-semibold text-green-800 mb-1">
                ✅ Logged today at {new Date(today.created_at).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                {today.submitted_by ? ` by ${today.submitted_by}` : ''}
              </p>
              <div className="text-green-700 grid grid-cols-2 gap-x-4 gap-y-0.5">
                {today.da_count != null && <div>DAs: <strong>{today.da_count}</strong></div>}
                {today.okami_count != null && <div>Okami: <strong>{today.okami_count}</strong></div>}
                {today.capacity_total != null && <div>Capacity: <strong>{today.capacity_total}</strong></div>}
                {today.van_count != null && <div>Vans: <strong>{today.van_count}</strong></div>}
                {today.frt != null && <div>FRT: <strong>{today.frt}</strong></div>}
              </div>
              <p className="text-xs text-green-600 mt-2">Need to correct a number? Submit again below — the latest entry is what counts.</p>
            </div>
          ) : (
            <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6 text-sm text-amber-800">
              Not logged yet today.
            </div>
          )}

          {today && (
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-6">
              {isFinalized ? (
                <>
                  <p className="font-semibold text-slate-900 mb-3">
                    🔒 Finalized at {new Date(today.finalized_at as string).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' })}
                    {today.finalized_by ? ` by ${today.finalized_by}` : ''}
                  </p>
                  <div className="space-y-2 text-sm">
                    <div className="flex items-center justify-between">
                      <span>DA coverage — need {today.required_da_count}, have {today.da_count}</span>
                      <StatusPill status={today.da_status} />
                    </div>
                    <div className="flex items-center justify-between">
                      <span>Van coverage — need {today.required_van_count}, effectively have {today.effective_available_vans}</span>
                      <StatusPill status={today.van_status} />
                    </div>
                    {today.van_status === 'short' && (
                      <div className="bg-red-50 border border-red-200 rounded p-2 text-xs text-red-700">
                        Short by {today.van_deficit}. Grounded: {today.grounded_vans_snapshot?.length
                          ? today.grounded_vans_snapshot.map((v) => v.vehicle_name || v.vin).join(', ')
                          : 'none currently flagged'}
                      </div>
                    )}
                    {today.frt != null && (
                      <div className="flex items-center justify-between">
                        <span>FRT (Flex Up Target) — {today.frt}</span>
                        <span className={`inline-block px-2 py-0.5 rounded-full text-xs font-semibold ${
                          today.frt_breached ? 'bg-red-100 text-red-700' : 'bg-green-100 text-green-700'
                        }`}>
                          {today.frt_breached ? 'BREACHED' : 'MET'}
                        </span>
                      </div>
                    )}
                  </div>
                  <p className="text-xs text-slate-400 mt-3">Corrected a number above? Finalizing again re-runs these checks and re-sends notifications.</p>
                </>
              ) : (
                <p className="text-sm text-slate-500">Numbers logged but not finalized yet — finalizing posts the summary to #nday-mgt and runs the coverage checks.</p>
              )}

              {finalizeError && <p className="text-sm text-red-600 font-medium mt-2">{finalizeError}</p>}

              <button
                onClick={handleFinalize}
                disabled={finalizing}
                className="w-full mt-4 py-2.5 bg-slate-800 text-white rounded font-semibold text-sm hover:bg-slate-900 disabled:opacity-50"
              >
                {finalizing ? 'Finalizing...' : isFinalized ? 'Re-finalize OKAMI' : 'Finalize OKAMI'}
              </button>
            </div>
          )}

          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <form onSubmit={handleSubmit} className="space-y-5">
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">DAs</label>
                <input
                  type="number" min="0" value={form.da_count}
                  onChange={(e) => set('da_count', e.target.value)}
                  placeholder="e.g. 61"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">Okami</label>
                <input
                  type="number" min="0" value={form.okami_count}
                  onChange={(e) => set('okami_count', e.target.value)}
                  placeholder="e.g. 44"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">Capacity</label>
                  <input
                    type="number" min="0" value={form.capacity_base}
                    onChange={(e) => set('capacity_base', e.target.value)}
                    placeholder="e.g. 50"
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">4x4</label>
                  <input
                    type="number" min="0" value={form.capacity_4x4}
                    onChange={(e) => set('capacity_4x4', e.target.value)}
                    placeholder="e.g. 1"
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                </div>
              </div>
              {computedTotal !== null && (
                <p className="text-xs text-slate-500 -mt-3">Total capacity: <strong>{computedTotal}</strong></p>
              )}

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">Vans</label>
                <input
                  type="number" min="0" value={form.van_count}
                  onChange={(e) => set('van_count', e.target.value)}
                  placeholder="e.g. 55"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">
                  FRT <span className="font-normal text-slate-400">(only if Amazon shows a &quot;Flex up target&quot; row this week)</span>
                </label>
                <input
                  type="number" min="0" value={form.frt}
                  onChange={(e) => set('frt', e.target.value)}
                  placeholder="Leave blank most weeks"
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                />
              </div>

              {error && <p className="text-sm text-red-600 font-medium">{error}</p>}
              {justSubmitted && !error && <p className="text-sm text-green-600 font-medium">Logged.</p>}

              <button
                type="submit"
                disabled={submitting}
                className="w-full py-3 bg-blue-600 text-white rounded font-bold text-base hover:bg-blue-700 disabled:opacity-50"
              >
                {submitting ? 'Logging...' : 'Log Okami Capacity'}
              </button>
            </form>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
