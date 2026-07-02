'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../../components/PageHeader';
import { ProtectedRoute } from '../../components/ProtectedRoute';
import { useAuth } from '../../contexts/AuthContext';

interface RescueEvent {
  event_id: string;
  event_date: string;
  event_type: string;
  rescued_route_id: string;
  rescued_driver_name: string;
  rescuing_route_id: string | null;
  rescuing_driver_name: string | null;
  reason_code: string;
  opened_by: string;
  status: string;
  pad_sweep_package_count: number | null;
}

const TYPE_COLORS: Record<string, string> = {
  'Full Pull':        'bg-red-100 text-red-800',
  'Full Pull Assist': 'bg-orange-100 text-orange-800',
  'Rescue':           'bg-amber-100 text-amber-800',
  'Pad Sweep':        'bg-blue-100 text-blue-800',
};

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8000';
}

export default function RescueDashboard() {
  const router = useRouter();
  const { user } = useAuth();
  const [events, setEvents] = useState<RescueEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [statusFilter, setStatusFilter] = useState<'Open' | 'Closed' | ''>('Open');

  const isDispatch = user?.role === 'dispatcher' || user?.role === 'admin' || user?.role === 'manager';

  const fetchEvents = useCallback(async () => {
    setLoading(true);
    try {
      const params = statusFilter ? `?status=${statusFilter}` : '';
      const res = await fetch(`${resolveApi()}/rescue/events${params}`);
      if (res.ok) setEvents(await res.json());
    } finally {
      setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => { fetchEvents(); }, [fetchEvents]);

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Rescue Tracker" showBack />

        <main className="max-w-5xl mx-auto px-4 py-8">
          {/* Actions */}
          <div className="flex flex-wrap gap-3 mb-6">
            {isDispatch && (
              <button
                onClick={() => router.push('/rescue/open')}
                className="px-4 py-2 bg-red-600 text-white rounded font-semibold hover:bg-red-700"
              >
                + Open Rescue
              </button>
            )}
            <button
              onClick={() => router.push('/rescue/payroll')}
              className="px-4 py-2 bg-green-600 text-white rounded font-semibold hover:bg-green-700"
            >
              Payroll Report
            </button>
            {isDispatch && (
              <button
                onClick={() => router.push('/rescue/missed-pulls')}
                className="px-4 py-2 bg-orange-600 text-white rounded font-semibold hover:bg-orange-700"
              >
                Missed Pulls Report
              </button>
            )}
            {isDispatch && (
              <button
                onClick={() => router.push('/rescue/roster')}
                className="px-4 py-2 bg-slate-600 text-white rounded font-semibold hover:bg-slate-700"
              >
                Driver Roster
              </button>
            )}
            {isDispatch && (
              <button
                onClick={() => router.push('/daily-notify')}
                className="px-4 py-2 bg-indigo-600 text-white rounded font-semibold hover:bg-indigo-700"
              >
                📨 Daily Notifications
              </button>
            )}
          </div>

          {/* Filter */}
          <div className="flex gap-2 mb-4">
            {(['Open', 'Closed', ''] as const).map((s) => (
              <button
                key={s}
                onClick={() => setStatusFilter(s)}
                className={`px-3 py-1 rounded text-sm font-medium border ${
                  statusFilter === s
                    ? 'bg-slate-700 text-white border-slate-700'
                    : 'bg-white text-slate-600 border-slate-300 hover:border-slate-500'
                }`}
              >
                {s === '' ? 'All' : s}
              </button>
            ))}
            <button onClick={fetchEvents} className="ml-auto text-sm text-slate-500 hover:text-slate-800">
              Refresh
            </button>
          </div>

          {/* Event list */}
          {loading ? (
            <p className="text-slate-500">Loading...</p>
          ) : events.length === 0 ? (
            <div className="bg-white rounded-lg border border-slate-200 p-10 text-center text-slate-400">
              No rescue events found.
            </div>
          ) : (
            <div className="space-y-3">
              {events.map((ev) => (
                <div
                  key={ev.event_id}
                  className="bg-white rounded-lg border border-slate-200 p-4 shadow-sm hover:shadow-md transition"
                >
                  <div className="flex flex-wrap items-center justify-between gap-2">
                    <div className="flex items-center gap-3">
                      <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${TYPE_COLORS[ev.event_type] ?? 'bg-gray-100 text-gray-700'}`}>
                        {ev.event_type}
                      </span>
                      <span className="font-mono text-sm text-slate-500">{ev.event_id}</span>
                      <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${ev.status === 'Open' ? 'bg-green-100 text-green-700' : 'bg-slate-100 text-slate-500'}`}>
                        {ev.status}
                      </span>
                    </div>
                    <span className="text-sm text-slate-400">{ev.event_date}</span>
                  </div>

                  <div className="mt-2 grid grid-cols-2 gap-x-6 gap-y-1 text-sm">
                    <div><span className="text-slate-400">Rescued route:</span> <span className="font-medium">{ev.rescued_route_id}</span> — {ev.rescued_driver_name}</div>
                    {ev.rescuing_driver_name && (
                      <div><span className="text-slate-400">Rescuing:</span> <span className="font-medium">{ev.rescuing_route_id}</span> — {ev.rescuing_driver_name}</div>
                    )}
                    {ev.pad_sweep_package_count != null && (
                      <div><span className="text-slate-400">Packages swept:</span> <span className="font-medium">{ev.pad_sweep_package_count}</span></div>
                    )}
                    <div><span className="text-slate-400">Reason:</span> {ev.reason_code}</div>
                    <div><span className="text-slate-400">Opened by:</span> {ev.opened_by}</div>
                  </div>

                  {isDispatch && ev.status === 'Open' && ev.event_type !== 'Pad Sweep' && (
                    <div className="mt-3 flex gap-2">
                      <button
                        onClick={() => router.push(`/rescue/close?eventId=${ev.event_id}`)}
                        className="px-3 py-1 text-sm bg-slate-700 text-white rounded hover:bg-slate-900"
                      >
                        Close Event
                      </button>
                      <button
                        onClick={() => {
                          const url = `/rescue/contribute?eventId=${ev.event_id}&routeId=${ev.rescued_route_id}`;
                          navigator.clipboard.writeText(window.location.origin + url);
                        }}
                        className="px-3 py-1 text-sm border border-slate-300 rounded hover:bg-slate-50"
                      >
                        Copy Stage 2 Link
                      </button>
                    </div>
                  )}
                </div>
              ))}
            </div>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
