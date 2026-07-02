'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../../components/PageHeader';
import { ProtectedRoute } from '../../components/ProtectedRoute';
import { useAuth } from '../../contexts/AuthContext';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8000';
}

interface Contribution {
  contribution_id: string;
  rescuing_driver_name: string;
  packages_taken: number;
  confirmed_all_taken: boolean;
  bonus_eligible: boolean;
  bonus_reinstated: boolean;
  verified: string;
  observations: string | null;
}

interface EventDetail {
  event_id: string;
  event_date: string;
  event_type: string;
  rescued_route_id: string;
  rescued_driver_name: string;
  rescuing_route_id: string | null;
  rescuing_driver_name: string | null;
  reason_code: string;
  status: string;
  contributions: Contribution[];
}

export default function CloseRescue() {
  const router = useRouter();
  const { user } = useAuth();
  const { eventId } = router.query as { eventId?: string };

  const [event, setEvent] = useState<EventDetail | null>(null);
  const [loadError, setLoadError] = useState('');
  const [closeNotes, setCloseNotes] = useState('');
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState('');
  const [closed, setClosed] = useState(false);

  // Reinstatement state
  const [reinstating, setReinstating] = useState<string | null>(null);
  const [reinstateReason, setReinstateReason] = useState('');
  const [reinstateError, setReinstateError] = useState('');

  const isAdmin = user?.role === 'admin' || user?.role === 'manager';

  useEffect(() => {
    if (!eventId) return;
    fetch(`${resolveApi()}/rescue/events/${eventId}`)
      .then((r) => (r.ok ? r.json() : Promise.reject()))
      .then(setEvent)
      .catch(() => setLoadError('Could not load event.'));
  }, [eventId]);

  const handleClose = async (e: React.FormEvent) => {
    e.preventDefault();
    setSubmitError('');
    setSubmitting(true);
    try {
      const res = await fetch(`${resolveApi()}/rescue/events/${eventId}/close`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ closed_by: user?.username ?? 'dispatch', close_notes: closeNotes || null }),
      });
      if (!res.ok) { setSubmitError('Failed to close event.'); return; }
      setClosed(true);
    } catch {
      setSubmitError('Network error.');
    } finally {
      setSubmitting(false);
    }
  };

  const handleReinstate = async (contributionId: string) => {
    if (!reinstateReason.trim()) { setReinstateError('A reason is required to reinstate.'); return; }
    setReinstateError('');
    try {
      const res = await fetch(`${resolveApi()}/rescue/contributions/${contributionId}/reinstate`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ reinstated_by: user?.username ?? 'admin', reinstatement_reason: reinstateReason }),
      });
      if (!res.ok) { setReinstateError('Reinstatement failed.'); return; }
      // Refresh event
      const updated = await fetch(`${resolveApi()}/rescue/events/${eventId}`).then((r) => r.json());
      setEvent(updated);
      setReinstating(null);
      setReinstateReason('');
    } catch {
      setReinstateError('Network error.');
    }
  };

  if (closed) {
    return (
      <ProtectedRoute>
        <div className="min-h-screen bg-gray-50">
          <PageHeader title="Event Closed" showBack />
          <main className="max-w-md mx-auto px-4 py-12 text-center">
            <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-8">
              <div className="text-5xl mb-4">🔒</div>
              <h2 className="text-2xl font-bold text-slate-900 mb-2">Rescue Closed</h2>
              <p className="text-slate-500 mb-6">All contributions have been verified.</p>
              <button onClick={() => router.push('/rescue')} className="w-full py-2 bg-slate-700 text-white rounded font-semibold hover:bg-slate-900">
                Back to Dashboard
              </button>
            </div>
          </main>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Close Rescue — Stage 3" showBack />

        <main className="max-w-2xl mx-auto px-4 py-8 space-y-6">
          {loadError && <p className="text-red-600 text-sm">{loadError}</p>}

          {event && (
            <>
              {/* Event summary */}
              <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
                <h2 className="font-bold text-slate-900 text-lg mb-3">Event Summary</h2>
                <div className="grid grid-cols-2 gap-2 text-sm">
                  <div><span className="text-slate-400">Event ID:</span> <span className="font-mono">{event.event_id}</span></div>
                  <div><span className="text-slate-400">Date:</span> {event.event_date}</div>
                  <div><span className="text-slate-400">Type:</span> {event.event_type}</div>
                  <div><span className="text-slate-400">Status:</span> {event.status}</div>
                  <div><span className="text-slate-400">Rescued route:</span> {event.rescued_route_id} — {event.rescued_driver_name}</div>
                  <div><span className="text-slate-400">Reason:</span> {event.reason_code}</div>
                </div>
              </div>

              {/* Contributions */}
              {event.contributions.length > 0 && (
                <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5">
                  <h2 className="font-bold text-slate-900 text-lg mb-3">Contributions</h2>
                  <div className="space-y-4">
                    {event.contributions.map((c) => (
                      <div key={c.contribution_id} className={`border rounded-lg p-4 ${c.bonus_eligible || c.bonus_reinstated ? 'border-green-200 bg-green-50' : 'border-amber-200 bg-amber-50'}`}>
                        <div className="flex items-center justify-between mb-2">
                          <span className="font-semibold text-slate-800">{c.rescuing_driver_name}</span>
                          <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${c.bonus_eligible || c.bonus_reinstated ? 'bg-green-200 text-green-800' : 'bg-amber-200 text-amber-800'}`}>
                            {c.bonus_reinstated ? 'Reinstated' : c.bonus_eligible ? 'Bonus Eligible' : 'Not Eligible'}
                          </span>
                        </div>
                        <div className="text-sm text-slate-600 space-y-0.5">
                          <p>Packages taken: <strong>{c.packages_taken}</strong></p>
                          <p>Confirmed all taken: <strong>{c.confirmed_all_taken ? 'Yes' : 'No'}</strong></p>
                          {c.observations && <p>Notes: {c.observations}</p>}
                        </div>

                        {/* Admin reinstate button */}
                        {isAdmin && !c.bonus_eligible && !c.bonus_reinstated && (
                          <div className="mt-3">
                            {reinstating === c.contribution_id ? (
                              <div className="space-y-2">
                                <textarea
                                  value={reinstateReason}
                                  onChange={(e) => setReinstateReason(e.target.value)}
                                  placeholder="Required: reason for reinstatement..."
                                  rows={2}
                                  className="w-full border border-slate-300 rounded px-3 py-2 text-sm"
                                />
                                {reinstateError && <p className="text-xs text-red-600">{reinstateError}</p>}
                                <div className="flex gap-2">
                                  <button
                                    onClick={() => handleReinstate(c.contribution_id)}
                                    className="px-3 py-1 text-sm bg-green-600 text-white rounded hover:bg-green-700"
                                  >
                                    Confirm Reinstatement
                                  </button>
                                  <button
                                    onClick={() => { setReinstating(null); setReinstateReason(''); setReinstateError(''); }}
                                    className="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-50"
                                  >
                                    Cancel
                                  </button>
                                </div>
                              </div>
                            ) : (
                              <button
                                onClick={() => setReinstating(c.contribution_id)}
                                className="text-sm text-green-700 underline hover:text-green-900"
                              >
                                Reinstate Bonus Eligibility
                              </button>
                            )}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Close form */}
              {event.status === 'Open' && (
                <form onSubmit={handleClose} className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 space-y-4">
                  <h2 className="font-bold text-slate-900 text-lg">Close Event</h2>
                  <div>
                    <label className="block text-sm font-semibold text-slate-700 mb-1">Closing Notes <span className="font-normal text-slate-400">(optional)</span></label>
                    <textarea
                      value={closeNotes}
                      onChange={(e) => setCloseNotes(e.target.value)}
                      rows={3}
                      placeholder="Final notes, discrepancies, or observations..."
                      className="w-full border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-slate-400"
                    />
                  </div>
                  {submitError && <p className="text-sm text-red-600">{submitError}</p>}
                  <button
                    type="submit"
                    disabled={submitting}
                    className="w-full py-3 bg-slate-800 text-white rounded font-bold hover:bg-slate-900 disabled:opacity-50"
                  >
                    {submitting ? 'Closing...' : 'Close and Verify Rescue'}
                  </button>
                </form>
              )}

              {event.status === 'Closed' && (
                <div className="bg-slate-100 rounded-xl border border-slate-200 p-5 text-center text-slate-500">
                  This event is already closed.
                </div>
              )}
            </>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
