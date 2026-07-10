'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../../components/PageHeader';
import { ProtectedRoute } from '../../components/ProtectedRoute';
import { useAuth } from '../../contexts/AuthContext';

const EVENT_TYPES = ['Full Pull', 'Full Pull Assist', 'Rescue', 'Pad Sweep'];
const REASON_CODES = [
  'Improperly Loaded Van',
  'Messy Van',
  'No Sense of Urgency',
  'Nursery Driver',
  'Running Behind Schedule',
  'Vehicle Issue',
  'Other',
];

interface RouteOption {
  route_code: string;
  driver_name: string;
  packages: number;
  wave: string;
  zone: string;
}

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

function RouteSelect({
  label,
  value,
  onChange,
  routes,
  loading,
  accentClass,
  exclude,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  routes: RouteOption[];
  loading: boolean;
  accentClass: string;
  exclude?: string;
}) {
  const available = exclude ? routes.filter((r) => r.route_code !== exclude) : routes;
  const selected = routes.find((r) => r.route_code === value);

  return (
    <div>
      <label className="block text-sm font-semibold text-slate-700 mb-1">{label}</label>
      {loading ? (
        <div className="w-full border border-slate-200 rounded px-3 py-2 text-sm text-slate-400 bg-slate-50">
          Loading today&apos;s routes...
        </div>
      ) : available.length === 0 ? (
        <div className="w-full border border-amber-200 rounded px-3 py-2 text-sm text-amber-700 bg-amber-50">
          No routes found for today. Check that Cortex data has been uploaded.
        </div>
      ) : (
        <select
          value={value}
          onChange={(e) => onChange(e.target.value)}
          className={`w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 ${accentClass}`}
        >
          <option value="">— select a route —</option>
          {available.map((r) => (
            <option key={r.route_code} value={r.route_code}>
              {r.route_code} — {r.driver_name} ({r.packages} pkgs)
            </option>
          ))}
        </select>
      )}
      {selected && (
        <p className="text-xs text-slate-500 mt-1">
          Wave {selected.wave} · Zone {selected.zone} · {selected.packages} packages
        </p>
      )}
    </div>
  );
}

export default function OpenRescue() {
  const router = useRouter();
  const { user } = useAuth();

  const [routes, setRoutes] = useState<RouteOption[]>([]);
  const [routesLoading, setRoutesLoading] = useState(true);

  const [form, setForm] = useState({
    rescued_route_id: '',
    rescuing_route_id: '',
    event_type: 'Full Pull',
    reason_code: 'Running Behind Schedule',
    reason_notes: '',
    pad_sweep_package_count: '',
    expected_packages: '',
    meeting_address: '',
  });

  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState<{ event_id: string; stage2_url: string } | null>(null);

  const isPadSweep = form.event_type === 'Pad Sweep';
  const needsExpected = form.event_type === 'Full Pull' || form.event_type === 'Full Pull Assist';

  const set = (k: string, v: string) => setForm((f) => ({ ...f, [k]: v }));

  useEffect(() => {
    fetch(`${resolveApi()}/rescue/routes`)
      .then((r) => r.json())
      .then((data) => setRoutes(Array.isArray(data) ? data : []))
      .catch(() => setRoutes([]))
      .finally(() => setRoutesLoading(false));
  }, []);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');

    if (!form.rescued_route_id) { setError('Rescued route is required.'); return; }
    if (!isPadSweep && !form.rescuing_route_id) { setError('Rescuing route is required.'); return; }
    if (isPadSweep && !form.pad_sweep_package_count) { setError('Package count is required for Pad Sweep.'); return; }
    if (form.reason_code === 'Other' && !form.reason_notes.trim()) { setError('"Other" reason requires a description.'); return; }

    setSubmitting(true);
    try {
      const body: Record<string, unknown> = {
        rescued_route_id: form.rescued_route_id,
        event_type: form.event_type,
        reason_code: form.reason_code,
        reason_notes: form.reason_notes || null,
        opened_by: user?.username ?? 'dispatch',
      };
      if (!isPadSweep) body.rescuing_route_id = form.rescuing_route_id;
      if (isPadSweep) body.pad_sweep_package_count = parseInt(form.pad_sweep_package_count, 10);
      if (needsExpected && form.expected_packages) body.expected_packages = parseInt(form.expected_packages, 10);
      if (!isPadSweep && form.meeting_address.trim()) body.meeting_address = form.meeting_address.trim();

      const res = await fetch(`${resolveApi()}/rescue/events`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });

      if (!res.ok) {
        const err = await res.json();
        setError(err.detail ?? 'Failed to open rescue event.');
        return;
      }

      const data = await res.json();
      const stage2_url = `${window.location.origin}/rescue/contribute?eventId=${data.event_id}&routeId=${form.rescued_route_id}`;
      setResult({ event_id: data.event_id, stage2_url });
    } catch {
      setError('Network error — please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (result) {
    return (
      <ProtectedRoute>
        <div className="min-h-screen bg-gray-50">
          <PageHeader title="Rescue Opened" showBack />
          <main className="max-w-lg mx-auto px-4 py-12 text-center">
            <div className="bg-white rounded-xl border border-green-200 shadow-sm p-8">
              <div className="text-5xl mb-4">✅</div>
              <h2 className="text-2xl font-bold text-slate-900 mb-2">Rescue Event Created</h2>
              <p className="text-slate-500 mb-1">Event ID</p>
              <p className="font-mono text-lg font-bold text-slate-800 mb-6">{result.event_id}</p>

              {!isPadSweep && (
                <>
                  <p className="text-sm text-slate-600 mb-2">Share this Stage 2 link with the rescuing driver:</p>
                  <div className="bg-slate-50 border border-slate-200 rounded p-3 text-xs font-mono text-slate-700 break-all mb-4">
                    {result.stage2_url}
                  </div>
                  <button
                    onClick={() => navigator.clipboard.writeText(result.stage2_url)}
                    className="w-full mb-3 px-4 py-2 bg-slate-700 text-white rounded font-semibold hover:bg-slate-900"
                  >
                    Copy Link
                  </button>
                </>
              )}

              <div className="flex gap-2">
                <button
                  onClick={() => {
                    setResult(null);
                    setForm({ rescued_route_id: '', rescuing_route_id: '', event_type: 'Full Pull', reason_code: 'Running Behind Schedule', reason_notes: '', pad_sweep_package_count: '', expected_packages: '', meeting_address: '' });
                  }}
                  className="flex-1 px-4 py-2 border border-slate-300 rounded hover:bg-slate-50"
                >
                  Open Another
                </button>
                <button
                  onClick={() => router.push('/rescue')}
                  className="flex-1 px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700"
                >
                  View Dashboard
                </button>
              </div>
            </div>
          </main>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Open Rescue — Stage 1" showBack />

        <main className="max-w-lg mx-auto px-4 py-8">
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
            <form onSubmit={handleSubmit} className="space-y-5">

              {/* Event Type */}
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">Event Type</label>
                <div className="grid grid-cols-2 gap-2">
                  {EVENT_TYPES.map((t) => {
                    const active = form.event_type === t;
                    const activeClass =
                      t === 'Full Pull'        ? 'bg-red-600 text-white border-red-600' :
                      t === 'Full Pull Assist' ? 'bg-orange-500 text-white border-orange-500' :
                      t === 'Rescue'           ? 'bg-amber-500 text-white border-amber-500' :
                                                 'bg-blue-600 text-white border-blue-600';
                    return (
                      <button
                        key={t}
                        type="button"
                        onClick={() => set('event_type', t)}
                        className={`py-2 rounded border text-sm font-medium transition ${
                          active ? activeClass : 'bg-white text-slate-600 border-slate-300 hover:border-slate-500'
                        }`}
                      >
                        {t}
                      </button>
                    );
                  })}
                </div>
                {form.event_type === 'Full Pull Assist' && (
                  <p className="text-xs text-orange-600 mt-1">Dispatch defines the expected package count. Bonus eligible by default.</p>
                )}
              </div>

              {/* Rescued Route */}
              <RouteSelect
                label="Rescued Route"
                value={form.rescued_route_id}
                onChange={(v) => set('rescued_route_id', v)}
                routes={routes}
                loading={routesLoading}
                accentClass="focus:ring-red-400"
                exclude={form.rescuing_route_id}
              />

              {/* Rescuing Route — hidden for Pad Sweep */}
              {!isPadSweep && (
                <RouteSelect
                  label="Rescuing Driver Route"
                  value={form.rescuing_route_id}
                  onChange={(v) => set('rescuing_route_id', v)}
                  routes={routes}
                  loading={routesLoading}
                  accentClass="focus:ring-amber-400"
                  exclude={form.rescued_route_id}
                />
              )}

              {/* Expected packages — Full Pull and Full Pull Assist */}
              {needsExpected && (
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">
                    Expected Packages
                    <span className="ml-1 font-normal text-slate-400 text-xs">(entered by dispatch)</span>
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={form.expected_packages}
                    onChange={(e) => set('expected_packages', e.target.value)}
                    placeholder="e.g. 87"
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"
                  />
                  <p className="text-xs text-slate-400 mt-1">
                    {form.event_type === 'Full Pull'
                      ? 'Total packages remaining on the rescued route.'
                      : 'Packages dispatch is sending this driver to pick up.'}
                  </p>
                </div>
              )}

              {/* Pad Sweep package count */}
              {isPadSweep && (
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">
                    Packages Swept
                  </label>
                  <input
                    type="number"
                    min="1"
                    value={form.pad_sweep_package_count}
                    onChange={(e) => set('pad_sweep_package_count', e.target.value)}
                    placeholder="0"
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-400"
                  />
                </div>
              )}

              {/* Meeting address — shown for all non-Pad Sweep types */}
              {!isPadSweep && (
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">
                    Meeting Address
                    <span className="ml-1 font-normal text-slate-400 text-xs">(sent as GPS link to both drivers)</span>
                  </label>
                  <input
                    type="text"
                    value={form.meeting_address}
                    onChange={(e) => set('meeting_address', e.target.value)}
                    placeholder="e.g. 1234 Main St, Seattle WA 98101"
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"
                  />
                  <p className="text-xs text-slate-400 mt-1">Both drivers receive a tap-to-navigate link in their Slack DM.</p>
                </div>
              )}

              {/* Reason */}
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-1">Reason</label>
                <select
                  value={form.reason_code}
                  onChange={(e) => set('reason_code', e.target.value)}
                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"
                >
                  {REASON_CODES.map((r) => <option key={r}>{r}</option>)}
                </select>
              </div>

              {/* Other memo */}
              {form.reason_code === 'Other' && (
                <div>
                  <label className="block text-sm font-semibold text-slate-700 mb-1">Please describe</label>
                  <textarea
                    value={form.reason_notes}
                    onChange={(e) => set('reason_notes', e.target.value)}
                    rows={3}
                    className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-red-400"
                    placeholder="Describe the situation..."
                  />
                </div>
              )}

              {error && <p className="text-sm text-red-600 font-medium">{error}</p>}

              <button
                type="submit"
                disabled={submitting || routesLoading}
                className="w-full py-3 bg-red-600 text-white rounded font-bold text-base hover:bg-red-700 disabled:opacity-50"
              >
                {submitting ? 'Opening...' : 'Open Rescue Event'}
              </button>
            </form>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
