'use client';

// Stage 2 — public page, no login required. Driver taps Slack link and submits package count.
import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../../components/PageHeader';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

export default function ContributePage() {
  const router = useRouter();
  const { eventId, routeId } = router.query as { eventId?: string; routeId?: string };

  const [event, setEvent] = useState<Record<string, unknown> | null>(null);
  const isFullPullAssist = event?.event_type === 'Full Pull Assist';
  const [loadError, setLoadError] = useState('');

  const [driverName, setDriverName] = useState('');
  const [packages, setPackages] = useState('');
  const [confirmedAll, setConfirmedAll] = useState<boolean | null>(null);
  const [observations, setObservations] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [result, setResult] = useState<{ bonus_eligible: boolean; confirmed_all_taken: boolean } | null>(null);

  useEffect(() => {
    if (!eventId) return;
    fetch(`${resolveApi()}/rescue/events/${eventId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then((data) => {
        setEvent(data);
        if (data.rescuing_driver_name) setDriverName(data.rescuing_driver_name);
      })
      .catch(() => setLoadError('Could not load rescue event. Check the link and try again.'));
  }, [eventId]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError('');

    if (!driverName.trim()) { setSubmitError('Driver name could not be loaded — please contact dispatch.'); return; }
    if (!packages || parseInt(packages, 10) < 1) { setSubmitError('Please enter a valid package count.'); return; }
    if (!isFullPullAssist && confirmedAll === null) { setSubmitError('Please answer whether you took all remaining packages.'); return; }

    setSubmitting(true);
    try {
      const res = await fetch(`${resolveApi()}/rescue/contributions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          event_id: eventId,
          rescuing_driver_name: driverName.trim(),
          packages_taken: parseInt(packages, 10),
          confirmed_all_taken: confirmedAll,
          observations: observations || null,
        }),
      });

      if (!res.ok) {
        const err = await res.json();
        setSubmitError(err.detail ?? 'Submission failed. Please try again.');
        return;
      }

      setResult(await res.json());
    } catch {
      setSubmitError('Network error — please try again.');
    } finally {
      setSubmitting(false);
    }
  };

  if (result) {
    return (
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Packages Logged" showBack={false} />
        <main className="max-w-md mx-auto px-4 py-12 text-center">
          <div className={`bg-white rounded-xl border shadow-sm p-8 ${result.bonus_eligible ? 'border-green-200' : 'border-amber-200'}`}>
            <div className="text-5xl mb-4">{result.bonus_eligible ? '✅' : '⚠️'}</div>
            <h2 className="text-2xl font-bold text-slate-900 mb-3">
              {result.bonus_eligible ? 'Logged — Bonus Eligible' : 'Logged — Under Review'}
            </h2>
            {result.bonus_eligible ? (
              <p className="text-slate-600">Your packages have been recorded and you are eligible for the rescue bonus. Dispatch will verify and close the event.</p>
            ) : (
              <p className="text-slate-600">
                Your packages have been recorded. Because you indicated you did not take <strong>all</strong> remaining packages, this rescue has been flagged for dispatch review. A manager may reinstate your bonus eligibility.
              </p>
            )}
            <p className="mt-6 text-xs text-slate-400">Event ID: {eventId}</p>
          </div>
        </main>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <PageHeader title="Log Rescue Packages — Stage 2" showBack={false} />

      <main className="max-w-md mx-auto px-4 py-8">
        {loadError && (
          <div className="bg-red-50 border border-red-200 rounded p-4 text-red-700 text-sm mb-6">{loadError}</div>
        )}

        {/* Event summary card */}
        {event && (
          <div className="bg-amber-50 border border-amber-200 rounded-lg p-4 mb-6 text-sm">
            <p className="font-semibold text-amber-800 mb-1">Rescue Event: {event.event_id as string}</p>
            <p className="text-amber-700">Route: <strong>{event.rescued_route_id as string}</strong> — {event.rescued_driver_name as string}</p>
            <p className="text-amber-700">Type: {event.event_type as string}</p>
            <p className="text-amber-700">Reason: {event.reason_code as string}</p>
            {!!event.expected_packages && (
              <p className="text-amber-800 font-semibold mt-1">
                Expected packages: {event.expected_packages as number}
              </p>
            )}
          </div>
        )}

        <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6">
          <form onSubmit={handleSubmit} className="space-y-5">

            {/* Driver name — locked, populated from Stage 1 */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Rescuing Driver</label>
              <div className="w-full border border-slate-200 rounded px-3 py-2 text-sm bg-slate-50 text-slate-700 font-medium">
                {driverName || <span className="text-slate-400 font-normal">Loading...</span>}
              </div>
              <p className="text-xs text-slate-400 mt-1">Populated from Stage 1 assignment — contact dispatch if incorrect.</p>
            </div>

            {/* Packages */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Packages Taken</label>
              <input
                type="number"
                min="1"
                value={packages}
                onChange={(e) => setPackages(e.target.value)}
                placeholder="0"
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
              />
            </div>

            {/* Confirmation gate — not shown for Full Pull Assist (always eligible) */}
            {isFullPullAssist ? (
              <div className="bg-orange-50 border border-orange-200 rounded p-3 text-sm text-orange-800">
                <strong>Full Pull Assist</strong> — bonus eligible by default. Enter the packages you picked up above.
              </div>
            ) : (
              <div>
                <label className="block text-sm font-semibold text-slate-700 mb-2">
                  Did you take <span className="underline">all</span> remaining packages from this route?
                </label>
                <p className="text-xs text-slate-500 mb-3">
                  Answering No will flag this rescue for review and may affect bonus eligibility.
                </p>
                <div className="flex gap-3">
                  <button
                    type="button"
                    onClick={() => setConfirmedAll(true)}
                    className={`flex-1 py-3 rounded border font-semibold text-sm transition ${
                      confirmedAll === true
                        ? 'bg-green-600 text-white border-green-600'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-green-400'
                    }`}
                  >
                    Yes — I took everything
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmedAll(false)}
                    className={`flex-1 py-3 rounded border font-semibold text-sm transition ${
                      confirmedAll === false
                        ? 'bg-amber-500 text-white border-amber-500'
                        : 'bg-white text-slate-600 border-slate-300 hover:border-amber-400'
                    }`}
                  >
                    No — partial pickup
                  </button>
                </div>
              </div>
            )}

            {/* Observations */}
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">
                Observations <span className="font-normal text-slate-400">(optional)</span>
              </label>
              <textarea
                value={observations}
                onChange={(e) => setObservations(e.target.value)}
                rows={3}
                placeholder="Van condition, load quality, anything notable..."
                className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-amber-400"
              />
            </div>

            {submitError && <p className="text-sm text-red-600 font-medium">{submitError}</p>}

            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3 bg-amber-500 text-white rounded font-bold text-base hover:bg-amber-600 disabled:opacity-50"
            >
              {submitting ? 'Submitting...' : 'Submit Package Count'}
            </button>
          </form>
        </div>
      </main>
    </div>
  );
}
