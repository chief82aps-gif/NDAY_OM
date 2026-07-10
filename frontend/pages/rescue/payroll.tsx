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
  return 'http://127.0.0.1:8001';
}

interface DriverBonus {
  driver: string;
  bonus_eligible_packages: number;
  bonus_amount: number;
  bonus_paid: boolean;
  contribution_ids: string[];
  week_start: string;
  week_end: string;
}

interface PayrollReport {
  week_start: string;
  week_end: string;
  drivers: DriverBonus[];
  total_payout: number;
  all_paid: boolean;
}

function todayISO() {
  return new Date().toISOString().slice(0, 10);
}

export default function PayrollReport() {
  const router = useRouter();
  const [weekOf, setWeekOf] = useState(todayISO());
  const [report, setReport] = useState<PayrollReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [paying, setPaying] = useState<string | null>(null);

  const fetchReport = async () => {
    setLoading(true);
    setError('');
    try {
      const res = await fetch(`${resolveApi()}/rescue/payroll?week_of=${weekOf}`);
      if (!res.ok) { setError('Failed to load report.'); return; }
      setReport(await res.json());
    } catch {
      setError('Network error.');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { fetchReport(); }, [weekOf]);

  const markPaid = async (driver: string) => {
    setPaying(driver);
    try {
      const res = await fetch(`${resolveApi()}/rescue/payroll/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver, week_of: weekOf, confirmed_by: 'dispatch' }),
      });
      if (res.ok) await fetchReport();
      else setError('Failed to mark as paid.');
    } catch {
      setError('Network error.');
    } finally {
      setPaying(null);
    }
  };

  const exportCSV = () => {
    if (!report) return;
    const q = (v: string | number) => {
      const s = String(v);
      return s.includes(',') || s.includes('"') || s.includes('\n') ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const rows = [
      ['Driver', 'Bonus Eligible Packages', 'Bonus Amount', 'Week Start', 'Week End'],
      ...report.drivers.map((d) => [d.driver, d.bonus_eligible_packages, `$${d.bonus_amount}`, d.week_start, d.week_end]),
      ['TOTAL', '', `$${report.total_payout}`, report.week_start, report.week_end],
    ];
    const csv = rows.map((r) => r.map(q).join(',')).join('\n');
    const blob = new Blob([csv], { type: 'text/csv' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `rescue_bonus_${report.week_start}.csv`;
    a.click();
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Rescue Bonus — Payroll Report" showBack />

        <main className="max-w-3xl mx-auto px-4 py-8">
          {/* Week selector */}
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-5 mb-6 flex flex-wrap gap-4 items-end">
            <div>
              <label className="block text-sm font-semibold text-slate-700 mb-1">Week of</label>
              <input
                type="date"
                value={weekOf}
                onChange={(e) => setWeekOf(e.target.value)}
                className="border border-slate-300 rounded px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-green-400"
              />
              <p className="text-xs text-slate-400 mt-1">Any date within the target week (Sun–Sat).</p>
            </div>
            {report && (
              <button
                onClick={exportCSV}
                className="px-4 py-2 bg-green-600 text-white rounded font-semibold text-sm hover:bg-green-700"
              >
                Export CSV
              </button>
            )}
            <button
              onClick={() => router.push('/rescue/missed-pulls')}
              className="px-4 py-2 bg-orange-500 text-white rounded font-semibold text-sm hover:bg-orange-600"
            >
              Missed Pulls
            </button>
          </div>

          {error && <p className="text-red-600 text-sm mb-4">{error}</p>}
          {loading && <p className="text-slate-400">Loading...</p>}

          {report && !loading && (
            <>
              {/* Week summary */}
              <div className="bg-green-50 border border-green-200 rounded-xl p-5 mb-6">
                <p className="text-sm text-green-700 mb-1">
                  Pay period: <strong>{report.week_start}</strong> — <strong>{report.week_end}</strong>
                </p>
                <p className="text-3xl font-bold text-green-800">${report.total_payout} total payout</p>
                <p className="text-sm text-green-600 mt-1">{report.drivers.length} driver{report.drivers.length !== 1 ? 's' : ''} with bonus-eligible rescues</p>
              </div>

              {/* Driver rows */}
              {report.drivers.length === 0 ? (
                <div className="bg-white rounded-xl border border-slate-200 p-10 text-center text-slate-400">
                  No bonus-eligible rescues this week.
                </div>
              ) : (
                <div className="bg-white rounded-xl border border-slate-200 shadow-sm overflow-hidden">
                  <table className="w-full text-sm">
                    <thead className="bg-slate-50 border-b border-slate-200">
                      <tr>
                        <th className="text-left px-4 py-3 font-semibold text-slate-600">Driver</th>
                        <th className="text-right px-4 py-3 font-semibold text-slate-600">Eligible Packages</th>
                        <th className="text-right px-4 py-3 font-semibold text-slate-600">Bonus</th>
                        <th className="text-right px-4 py-3 font-semibold text-slate-600">Formula</th>
                        <th className="text-right px-4 py-3 font-semibold text-slate-600">Status</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-slate-100">
                      {report.drivers.map((d) => (
                        <tr key={d.driver} className={`hover:bg-slate-50 ${d.bonus_paid ? 'opacity-60' : ''}`}>
                          <td className="px-4 py-3 font-medium text-slate-800">{d.driver}</td>
                          <td className="px-4 py-3 text-right text-slate-600">{d.bonus_eligible_packages}</td>
                          <td className="px-4 py-3 text-right font-bold text-green-700">${d.bonus_amount}</td>
                          <td className="px-4 py-3 text-right text-slate-400 text-xs">
                            ⌊{d.bonus_eligible_packages} ÷ 40⌋ × $10
                          </td>
                          <td className="px-4 py-3 text-right">
                            {d.bonus_paid ? (
                              <span className="inline-block px-2 py-0.5 rounded-full text-xs font-semibold bg-green-100 text-green-700">Paid</span>
                            ) : (
                              <button
                                onClick={() => markPaid(d.driver)}
                                disabled={paying === d.driver}
                                className="px-3 py-1 text-xs bg-green-600 text-white rounded font-semibold hover:bg-green-700 disabled:opacity-50"
                              >
                                {paying === d.driver ? 'Saving...' : 'Mark Paid'}
                              </button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                    <tfoot className="border-t-2 border-slate-300 bg-slate-50">
                      <tr>
                        <td className="px-4 py-3 font-bold text-slate-800">Total</td>
                        <td className="px-4 py-3 text-right font-bold text-slate-800">
                          {report.drivers.reduce((s, d) => s + d.bonus_eligible_packages, 0)}
                        </td>
                        <td className="px-4 py-3 text-right font-bold text-green-700">${report.total_payout}</td>
                        <td />
                        <td className="px-4 py-3 text-right">
                          {report.all_paid && (
                            <span className="text-xs font-semibold text-green-600">All paid</span>
                          )}
                        </td>
                      </tr>
                    </tfoot>
                  </table>
                </div>
              )}

              <p className="text-xs text-slate-400 mt-4">
                Bonus formula: ⌊eligible packages ÷ 40⌋ × $10 per driver per week. Includes admin-reinstated events.
              </p>
            </>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
