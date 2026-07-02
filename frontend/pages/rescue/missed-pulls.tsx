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

interface MissedPull {
  contribution_id: string;
  event_id: string;
  event_date: string;
  event_type: string;
  rescued_route: string;
  rescued_driver: string;
  rescuing_driver: string;
  packages_reported: number;
  bonus_reinstated: boolean;
}

interface MissedReport {
  week_start: string;
  week_end: string;
  missed_pulls: MissedPull[];
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

export default function MissedPullsReport() {
  const router = useRouter();
  const [weekOf, setWeekOf] = useState(todayISO());
  const [report, setReport] = useState<MissedReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');

  const fetchReport = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${resolveApi()}/rescue/missed-pulls?week_of=${weekOf}`);
      if (!res.ok) { setError('Failed to load report.'); return; }
      setReport(await res.json());
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchReport(); }, [weekOf]);

  const exportCSV = () => {
    if (!report) return;
    const rows = [
      ['Event ID', 'Date', 'Type', 'Rescued Route', 'Rescued Driver', 'Rescuing Driver', 'Packages Reported', 'Reinstated'],
      ...report.missed_pulls.map((m) => [
        m.event_id, m.event_date, m.event_type, m.rescued_route,
        m.rescued_driver, m.rescuing_driver, m.packages_reported, m.bonus_reinstated ? 'Yes' : 'No',
      ]),
    ];
    const csv = rows.map((r) => r.join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `missed_pulls_${report.week_start}.csv`;
    a.click();
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Missed Pulls Report" showBack />

        <main className="max-w-4xl mx-auto px-4 py-8">
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-6 flex flex-wrap gap-4 items-end">
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Week of</label>
              <input
                type="date"
                value={weekOf}
                onChange={(e) => setWeekOf(e.target.value)}
                className="border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-orange-400"
              />
            </div>
            {report && report.missed_pulls.length > 0 && (
              <button
                onClick={exportCSV}
                className="px-4 py-2 bg-orange-600 text-white rounded font-semibold text-sm hover:bg-orange-700"
              >
                Export CSV
              </button>
            )}
            <button
              onClick={() => router.push('/rescue/payroll')}
              className="px-4 py-2 bg-green-600 text-white rounded font-semibold text-sm hover:bg-green-700"
            >
              Payroll Report
            </button>
          </div>

          {error && <p className="text-red-600 text-sm mb-4">{error}</p>}
          {loading && <p className="text-slate-400">Loading...</p>}

          {report && !loading && (
            <>
              <div className={`rounded-xl border p-5 mb-6 ${report.missed_pulls.length > 0 ? 'bg-orange-50 border-orange-200' : 'bg-green-50 border-green-200'}`}>
                <p className="text-sm mb-1" style={{ color: report.missed_pulls.length > 0 ? '#92400e' : '#14532d' }}>
                  Pay period: <strong>{report.week_start}</strong> — <strong>{report.week_end}</strong>
                </p>
                <p className={`text-2xl font-bold ${report.missed_pulls.length > 0 ? 'text-orange-800' : 'text-green-800'}`}>
                  {report.missed_pulls.length} missed pull{report.missed_pulls.length !== 1 ? 's' : ''}
                </p>
                <p className="text-sm mt-1" style={{ color: report.missed_pulls.length > 0 ? '#b45309' : '#166534' }}>
                  {report.missed_pulls.length > 0
                    ? 'Rescuing drivers did not confirm taking all packages. Admin review required to reinstate bonus.'
                    : 'All Full Pull / Rescue events were confirmed complete this week.'}
                </p>
              </div>

              {report.missed_pulls.length > 0 && (
                <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Date</th>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Type</th>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Route</th>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Rescued Driver</th>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Rescuing Driver</th>
                        <th className="text-right px-4 py-3 font-semibold text-slate-600">Pkgs</th>
                        <th className="text-center px-4 py-3 font-semibold text-slate-600">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {report.missed_pulls.map((m) => (
                        <tr key={m.contribution_id} className="hover:bg-orange-50">
                          <td className="px-4 py-3 text-slate-600">{m.event_date}</td>
                          <td className="px-4 py-3">
                            <span className={`px-2 py-0.5 rounded-full text-xs font-semibold ${m.event_type === 'Full Pull' ? 'bg-red-100 text-red-800' : 'bg-amber-100 text-amber-800'}`}>
                              {m.event_type}
                            </span>
                          </td>
                          <td className="px-4 py-3 font-mono text-slate-700">{m.rescued_route}</td>
                          <td className="px-4 py-3 text-slate-700">{m.rescued_driver}</td>
                          <td className="px-4 py-3 text-slate-700">{m.rescuing_driver}</td>
                          <td className="px-4 py-3 text-right text-slate-600">{m.packages_reported}</td>
                          <td className="px-4 py-3 text-center">
                            <button
                              onClick={() => router.push(`/rescue/close?eventId=${m.event_id}`)}
                              className="text-xs px-2 py-1 bg-slate-700 text-white rounded hover:bg-slate-900"
                            >
                              Review
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}

              <p className="text-xs text-slate-400 mt-4">
                Only shows Full Pull and Rescue events where the driver answered &quot;No&quot; to taking all packages and bonus has not been reinstated.
              </p>
            </>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
