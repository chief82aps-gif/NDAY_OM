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

const EVENT_TYPES = ['call_in', 'no_show', 'late_arrival', 'early_departure', 'present', 'excused'];
const REASON_CODES = ['sick', 'personal', 'family', 'weather', 'transportation', 'no_call', 'other'];
const WAVES = ['1020', '1025', '1045', '1050', '1100', '1115'];

const EVENT_LABELS: Record<string, string> = {
  call_in: '📞 Call-In',
  no_show: '🚫 No Show',
  late_arrival: '⏰ Late Arrival',
  early_departure: '🚪 Early Departure',
  present: '✅ Present',
  excused: '📋 Excused',
};

const EVENT_COLORS: Record<string, string> = {
  call_in: 'bg-amber-900/30 border-amber-700/50 text-amber-300',
  no_show: 'bg-red-900/30 border-red-700/50 text-red-300',
  late_arrival: 'bg-orange-900/30 border-orange-700/50 text-orange-300',
  early_departure: 'bg-yellow-900/30 border-yellow-700/50 text-yellow-300',
  present: 'bg-green-900/30 border-green-700/50 text-green-300',
  excused: 'bg-blue-900/30 border-blue-700/50 text-blue-300',
};

interface AttendanceEvent {
  id: number;
  driver_name: string;
  event_date: string;
  event_type: string;
  reason_code: string | null;
  call_time: string | null;
  scheduled_wave: string | null;
  hours_before_shift: number | null;
  compliant: boolean | null;
  is_missed: boolean;
  missed_shift_count: number;
  voluntary_resign_flag: boolean;
  notes: string | null;
  logged_by: string | null;
  ringcentral_call_id: string | null;
  created_at: string;
}

interface TodayResponse {
  date: string;
  total: number;
  events: AttendanceEvent[];
}

interface MissedResponse {
  days: number;
  flagged_count: number;
  drivers: { driver_name: string; missed_shifts: number; voluntary_resign_risk: boolean }[];
}

function fmt(iso: string | null) {
  if (!iso) return '—';
  return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
}

interface RosterDriver {
  id: number;
  payroll_name: string;
  has_pin: boolean;
}

function AttendanceContent() {
  const [tab, setTab] = useState<'log' | 'today' | 'missed' | 'pins'>('today');
  const [todayData, setTodayData] = useState<TodayResponse | null>(null);
  const [missedData, setMissedData] = useState<MissedResponse | null>(null);
  const [rosterDrivers, setRosterDrivers] = useState<RosterDriver[]>([]);
  const [pinInputs, setPinInputs] = useState<Record<number, string>>({});
  const [pinStatus, setPinStatus] = useState<Record<number, 'saving' | 'saved' | 'error'>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [alert, setAlert] = useState('');

  // Log form state
  const [form, setForm] = useState({
    driver_name: '',
    event_type: 'call_in',
    reason_code: '',
    call_time: '',
    scheduled_wave: '',
    notes: '',
    logged_by: '',
  });
  const [submitting, setSubmitting] = useState(false);

  const loadToday = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${resolveApi()}/attendance/today`, { credentials: 'include' });
      if (res.ok) setTodayData(await res.json());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  const loadMissed = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${resolveApi()}/attendance/missed-shifts`, { credentials: 'include' });
      if (res.ok) setMissedData(await res.json());
    } catch { /* ignore */ }
    finally { setLoading(false); }
  }, []);

  const loadRoster = useCallback(async () => {
    try {
      const res = await fetch(`${resolveApi()}/attendance/roster-list`, { credentials: 'include' });
      if (res.ok) {
        const data = await res.json();
        setRosterDrivers(data.drivers ?? []);
      }
    } catch { /* ignore */ }
  }, []);

  useEffect(() => {
    if (tab === 'today') loadToday();
    if (tab === 'missed') loadMissed();
    if (tab === 'pins') loadRoster();
  }, [tab, loadToday, loadMissed, loadRoster]);

  const savePin = async (driver: RosterDriver) => {
    const pin = pinInputs[driver.id] ?? '';
    if (!/^\d{4}$/.test(pin)) return;
    setPinStatus(s => ({ ...s, [driver.id]: 'saving' }));
    try {
      const res = await fetch(`${resolveApi()}/attendance/roster/${driver.id}/pin`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ ssn_last4: pin }),
      });
      if (res.ok) {
        setPinStatus(s => ({ ...s, [driver.id]: 'saved' }));
        setRosterDrivers(d => d.map(r => r.id === driver.id ? { ...r, has_pin: true } : r));
        setPinInputs(p => { const n = { ...p }; delete n[driver.id]; return n; });
        setTimeout(() => setPinStatus(s => { const n = { ...s }; delete n[driver.id]; return n; }), 2000);
      } else {
        setPinStatus(s => ({ ...s, [driver.id]: 'error' }));
      }
    } catch {
      setPinStatus(s => ({ ...s, [driver.id]: 'error' }));
    }
  };

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!form.driver_name.trim()) { setError('Driver name is required.'); return; }
    setSubmitting(true); setError(''); setSuccess(''); setAlert('');
    try {
      const res = await fetch(`${resolveApi()}/attendance/log`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          driver_name: form.driver_name.trim(),
          event_type: form.event_type,
          reason_code: form.reason_code || undefined,
          call_time: form.call_time || undefined,
          scheduled_wave: form.scheduled_wave || undefined,
          notes: form.notes || undefined,
          logged_by: form.logged_by || undefined,
        }),
      });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail ?? 'Failed to log');
      setSuccess(`✅ Logged ${form.event_type.replace('_', ' ')} for ${form.driver_name}`);
      if (data.alert) setAlert(data.alert);
      if (data.compliance_alert) setAlert(prev => prev + '\n' + data.compliance_alert);
      setForm({ driver_name: '', event_type: 'call_in', reason_code: '', call_time: '', scheduled_wave: '', notes: '', logged_by: '' });
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Error logging attendance');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      <PageHeader title="Attendance Tracker" />

      <div className="max-w-4xl mx-auto px-4 py-6 space-y-6">

        {/* Tabs */}
        <div className="flex flex-wrap gap-2 border-b border-slate-700 pb-1">
          {[
            { key: 'today', label: "Today's Events" },
            { key: 'log', label: 'Log Event' },
            { key: 'missed', label: '⚠️ Missed Shifts' },
            { key: 'pins', label: '🔑 Manage PINs' },
          ].map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key as typeof tab)}
              className={`px-4 py-2 text-sm font-medium rounded-t transition-colors ${
                tab === t.key
                  ? 'bg-slate-700 text-white'
                  : 'text-slate-400 hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* ── LOG EVENT TAB ── */}
        {tab === 'log' && (
          <form onSubmit={handleSubmit} className="bg-slate-800 rounded-xl p-6 space-y-4">
            <h2 className="font-semibold text-slate-200">Log Attendance Event</h2>

            {error && <p className="text-red-400 text-sm bg-red-900/20 border border-red-700 rounded-lg px-3 py-2">{error}</p>}
            {success && <p className="text-green-400 text-sm bg-green-900/20 border border-green-700 rounded-lg px-3 py-2">{success}</p>}
            {alert && (
              <div className="text-amber-300 text-sm bg-amber-900/20 border border-amber-700 rounded-lg px-3 py-2 whitespace-pre-line">
                {alert}
              </div>
            )}

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Driver Name *</label>
                <input
                  value={form.driver_name}
                  onChange={e => setForm(f => ({ ...f, driver_name: e.target.value }))}
                  placeholder="Last, First"
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                  required
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">Event Type *</label>
                <select
                  value={form.event_type}
                  onChange={e => setForm(f => ({ ...f, event_type: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                >
                  {EVENT_TYPES.map(t => (
                    <option key={t} value={t}>{EVENT_LABELS[t] ?? t}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">Reason Code</label>
                <select
                  value={form.reason_code}
                  onChange={e => setForm(f => ({ ...f, reason_code: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="">— Select reason —</option>
                  {REASON_CODES.map(r => (
                    <option key={r} value={r}>{r.replace('_', ' ')}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">Scheduled Wave</label>
                <select
                  value={form.scheduled_wave}
                  onChange={e => setForm(f => ({ ...f, scheduled_wave: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                >
                  <option value="">— Select wave —</option>
                  {WAVES.map(w => (
                    <option key={w} value={w}>{w}</option>
                  ))}
                </select>
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">Call Time (for 4-hr rule)</label>
                <input
                  type="datetime-local"
                  value={form.call_time}
                  onChange={e => setForm(f => ({ ...f, call_time: e.target.value }))}
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                />
              </div>

              <div>
                <label className="text-xs text-slate-400 block mb-1">Logged By</label>
                <input
                  value={form.logged_by}
                  onChange={e => setForm(f => ({ ...f, logged_by: e.target.value }))}
                  placeholder="Dispatcher / OM name"
                  className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
                />
              </div>
            </div>

            <div>
              <label className="text-xs text-slate-400 block mb-1">Notes</label>
              <textarea
                value={form.notes}
                onChange={e => setForm(f => ({ ...f, notes: e.target.value }))}
                rows={2}
                placeholder="Additional context..."
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm"
              />
            </div>

            <button
              type="submit"
              disabled={submitting}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 py-2.5 rounded-lg font-medium text-sm"
            >
              {submitting ? 'Logging…' : 'Log Attendance Event'}
            </button>
          </form>
        )}

        {/* ── TODAY TAB ── */}
        {tab === 'today' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-400">
                {todayData ? `${todayData.total} event${todayData.total !== 1 ? 's' : ''} logged today` : ''}
              </p>
              <button onClick={loadToday} className="text-xs text-blue-400 hover:text-blue-300">
                Refresh
              </button>
            </div>

            {loading && <p className="text-slate-400 text-sm animate-pulse">Loading…</p>}

            {!loading && todayData?.events.length === 0 && (
              <div className="text-center py-12 text-slate-500">
                <p className="text-3xl mb-3">📋</p>
                <p className="text-sm">No attendance events logged today.</p>
                <button
                  onClick={() => setTab('log')}
                  className="mt-3 text-blue-400 hover:text-blue-300 text-sm"
                >
                  Log an event →
                </button>
              </div>
            )}

            {todayData?.events.map(e => (
              <div
                key={e.id}
                className={`rounded-xl border px-4 py-3 ${EVENT_COLORS[e.event_type] ?? 'bg-slate-800 border-slate-700'}`}
              >
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div>
                    <p className="font-semibold text-sm">{e.driver_name}</p>
                    <p className="text-xs opacity-80 mt-0.5">
                      {EVENT_LABELS[e.event_type] ?? e.event_type}
                      {e.reason_code ? ` · ${e.reason_code.replace('_', ' ')}` : ''}
                      {e.scheduled_wave ? ` · Wave ${e.scheduled_wave}` : ''}
                    </p>
                  </div>
                  <div className="text-right text-xs opacity-70">
                    <div>{e.call_time ? `Called: ${fmt(e.call_time)}` : ''}</div>
                    <div>Logged: {fmt(e.created_at)}</div>
                    {e.logged_by && <div>By: {e.logged_by}</div>}
                  </div>
                </div>

                {/* Compliance + flags */}
                <div className="flex flex-wrap gap-2 mt-2">
                  {e.compliant === false && (
                    <span className="text-xs bg-red-700/50 text-red-200 px-2 py-0.5 rounded-full">
                      ⚠️ Non-compliant — {Math.abs(e.hours_before_shift ?? 0).toFixed(1)}h before shift
                    </span>
                  )}
                  {e.compliant === true && (
                    <span className="text-xs bg-green-700/50 text-green-200 px-2 py-0.5 rounded-full">
                      ✅ Compliant — {(e.hours_before_shift ?? 0).toFixed(1)}h before shift
                    </span>
                  )}
                  {e.voluntary_resign_flag && (
                    <span className="text-xs bg-red-800/70 text-red-200 px-2 py-0.5 rounded-full font-semibold">
                      🚨 2+ Missed — Voluntary Resignation Risk
                    </span>
                  )}
                  {e.ringcentral_call_id && (
                    <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full">
                      📞 RingCentral
                    </span>
                  )}
                </div>

                {e.notes && (
                  <p className="text-xs opacity-70 mt-2 italic">{e.notes}</p>
                )}
              </div>
            ))}
          </div>
        )}

        {/* ── MISSED SHIFTS TAB ── */}
        {tab === 'missed' && (
          <div className="space-y-3">
            <p className="text-sm text-slate-400">
              Drivers with 2+ missed shifts in the last 90 days — potential voluntary resignation per handbook.
            </p>

            {loading && <p className="text-slate-400 text-sm animate-pulse">Loading…</p>}

            {!loading && missedData?.flagged_count === 0 && (
              <div className="text-center py-12 text-slate-500">
                <p className="text-3xl mb-3">✅</p>
                <p className="text-sm">No drivers currently flagged for missed shifts.</p>
              </div>
            )}

            {missedData?.drivers.map(d => (
              <div
                key={d.driver_name}
                className={`rounded-xl border px-4 py-3 flex items-center justify-between ${
                  d.voluntary_resign_risk
                    ? 'bg-red-900/30 border-red-700/50'
                    : 'bg-amber-900/20 border-amber-700/40'
                }`}
              >
                <div>
                  <p className="font-semibold text-sm">{d.driver_name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    {d.missed_shifts} missed shift{d.missed_shifts !== 1 ? 's' : ''} in 90 days
                  </p>
                </div>
                {d.voluntary_resign_risk && (
                  <span className="text-xs bg-red-700 text-white px-3 py-1 rounded-full font-semibold">
                    🚨 Resign Risk
                  </span>
                )}
              </div>
            ))}
          </div>
        )}

        {/* ── MANAGE PINs TAB ── */}
        {tab === 'pins' && (
          <div className="space-y-4">
            <div className="bg-blue-900/20 border border-blue-700/40 rounded-xl p-4 text-sm text-blue-200">
              <p className="font-semibold mb-1">Driver Callout Page PINs</p>
              <p className="text-blue-300 text-xs">
                Each driver uses their last 4 SSN digits (same as ADP kiosk) to submit call-outs
                at <span className="font-mono">nday-om.vercel.app/callout</span>.
                Set their PIN here once — they never need to change it.
              </p>
            </div>

            <div className="flex items-center justify-between">
              <p className="text-sm text-slate-400">
                {rosterDrivers.filter(d => d.has_pin).length} of {rosterDrivers.length} drivers have a PIN set
              </p>
              <button onClick={loadRoster} className="text-xs text-blue-400 hover:text-blue-300">Refresh</button>
            </div>

            <div className="space-y-2">
              {rosterDrivers.map(driver => (
                <div
                  key={driver.id}
                  className={`rounded-xl border px-4 py-3 flex flex-wrap items-center gap-3 ${
                    driver.has_pin
                      ? 'bg-green-900/10 border-green-800/40'
                      : 'bg-slate-800 border-slate-700'
                  }`}
                >
                  <div className="flex-1 min-w-0">
                    <p className="text-sm font-medium">{driver.payroll_name}</p>
                    <p className={`text-xs mt-0.5 ${driver.has_pin ? 'text-green-400' : 'text-slate-500'}`}>
                      {driver.has_pin ? '✓ PIN set' : 'No PIN — cannot use callout page'}
                    </p>
                  </div>
                  <div className="flex items-center gap-2">
                    <input
                      type="password"
                      inputMode="numeric"
                      maxLength={4}
                      placeholder="••••"
                      value={pinInputs[driver.id] ?? ''}
                      onChange={e => {
                        const v = e.target.value.replace(/\D/g, '').slice(0, 4);
                        setPinInputs(p => ({ ...p, [driver.id]: v }));
                      }}
                      className="w-20 bg-slate-700 border border-slate-600 rounded-lg px-2 py-1.5 text-sm text-center tracking-widest"
                    />
                    <button
                      onClick={() => savePin(driver)}
                      disabled={!/^\d{4}$/.test(pinInputs[driver.id] ?? '') || pinStatus[driver.id] === 'saving'}
                      className="bg-blue-600 hover:bg-blue-500 disabled:opacity-40 text-white text-xs font-medium px-3 py-1.5 rounded-lg"
                    >
                      {pinStatus[driver.id] === 'saving' ? '…' :
                       pinStatus[driver.id] === 'saved' ? '✓' :
                       pinStatus[driver.id] === 'error' ? '✗' :
                       driver.has_pin ? 'Update' : 'Set PIN'}
                    </button>
                  </div>
                </div>
              ))}
              {rosterDrivers.length === 0 && (
                <p className="text-center text-slate-500 text-sm py-8">No active drivers found in roster.</p>
              )}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

export default function AttendancePage() {
  return (
    <ProtectedRoute>
      <AttendanceContent />
    </ProtectedRoute>
  );
}
