'use client';

import { useState, useEffect } from 'react';
import Head from 'next/head';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const REASONS: { value: string; label: string; emoji: string }[] = [
  { value: 'sick',           label: 'Sick',             emoji: '🤒' },
  { value: 'personal',       label: 'Personal',         emoji: '👤' },
  { value: 'family',         label: 'Family emergency', emoji: '🏠' },
  { value: 'weather',        label: 'Weather',          emoji: '🌧️' },
  { value: 'transportation', label: 'Transportation',   emoji: '🚗' },
  { value: 'other',          label: 'Other',            emoji: '📋' },
];

// HRM-023.1 point thresholds
const THRESHOLDS = [
  { pts: 5.0,  label: 'Written Warning',          color: 'text-amber-400' },
  { pts: 7.5,  label: 'Final Warning / Suspension', color: 'text-orange-400' },
  { pts: 10.0, label: 'Termination',              color: 'text-red-400' },
];

type Status = 'good' | 'written_warning' | 'final_warning' | 'termination';

interface AttendancePattern {
  type: string;
  severity: 'flag' | 'concern';
  message: string;
}

interface DriverStatus {
  driver_name: string;
  current_points: number;
  status: Status;
  next_threshold: { points: number; label: string; points_away: number };
  callout_points_added: number;
  projected_total: number;
  projected_status: Status;
  projected_next_threshold: { points: number; label: string; points_away: number };
  event_count: number;
  is_default_pin: boolean;
  patterns: AttendancePattern[];
}

interface SubmitResult {
  driver_name: string;
  compliant: boolean | null;
  not_scheduled: boolean;
  shift_date: string;
  hours_before_shift: number | null;
  points_added: number;
  new_total_points: number;
  new_status: Status;
  next_threshold: { points: number; label: string; points_away: number };
}

type Step = 'identify' | 'set-pin' | 'status' | 'details' | 'review' | 'submitting' | 'done' | 'error';

function fmtDate(iso: string): string {
  const [y, m, d] = iso.split('-');
  const dt = new Date(Number(y), Number(m) - 1, Number(d));
  return dt.toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' });
}

function statusColor(s: Status): string {
  if (s === 'termination')   return 'text-red-400';
  if (s === 'final_warning') return 'text-orange-400';
  if (s === 'written_warning') return 'text-amber-400';
  return 'text-green-400';
}

function statusLabel(s: Status): string {
  if (s === 'termination')   return 'Termination Risk';
  if (s === 'final_warning') return 'Final Warning Zone';
  if (s === 'written_warning') return 'Written Warning Zone';
  return 'Good Standing';
}

function barColor(pts: number): string {
  if (pts >= 7.5) return 'bg-red-500';
  if (pts >= 5.0) return 'bg-amber-500';
  return 'bg-green-500';
}

function PointsBar({ points }: { points: number }) {
  const pct = Math.min((points / 10) * 100, 100);
  return (
    <div className="w-full bg-slate-700 rounded-full h-3 relative">
      <div
        className={`${barColor(points)} h-3 rounded-full transition-all duration-500`}
        style={{ width: `${pct}%` }}
      />
      {/* threshold markers */}
      {[50, 75].map(p => (
        <div
          key={p}
          className="absolute top-0 bottom-0 w-px bg-slate-500"
          style={{ left: `${p}%` }}
        />
      ))}
    </div>
  );
}

export default function CalloutPage() {
  const [step, setStep] = useState<Step>('identify');

  // Step 1 — date + name + PIN
  const [scheduleDates, setScheduleDates] = useState<string[]>([]);
  const [shiftDate, setShiftDate]         = useState<string>('');
  const [names, setNames]                 = useState<string[]>([]);
  const [driverName, setDriverName]       = useState('');
  const [pin, setPin]                     = useState('');
  const [identifyErr, setIdentifyErr]     = useState('');
  const [identifying, setIdentifying]     = useState(false);

  // Step 2 — status data
  const [driverStatus, setDriverStatus] = useState<DriverStatus | null>(null);

  // Step 3
  const [reason, setReason]           = useState('');
  const [notes, setNotes]             = useState('');
  const [familyWhat, setFamilyWhat]   = useState('');
  const [familyWho, setFamilyWho]     = useState('');
  const [detailErr, setDetailErr]     = useState('');

  // Set-PIN step
  const [newPin, setNewPin]         = useState('');
  const [confirmPin, setConfirmPin] = useState('');
  const [pinErr, setPinErr]         = useState('');
  const [savingPin, setSavingPin]   = useState(false);

  // Family member pattern check (Step 3)
  const [familyPatternMsg, setFamilyPatternMsg] = useState('');

  // Step 4 — signature
  const [signatureName, setSignatureName] = useState('');
  const [signErr, setSignErr]             = useState('');

  // Done
  const [result, setResult] = useState<SubmitResult | null>(null);
  const [errorMsg, setErrorMsg] = useState('');

  // Load available shift dates; default to today if no schedule data yet
  useEffect(() => {
    const today = new Date().toISOString().slice(0, 10);
    fetch(`${resolveApi()}/attendance/schedule-dates`)
      .then(r => r.json())
      .then(d => {
        const dates: string[] = d.dates ?? [];
        setScheduleDates(dates);
        // Pre-select today if available, otherwise nearest future date
        const todayOrLater = dates.find(dt => dt >= today) ?? dates[0] ?? today;
        setShiftDate(todayOrLater);
      })
      .catch(() => {
        setShiftDate(today);
      });
  }, []);

  // Re-fetch driver names whenever the selected shift date changes
  useEffect(() => {
    if (!shiftDate) return;
    fetch(`${resolveApi()}/attendance/roster-names?date=${shiftDate}`)
      .then(r => r.json())
      .then(d => setNames(d.names ?? []))
      .catch(() => {});
  }, [shiftDate]);

  // When a family member is selected in Step 3, check for prior patterns
  useEffect(() => {
    if (!familyWho || !driverName || !pin) { setFamilyPatternMsg(''); return; }
    fetch(
      `${resolveApi()}/attendance/callout/family-pattern?driver_name=${encodeURIComponent(driverName)}&ssn_last4=${pin}&family_who=${encodeURIComponent(familyWho)}`
    )
      .then(r => r.ok ? r.json() : null)
      .then(d => setFamilyPatternMsg(d?.has_pattern && d.message ? d.message : ''))
      .catch(() => setFamilyPatternMsg(''));
  }, [familyWho, driverName, pin]);

  // Step 1 → Step 2: verify PIN and load status
  async function handleIdentify(e: React.FormEvent) {
    e.preventDefault();
    setIdentifyErr('');
    if (!driverName) { setIdentifyErr('Please select your name.'); return; }
    if (!/^\d{4}$/.test(pin)) { setIdentifyErr('PIN must be 4 digits.'); return; }
    setIdentifying(true);
    try {
      const res = await fetch(
        `${resolveApi()}/attendance/driver-status?driver_name=${encodeURIComponent(driverName)}&ssn_last4=${pin}`
      );
      if (res.status === 401) { setIdentifyErr('Name or PIN is incorrect.'); return; }
      if (!res.ok) throw new Error(`Server error (${res.status}). Try again or call dispatch directly.`);
      const data = await res.json();
      setDriverStatus(data);
      setStep(data.is_default_pin ? 'set-pin' : 'status');
    } catch (err: unknown) {
      setIdentifyErr(err instanceof Error ? err.message : 'Error verifying identity.');
    } finally {
      setIdentifying(false);
    }
  }

  // Set-PIN step handler
  async function handleSetPin(e: React.FormEvent) {
    e.preventDefault();
    setPinErr('');
    if (!/^\d{4}$/.test(newPin)) { setPinErr('PIN must be exactly 4 digits.'); return; }
    if (newPin === '1234') { setPinErr('Please choose a PIN other than the default (1234).'); return; }
    if (newPin !== confirmPin) { setPinErr('PINs do not match.'); return; }
    setSavingPin(true);
    try {
      const res = await fetch(`${resolveApi()}/attendance/callout/change-pin`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_name: driverName, current_pin: pin, new_pin: newPin }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setPinErr(d.detail ?? 'Could not save PIN. Please try again.');
        return;
      }
      // Update the pin in state so subsequent API calls use the new PIN
      setPin(newPin);
      setNewPin('');
      setConfirmPin('');
      setStep('status');
    } catch {
      setPinErr('Network error. Please try again.');
    } finally {
      setSavingPin(false);
    }
  }

  // Step 3 → submit
  function handleDetailsSubmit(e: React.FormEvent) {
    e.preventDefault();
    setDetailErr('');
    if (!reason) { setDetailErr('Please select a reason.'); return; }
    if (reason === 'family') {
      if (!familyWhat.trim()) { setDetailErr('Please describe the emergency.'); return; }
      if (!familyWho) { setDetailErr('Please select who the emergency pertains to.'); return; }
    }
    setStep('review');
  }

  async function handleSign(e: React.FormEvent) {
    e.preventDefault();
    setSignErr('');
    const expectedName = driverStatus?.driver_name ?? driverName;
    if (!signatureName.trim()) { setSignErr('Please type your full name to sign.'); return; }
    if (signatureName.trim().toLowerCase() !== expectedName.toLowerCase()) {
      setSignErr(`Name must match exactly: "${expectedName}"`);
      return;
    }
    setStep('submitting');

    const familyDetail = reason === 'family'
      ? `Emergency: ${familyWhat.trim()} | Pertains to: ${familyWho}`
      : '';
    const combinedNotes = [familyDetail, notes.trim()].filter(Boolean).join(' — ');

    try {
      const res = await fetch(`${resolveApi()}/attendance/callout`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          driver_name: driverName,
          ssn_last4: pin,
          reason_code: reason,
          shift_date: shiftDate || undefined,
          notes: combinedNotes || undefined,
          signature_name: signatureName.trim(),
        }),
      });
      if (res.status === 401) {
        setErrorMsg('Session expired. Please go back and re-enter your PIN.');
        setStep('error');
        return;
      }
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? `Error ${res.status}`);
      }
      setResult(await res.json());
      setStep('done');
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Submission failed.');
      setStep('error');
    }
  }

  // ── Step indicator ──────────────────────────────────────────────────────────
  function StepIndicator({ current }: { current: number }) {
    return (
      <div className="flex items-center justify-center gap-1 mb-6">
        {[1, 2, 3, 4].map(n => (
          <div key={n} className="flex items-center gap-1">
            <div className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${
              n < current ? 'bg-green-600 text-white' :
              n === current ? 'bg-blue-600 text-white' :
              'bg-slate-700 text-slate-400'
            }`}>{n < current ? '✓' : n}</div>
            {n < 4 && <div className={`w-5 h-px ${n < current ? 'bg-green-600' : 'bg-slate-700'}`} />}
          </div>
        ))}
      </div>
    );
  }

  // ── DONE ───────────────────────────────────────────────────────────────────
  if (step === 'done' && result) {
    return (
      <>
        <Head><title>Call-Out Received — New Day Logistics</title></Head>
        <div className="min-h-screen bg-slate-900 px-4 py-10 flex items-start justify-center">
          <div className="w-full max-w-sm space-y-5">
            <div className="text-center space-y-2">
              <div className="text-6xl">✅</div>
              <h1 className="text-2xl font-bold text-white">Call-Out Received</h1>
              <p className="text-slate-300 text-sm">Dispatch has been notified.</p>
            </div>

            {/* Updated points */}
            <div className="bg-slate-800 rounded-2xl p-5 space-y-3">
              <p className="text-slate-400 text-xs uppercase tracking-wider">Your Updated Standing</p>
              <div className="flex items-end justify-between">
                <span className="text-3xl font-bold text-white">{result.new_total_points.toFixed(1)}</span>
                <span className="text-slate-400 text-sm">/ 10 pts</span>
              </div>
              <PointsBar points={result.new_total_points} />
              <div className="flex items-center justify-between text-sm">
                <span className={`font-semibold ${statusColor(result.new_status)}`}>
                  {statusLabel(result.new_status)}
                </span>
                {result.next_threshold && result.new_status !== 'termination' && (
                  <span className="text-slate-400 text-xs">
                    {result.next_threshold.points_away} pts to {result.next_threshold.label}
                  </span>
                )}
              </div>
              <div className="text-xs text-slate-500 border-t border-slate-700 pt-3">
                This call-out added +{result.points_added} pts · Points reset after 60 days
              </div>
            </div>

            {/* Not scheduled notice */}
            {result.not_scheduled && (
              <div className="bg-blue-900/30 border border-blue-500/40 rounded-xl p-4 text-blue-300 text-sm">
                Note: You were not on the schedule for {fmtDate(result.shift_date)}. This callout has been recorded and flagged in your attendance record.
              </div>
            )}

            {/* Compliance */}
            {result.compliant === false && result.hours_before_shift !== null && (
              <div className="bg-amber-900/30 border border-amber-600/40 rounded-xl p-4 text-amber-300 text-sm">
                ⚠️ Call-in was {Math.abs(result.hours_before_shift).toFixed(1)}h before shift — policy requires 4 hours minimum. Contact your manager.
              </div>
            )}

            <p className="text-center text-slate-600 text-xs pb-4">You may close this page.</p>
          </div>
        </div>
      </>
    );
  }

  // ── ERROR ──────────────────────────────────────────────────────────────────
  if (step === 'error') {
    return (
      <>
        <Head><title>Call-Out — New Day Logistics</title></Head>
        <div className="min-h-screen bg-slate-900 flex items-center justify-center px-4">
          <div className="w-full max-w-sm text-center space-y-5">
            <div className="text-6xl">⚠️</div>
            <h1 className="text-xl font-bold text-white">Something went wrong</h1>
            <p className="text-slate-400 text-sm">{errorMsg}</p>
            <button
              onClick={() => { setStep('identify'); setErrorMsg(''); }}
              className="w-full bg-blue-600 hover:bg-blue-500 text-white font-semibold py-4 rounded-2xl text-lg"
            >
              Start Over
            </button>
          </div>
        </div>
      </>
    );
  }

  return (
    <>
      <Head>
        <title>Report Absence — New Day Logistics</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
        <meta name="theme-color" content="#0f172a" />
      </Head>

      <div className="min-h-screen bg-slate-900 px-4 py-8">
        <div className="w-full max-w-sm mx-auto space-y-5">

          {/* Header */}
          <div className="text-center space-y-1">
            <p className="text-slate-400 text-xs uppercase tracking-widest">New Day Logistics</p>
            <h1 className="text-2xl font-bold text-white">Report Absence</h1>
          </div>

          <StepIndicator current={
            step === 'identify' || step === 'set-pin' ? 1 :
            step === 'status'   ? 2 :
            step === 'details'  ? 3 : 4
          } />

          {/* ── STEP 1: Identify ────────────────────────────────────────────── */}
          {step === 'identify' && (
            <form onSubmit={handleIdentify} className="space-y-4">
              <p className="text-slate-400 text-sm text-center">Enter your name and ADP kiosk PIN</p>

              {/* Shift date picker */}
              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Date of Shift</label>
                {scheduleDates.length > 0 ? (
                  <div className="flex flex-wrap gap-2">
                    {scheduleDates.map(d => (
                      <button
                        key={d}
                        type="button"
                        onClick={() => { setShiftDate(d); setDriverName(''); }}
                        className={`px-3 py-2.5 rounded-xl text-sm font-medium transition-colors ${
                          shiftDate === d
                            ? 'bg-blue-600 text-white'
                            : 'bg-slate-800 border border-slate-600 text-slate-300'
                        }`}
                      >
                        {fmtDate(d)}
                      </button>
                    ))}
                  </div>
                ) : (
                  <input
                    type="date"
                    value={shiftDate}
                    onChange={e => { setShiftDate(e.target.value); setDriverName(''); }}
                    className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-4 text-white text-base"
                    required
                  />
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Your Name</label>
                {names.length > 0 ? (
                  <select
                    value={driverName}
                    onChange={e => setDriverName(e.target.value)}
                    className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-4 text-white text-base"
                    required
                  >
                    <option value="">Select your name…</option>
                    {names.map(n => <option key={n} value={n}>{n}</option>)}
                  </select>
                ) : (
                  <input
                    type="text"
                    value={driverName}
                    onChange={e => setDriverName(e.target.value)}
                    placeholder="Last, First"
                    autoComplete="off"
                    className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-4 text-white text-base"
                    required
                  />
                )}
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">PIN (last 4 SSN)</label>
                <input
                  type="password"
                  inputMode="numeric"
                  maxLength={4}
                  value={pin}
                  onChange={e => setPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="••••"
                  autoComplete="new-password"
                  className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-4 text-white text-base tracking-[0.5em] text-center"
                  required
                />
              </div>

              {identifyErr && (
                <div className="bg-red-900/40 border border-red-600/50 rounded-xl p-3 text-red-300 text-sm">
                  {identifyErr}
                </div>
              )}

              <button
                type="submit"
                disabled={identifying}
                className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold py-5 rounded-2xl text-xl"
              >
                {identifying ? 'Verifying…' : 'Continue →'}
              </button>
            </form>
          )}

          {/* ── SET PIN: First-time PIN setup ──────────────────────────────── */}
          {step === 'set-pin' && (
            <form onSubmit={handleSetPin} className="space-y-4">
              <div className="bg-amber-900/30 border border-amber-600/50 rounded-2xl p-4 space-y-1">
                <p className="text-amber-300 font-semibold text-sm">Set Your Personal PIN</p>
                <p className="text-amber-200/80 text-xs">
                  Your account is using the default PIN (1234). Please create a personal 4-digit PIN before continuing.
                  Choose something only you will know.
                </p>
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">New PIN</label>
                <input
                  type="password"
                  inputMode="numeric"
                  maxLength={4}
                  value={newPin}
                  onChange={e => setNewPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="Enter 4 digits"
                  className="w-full bg-slate-700 border border-slate-600 rounded-xl px-4 py-4 text-white text-2xl tracking-[1rem] text-center"
                  autoFocus
                />
              </div>

              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Confirm PIN</label>
                <input
                  type="password"
                  inputMode="numeric"
                  maxLength={4}
                  value={confirmPin}
                  onChange={e => setConfirmPin(e.target.value.replace(/\D/g, '').slice(0, 4))}
                  placeholder="Re-enter 4 digits"
                  className="w-full bg-slate-700 border border-slate-600 rounded-xl px-4 py-4 text-white text-2xl tracking-[1rem] text-center"
                />
              </div>

              {pinErr && (
                <div className="bg-red-900/40 border border-red-600/50 rounded-xl p-3 text-red-300 text-sm">
                  {pinErr}
                </div>
              )}

              <button
                type="submit"
                disabled={savingPin}
                className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 disabled:cursor-wait text-white font-bold py-5 rounded-2xl text-xl"
              >
                {savingPin ? 'Saving…' : 'Save My PIN →'}
              </button>
            </form>
          )}

          {/* ── STEP 2: Status + Policy ─────────────────────────────────────── */}
          {step === 'status' && driverStatus && (
            <div className="space-y-4">
              <p className="text-center text-slate-300 text-sm">
                Hi <span className="font-semibold text-white">{driverStatus.driver_name.split(',')[1]?.trim() ?? driverStatus.driver_name}</span> — here's your current standing.
              </p>

              {/* Not-scheduled warning */}
              {scheduleDates.length > 0 && shiftDate && !names.includes(driverStatus.driver_name) && (
                <div className="bg-blue-900/40 border border-blue-500/50 rounded-2xl p-4 space-y-1">
                  <p className="text-blue-300 font-semibold text-sm">Not Scheduled This Day</p>
                  <p className="text-blue-200/80 text-xs">
                    You are not on the schedule for <span className="font-semibold">{fmtDate(shiftDate)}</span>.
                    Your callout will still be recorded and included in attendance reports.
                    If you selected the wrong date, go back and choose the correct shift.
                  </p>
                </div>
              )}

              {/* ── Pattern warnings ──────────────────────────────────────── */}
              {driverStatus.patterns?.length > 0 && (
                <div className="space-y-3">
                  {driverStatus.patterns.map((p, i) => (
                    <div
                      key={i}
                      className={`rounded-2xl p-4 space-y-1 border ${
                        p.severity === 'flag'
                          ? 'bg-amber-950/60 border-amber-600/50'
                          : 'bg-slate-700/60 border-slate-500/50'
                      }`}
                    >
                      <p className={`text-xs font-semibold uppercase tracking-wide ${
                        p.severity === 'flag' ? 'text-amber-400' : 'text-slate-400'
                      }`}>
                        {p.severity === 'flag' ? '⚑ Pattern Noticed' : '○ Just Checking In'}
                      </p>
                      <p className={`text-sm leading-relaxed ${
                        p.severity === 'flag' ? 'text-amber-100' : 'text-slate-300'
                      }`}>
                        {p.message}
                      </p>
                    </div>
                  ))}
                </div>
              )}

              {/* Current points card */}
              <div className="bg-slate-800 rounded-2xl p-5 space-y-3">
                <p className="text-slate-400 text-xs uppercase tracking-wider">Current Standing (last 60 days)</p>
                <div className="flex items-end justify-between">
                  <span className="text-3xl font-bold text-white">{driverStatus.current_points.toFixed(1)}</span>
                  <span className="text-slate-400 text-sm">/ 10 pts</span>
                </div>
                <PointsBar points={driverStatus.current_points} />
                <div className="flex justify-between text-xs text-slate-500">
                  <span>0</span><span>5 — Warning</span><span>7.5 — Final</span><span>10 — Term.</span>
                </div>
                <div className={`font-semibold text-sm ${statusColor(driverStatus.status)}`}>
                  {statusLabel(driverStatus.status)}
                </div>
              </div>

              {/* Impact of this callout */}
              <div className="bg-slate-700/60 border border-slate-600 rounded-2xl p-4 space-y-3">
                <p className="text-slate-300 text-sm font-semibold">This call-out will add:</p>
                <div className="flex items-center justify-between">
                  <span className="text-slate-400 text-sm">Absence with notification</span>
                  <span className="text-white font-bold">+{driverStatus.callout_points_added} pts</span>
                </div>
                <div className="border-t border-slate-600 pt-3 flex items-center justify-between">
                  <span className="text-slate-400 text-sm">New total</span>
                  <span className={`font-bold text-lg ${statusColor(driverStatus.projected_status)}`}>
                    {driverStatus.projected_total.toFixed(1)} pts
                  </span>
                </div>
                {driverStatus.projected_total >= 5.0 && driverStatus.current_points < 5.0 && (
                  <div className="bg-amber-900/40 border border-amber-600/40 rounded-xl p-3 text-amber-300 text-xs">
                    ⚠️ This call-out will put you into <strong>Written Warning</strong> territory. A written warning may be issued.
                  </div>
                )}
                {driverStatus.projected_total >= 7.5 && driverStatus.current_points < 7.5 && (
                  <div className="bg-orange-900/40 border border-orange-600/40 rounded-xl p-3 text-orange-300 text-xs">
                    ⚠️ This call-out will put you into <strong>Final Warning / Suspension</strong> territory.
                  </div>
                )}
                {driverStatus.projected_total >= 10.0 && (
                  <div className="bg-red-900/40 border border-red-600/40 rounded-xl p-3 text-red-300 text-xs">
                    🚨 This call-out may result in <strong>termination</strong> per NDL policy. Contact your manager immediately.
                  </div>
                )}
                {driverStatus.projected_status === 'good' && (
                  <p className="text-xs text-slate-500">
                    You will have {driverStatus.projected_next_threshold.points_away} pts remaining before a Written Warning.
                  </p>
                )}
              </div>

              {/* Policy summary */}
              <div className="bg-slate-800/50 border border-slate-700 rounded-2xl p-4 space-y-3">
                <p className="text-slate-300 text-sm font-semibold">NDL Attendance Policy (HRM-023.1)</p>
                <ul className="space-y-1.5 text-xs text-slate-400">
                  <li className="flex justify-between"><span>Rescued / Early departure / Uniform</span><span className="text-slate-300">0.5 pts</span></li>
                  <li className="flex justify-between"><span>Late / Tardy</span><span className="text-slate-300">1 pt</span></li>
                  <li className="flex justify-between"><span>Absence with notification (4 hr minimum)</span><span className="text-slate-300">2 pts</span></li>
                  <li className="flex justify-between"><span>No-Call / No-Show</span><span className="text-slate-300">5 pts</span></li>
                </ul>
                <div className="border-t border-slate-700 pt-2 space-y-1 text-xs">
                  {THRESHOLDS.map(t => (
                    <div key={t.pts} className="flex justify-between">
                      <span className="text-slate-400">{t.pts} pts</span>
                      <span className={t.color}>{t.label}</span>
                    </div>
                  ))}
                </div>
                <p className="text-xs text-slate-500">
                  Points roll over a 90-day window. Multiple violations in the same pay period multiply.
                  You must call dispatch — texting is not accepted.
                </p>
              </div>

              <button
                onClick={() => setStep('details')}
                className="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-5 rounded-2xl text-lg"
              >
                I Understand — Continue →
              </button>
              <button
                onClick={() => { setStep('identify'); setDriverStatus(null); }}
                className="w-full text-slate-500 hover:text-slate-300 text-sm py-2"
              >
                ← Back
              </button>
            </div>
          )}

          {/* ── STEP 3: Details ─────────────────────────────────────────────── */}
          {step === 'details' && (
            <form onSubmit={handleDetailsSubmit} className="space-y-4">
              <p className="text-center text-slate-400 text-sm">Select your reason for calling out</p>

              {/* Reason */}
              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Reason <span className="text-red-400">*</span></label>
                <div className="grid grid-cols-2 gap-2">
                  {REASONS.map(r => (
                    <button
                      key={r.value}
                      type="button"
                      onClick={() => {
                        setReason(r.value);
                        if (r.value !== 'family') { setFamilyWhat(''); setFamilyWho(''); }
                      }}
                      className={`flex items-center gap-2 px-3 py-3.5 rounded-xl text-sm font-medium transition-colors ${
                        reason === r.value
                          ? 'bg-blue-600 text-white'
                          : 'bg-slate-800 border border-slate-600 text-slate-300'
                      }`}
                    >
                      <span>{r.emoji}</span>
                      <span>{r.label}</span>
                    </button>
                  ))}
                </div>
              </div>

              {/* Family emergency follow-up */}
              {reason === 'family' && (
                <div className="bg-amber-900/20 border border-amber-700/40 rounded-2xl p-4 space-y-4">
                  <p className="text-amber-300 text-sm font-semibold">Family Emergency — Additional Information Required</p>

                  <div>
                    <label className="block text-slate-300 text-sm font-medium mb-1.5">
                      What is the emergency? <span className="text-red-400">*</span>
                    </label>
                    <input
                      type="text"
                      value={familyWhat}
                      onChange={e => setFamilyWhat(e.target.value)}
                      placeholder="Brief description…"
                      className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-3 text-white text-base"
                    />
                  </div>

                  <div>
                    <label className="block text-slate-300 text-sm font-medium mb-1.5">
                      Who does this pertain to? <span className="text-red-400">*</span>
                    </label>
                    <div className="grid grid-cols-2 gap-2">
                      {['Spouse', 'Child', 'Mother', 'Father'].map(person => (
                        <button
                          key={person}
                          type="button"
                          onClick={() => setFamilyWho(person)}
                          className={`py-3 rounded-xl text-sm font-medium transition-colors ${
                            familyWho === person
                              ? 'bg-amber-600 text-white'
                              : 'bg-slate-800 border border-slate-600 text-slate-300'
                          }`}
                        >
                          {person}
                        </button>
                      ))}
                    </div>
                    <p className="text-slate-500 text-xs mt-2">
                      Family emergency is only accepted for spouse, child, mother, or father.
                    </p>

                    {/* Family member pattern push-back */}
                    {familyPatternMsg && (
                      <div className="mt-3 bg-amber-950/60 border border-amber-600/50 rounded-xl p-3">
                        <p className="text-xs font-semibold text-amber-400 uppercase tracking-wide mb-1">⚑ Pattern Noticed</p>
                        <p className="text-sm text-amber-100 leading-relaxed">{familyPatternMsg}</p>
                      </div>
                    )}
                  </div>
                </div>
              )}

              {/* Notes */}
              <div>
                <label className="block text-slate-300 text-sm font-medium mb-1.5">Additional Notes <span className="text-slate-500">(optional)</span></label>
                <textarea
                  value={notes}
                  onChange={e => setNotes(e.target.value)}
                  placeholder="Any additional details for your manager…"
                  rows={3}
                  className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-3 text-white text-base resize-none"
                />
              </div>

              {detailErr && (
                <div className="bg-red-900/40 border border-red-600/50 rounded-xl p-3 text-red-300 text-sm">
                  {detailErr}
                </div>
              )}

              <button
                type="submit"
                className="w-full bg-blue-600 hover:bg-blue-500 text-white font-bold py-5 rounded-2xl text-xl"
              >
                Review & Sign →
              </button>
              <button
                type="button"
                onClick={() => setStep('status')}
                className="w-full text-slate-500 hover:text-slate-300 text-sm py-2"
              >
                ← Back
              </button>
            </form>
          )}

          {/* ── STEP 4: Review & Sign ────────────────────────────────────────── */}
          {(step === 'review' || step === 'submitting') && driverStatus && (() => {
            const today = new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' });
            const reasonLabel = REASONS.find(r => r.value === reason)?.label ?? reason;
            const familyDetail = reason === 'family' ? `${familyWhat} — pertains to: ${familyWho}` : null;
            const pointsBefore = driverStatus.current_points;
            const pointsAfter = driverStatus.projected_total;

            return (
              <form onSubmit={handleSign} className="space-y-4">
                {/* NDL Document Header */}
                <div className="bg-white rounded-2xl p-5 text-slate-900 space-y-4 text-sm">
                  <div className="text-center border-b border-slate-200 pb-3">
                    <p className="font-bold text-base">NEW DAY LOGISTICS LLC</p>
                    <p className="font-semibold text-slate-600">Absence Notification</p>
                    <p className="text-xs text-slate-400 mt-1">HRM-023.1 — Driver Self-Report</p>
                  </div>

                  <div className="grid grid-cols-2 gap-y-1 text-xs">
                    <span className="text-slate-500">Employee:</span>
                    <span className="font-semibold">{driverStatus.driver_name}</span>
                    <span className="text-slate-500">Date:</span>
                    <span>{today}</span>
                    <span className="text-slate-500">Supervisor:</span>
                    <span>On-Duty Dispatch</span>
                  </div>

                  <div className="border-t border-slate-200 pt-3 space-y-3">
                    <div>
                      <p className="font-semibold text-xs text-slate-500 uppercase tracking-wide mb-1">1. Statement of Absence</p>
                      <p className="text-xs text-slate-700">
                        I, <span className="font-semibold">{driverStatus.driver_name}</span>, am notifying New Day Logistics LLC that I am unable to report to work on <span className="font-semibold">{shiftDate ? fmtDate(shiftDate) : today}</span>.
                      </p>
                      <p className="text-xs text-slate-700 mt-1">
                        <span className="font-semibold">Reason:</span> {reasonLabel}
                        {familyDetail && <span> — {familyDetail}</span>}
                      </p>
                      {notes.trim() && (
                        <p className="text-xs text-slate-600 italic mt-1">"{notes.trim()}"</p>
                      )}
                    </div>

                    <div>
                      <p className="font-semibold text-xs text-slate-500 uppercase tracking-wide mb-1">2. Attendance Point Impact (HRM-023.1)</p>
                      <div className="bg-slate-50 rounded-lg p-2 space-y-1 text-xs">
                        <div className="flex justify-between"><span className="text-slate-500">Points before this absence:</span><span>{pointsBefore.toFixed(1)}</span></div>
                        <div className="flex justify-between"><span className="text-slate-500">Points assessed (absence w/ notice):</span><span>+2.0</span></div>
                        <div className="flex justify-between font-semibold border-t border-slate-200 pt-1 mt-1"><span>New total:</span><span className={pointsAfter >= 7.5 ? 'text-red-600' : pointsAfter >= 5.0 ? 'text-amber-600' : 'text-slate-900'}>{pointsAfter.toFixed(1)} / 10</span></div>
                      </div>
                    </div>

                    <div>
                      <p className="font-semibold text-xs text-slate-500 uppercase tracking-wide mb-1">3. Employee Acknowledgment</p>
                      <ul className="space-y-1 text-xs text-slate-700">
                        <li>✓ I have notified NDL management of my inability to report to work.</li>
                        <li>✓ I understand this absence results in <strong>+2.0 attendance points</strong> per HRM-023.1.</li>
                        <li>✓ I understand accumulation of points may result in corrective action up to and including termination.</li>
                        <li>✓ I understand a No-Call/No-Show carries 5 points and may result in immediate termination.</li>
                        <li>✓ I understand I must provide at least <strong>4 hours advance notice</strong>. Text is not accepted.</li>
                      </ul>
                    </div>

                    <div className="border-t border-slate-200 pt-3">
                      <p className="font-semibold text-xs text-slate-500 uppercase tracking-wide mb-1">Consequences of failure to improve</p>
                      <p className="text-xs text-slate-600">Failure to comply with attendance requirements could lead to additional disciplinary action up to and including termination of employment.</p>
                    </div>
                  </div>
                </div>

                {/* Signature field */}
                <div className="bg-slate-800 border border-slate-600 rounded-2xl p-4 space-y-3">
                  <p className="text-white text-sm font-semibold">Electronic Signature</p>
                  <p className="text-slate-400 text-xs">
                    By typing your full name below, you confirm the information above is accurate and that you have read and understand all NDL policies referenced.
                  </p>
                  <input
                    type="text"
                    value={signatureName}
                    onChange={e => setSignatureName(e.target.value)}
                    placeholder={`Type your name: ${driverStatus.driver_name}`}
                    className="w-full bg-slate-700 border border-slate-500 rounded-xl px-4 py-3 text-white text-base italic"
                    autoComplete="name"
                  />
                  <p className="text-slate-500 text-xs">Name must match exactly as it appears above.</p>
                </div>

                {signErr && (
                  <div className="bg-red-900/40 border border-red-600/50 rounded-xl p-3 text-red-300 text-sm">
                    {signErr}
                  </div>
                )}

                <button
                  type="submit"
                  disabled={step === 'submitting'}
                  className="w-full bg-green-700 hover:bg-green-600 disabled:opacity-50 disabled:cursor-wait text-white font-bold py-5 rounded-2xl text-xl"
                >
                  {step === 'submitting' ? 'Submitting…' : 'I Acknowledge & Submit →'}
                </button>
                <button
                  type="button"
                  onClick={() => setStep('details')}
                  disabled={step === 'submitting'}
                  className="w-full text-slate-500 hover:text-slate-300 text-sm py-2 disabled:opacity-30"
                >
                  ← Back
                </button>
              </form>
            );
          })()}

          <p className="text-center text-slate-600 text-xs pb-4">
            Having trouble? Call dispatch directly.
          </p>
        </div>
      </div>
    </>
  );
}
