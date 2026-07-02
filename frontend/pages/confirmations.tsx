'use client';

import { useState, useEffect, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface Assignment {
  id: number;
  driver_name: string;
  route_code: string;
  van_number: string;
  stage_location: string;
  wave: string;
  packages: number | null;
  dm_sent: boolean;
  dm_sent_at: string | null;
  acknowledged: boolean;
  acknowledged_at: string | null;
}

interface StatusResponse {
  date: string;
  total: number;
  dm_sent: number;
  confirmed: number;
  pending: number;
  assignments: Assignment[];
}

function fmt(iso: string | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

function fmtDate(iso: string) {
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', {
    weekday: 'long', month: 'long', day: 'numeric',
  });
}

function ConfirmationsContent() {
  const [data, setData] = useState<StatusResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  const [selectedDate, setSelectedDate] = useState('');
  const [filter, setFilter] = useState<'all' | 'confirmed' | 'pending'>('all');

  const load = useCallback(async (d?: string) => {
    setLoading(true);
    setError('');
    try {
      const url = d
        ? `${resolveApi()}/daily-notify/today-status?for_date=${d}`
        : `${resolveApi()}/daily-notify/today-status`;
      const res = await fetch(url, { credentials: 'include' });
      if (!res.ok) throw new Error(`Server error ${res.status}`);
      setData(await res.json());
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to load');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleDateChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setSelectedDate(e.target.value);
    load(e.target.value);
  };

  const filtered = data?.assignments.filter(a => {
    if (filter === 'confirmed') return a.acknowledged;
    if (filter === 'pending') return !a.acknowledged;
    return true;
  }) ?? [];

  const confirmPct = data && data.total > 0
    ? Math.round((data.confirmed / data.total) * 100)
    : 0;

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      <PageHeader title="Confirmation Dashboard" subtitle="Driver attendance confirmations" />

      <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">

        {/* Date picker + refresh */}
        <div className="flex flex-wrap items-center gap-3">
          <input
            type="date"
            value={selectedDate}
            onChange={handleDateChange}
            className="bg-slate-800 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
          />
          <button
            onClick={() => load(selectedDate || undefined)}
            className="bg-blue-600 hover:bg-blue-500 px-4 py-2 rounded-lg text-sm font-medium"
          >
            Refresh
          </button>
          {data && (
            <span className="text-slate-400 text-sm">{fmtDate(data.date)}</span>
          )}
        </div>

        {/* Summary cards */}
        {data && (
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            {[
              { label: 'Total Drivers', value: data.total, color: 'bg-slate-700' },
              { label: 'DM Sent', value: data.dm_sent, color: 'bg-blue-900' },
              { label: 'Confirmed', value: data.confirmed, color: 'bg-green-900' },
              { label: 'Pending', value: data.pending, color: 'bg-amber-900' },
            ].map(c => (
              <div key={c.label} className={`${c.color} rounded-xl p-4 text-center`}>
                <div className="text-2xl font-bold">{c.value}</div>
                <div className="text-xs text-slate-300 mt-1">{c.label}</div>
              </div>
            ))}
          </div>
        )}

        {/* Progress bar */}
        {data && data.total > 0 && (
          <div>
            <div className="flex justify-between text-xs text-slate-400 mb-1">
              <span>Confirmation rate</span>
              <span>{confirmPct}%</span>
            </div>
            <div className="w-full bg-slate-700 rounded-full h-3">
              <div
                className="bg-green-500 h-3 rounded-full transition-all duration-500"
                style={{ width: `${confirmPct}%` }}
              />
            </div>
          </div>
        )}

        {/* Filter tabs */}
        <div className="flex gap-2">
          {(['all', 'confirmed', 'pending'] as const).map(f => (
            <button
              key={f}
              onClick={() => setFilter(f)}
              className={`px-4 py-1.5 rounded-full text-sm font-medium capitalize transition-colors ${
                filter === f
                  ? 'bg-blue-600 text-white'
                  : 'bg-slate-700 text-slate-300 hover:bg-slate-600'
              }`}
            >
              {f} {f === 'all' ? `(${data?.total ?? 0})` : f === 'confirmed' ? `(${data?.confirmed ?? 0})` : `(${data?.pending ?? 0})`}
            </button>
          ))}
        </div>

        {/* Loading / error */}
        {loading && <p className="text-slate-400 text-sm animate-pulse">Loading…</p>}
        {error && <p className="text-red-400 text-sm">{error}</p>}

        {/* Driver list */}
        {!loading && data && (
          <div className="space-y-2">
            {filtered.length === 0 && (
              <p className="text-slate-500 text-sm text-center py-8">No assignments found.</p>
            )}
            {filtered.map(a => (
              <div
                key={a.id}
                className={`rounded-xl border px-4 py-3 flex flex-wrap items-center gap-3 ${
                  a.acknowledged
                    ? 'bg-green-900/20 border-green-700/40'
                    : 'bg-slate-800 border-slate-700'
                }`}
              >
                {/* Status badge */}
                <span className={`text-lg ${a.acknowledged ? '✅' : '⏳'}`}>
                  {a.acknowledged ? '✅' : '⏳'}
                </span>

                {/* Driver name */}
                <div className="flex-1 min-w-0">
                  <p className="font-semibold text-sm truncate">{a.driver_name}</p>
                  <p className="text-xs text-slate-400">
                    {[a.route_code, a.van_number, a.stage_location].filter(Boolean).join(' · ')}
                  </p>
                </div>

                {/* Wave */}
                {a.wave && (
                  <span className="text-xs bg-slate-700 px-2 py-0.5 rounded-full">
                    Wave {a.wave}
                  </span>
                )}

                {/* Packages */}
                {a.packages && (
                  <span className="text-xs text-slate-400">📦 {a.packages}</span>
                )}

                {/* DM sent time */}
                <div className="text-xs text-slate-400 text-right">
                  <div>{a.dm_sent ? `DM ${fmt(a.dm_sent_at)}` : 'DM not sent'}</div>
                  <div className={a.acknowledged ? 'text-green-400' : 'text-amber-400'}>
                    {a.acknowledged ? `Confirmed ${fmt(a.acknowledged_at)}` : 'Awaiting confirmation'}
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

export default function ConfirmationsPage() {
  return (
    <ProtectedRoute>
      <ConfirmationsContent />
    </ProtectedRoute>
  );
}
