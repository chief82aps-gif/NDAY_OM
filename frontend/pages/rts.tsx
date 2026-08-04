'use client';

import { useState, useEffect } from 'react';
import Head from 'next/head';

function resolveApi() {
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h === 'localhost' || h === '127.0.0.1') return 'http://127.0.0.1:8001';
  }
  return '';
}

type Step = 'loading' | 'identify' | 'form' | 'submitting' | 'done' | 'error';

interface PackageInfo {
  tracking_id: string;
  package_status: string | null;
  amazon_reason_code: string | null;
  suggested_code: string | null;
  needs_answer: boolean;
}

interface ReasonCode {
  code: string;
  label: string;
  amazon_code: string | null;
}

interface DebriefInfo {
  driver_name: string;
  route_id: string | null;
  shift_date: string;
  packages: PackageInfo[];
  reason_codes: ReasonCode[];
}

interface SubmitResult {
  reattempt_assigned_count: number;
  reattempt_skipped_count: number;
  expected_return_time: string | null;
}

interface IdentifyResult {
  routed_to_rescue: boolean;
  contribute_url?: string;
  rescued_driver_name?: string;
  debrief_url?: string;
}

interface Answer {
  reason_code: string;
  other_detail: string;
  within_drive_time: boolean | null;
  source: 'packages_file' | 'manual';
  amazon_reason_code: string | null;
  package_status: string | null;
}

function isAnswerComplete(a: Answer): boolean {
  if (!a.reason_code) return false;
  if (a.reason_code === 'other' && !a.other_detail.trim()) return false;
  if (a.reason_code === 'reattemptable' && a.within_drive_time === null) return false;
  return true;
}

export default function RtsPage() {
  const [step, setStep] = useState<Step>('loading');
  const [token, setToken] = useState('');
  const [info, setInfo] = useState<DebriefInfo | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [result, setResult] = useState<SubmitResult | null>(null);

  const [names, setNames] = useState<string[]>([]);
  const [driverName, setDriverName] = useState('');
  const [pin, setPin] = useState('');
  const [identifyErr, setIdentifyErr] = useState('');
  const [identifying, setIdentifying] = useState(false);

  const [answers, setAnswers] = useState<Record<string, Answer>>({});
  const [newTrackingId, setNewTrackingId] = useState('');
  const [submitErr, setSubmitErr] = useState('');

  function loadDebrief(t: string) {
    fetch(`${resolveApi()}/rts/debrief?token=${encodeURIComponent(t)}`)
      .then(async r => {
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error(d.detail ?? 'This link is invalid or has expired.');
        }
        return r.json();
      })
      .then((d: DebriefInfo) => {
        setInfo(d);
        const seeded: Record<string, Answer> = {};
        for (const p of d.packages) {
          seeded[p.tracking_id] = {
            reason_code: p.suggested_code ?? '',
            other_detail: '',
            within_drive_time: null,
            source: 'packages_file',
            amazon_reason_code: p.amazon_reason_code,
            package_status: p.package_status,
          };
        }
        setAnswers(seeded);
        setStep('form');
      })
      .catch(err => {
        setErrorMsg(err instanceof Error ? err.message : 'This link is invalid.');
        setStep('error');
      });
  }

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get('token');
    if (t) {
      setToken(t);
      loadDebrief(t);
      return;
    }
    // No token — driver opened this straight from the driver-dashboard hub card.
    fetch(`${resolveApi()}/attendance/roster-names`)
      .then(r => r.json())
      .then(d => setNames(d.names ?? []))
      .catch(() => {});
    setStep('identify');
  }, []);

  async function handleIdentify(e: React.FormEvent) {
    e.preventDefault();
    setIdentifyErr('');
    if (!/^\d{4}$/.test(pin)) { setIdentifyErr('PIN must be 4 digits.'); return; }
    setIdentifying(true);
    try {
      const res = await fetch(`${resolveApi()}/rts/identify`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ driver_name: driverName, ssn_last4: pin }),
      });
      if (!res.ok) {
        if (res.status === 401) { setIdentifyErr('Name or PIN is incorrect.'); return; }
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? 'Something went wrong. Try again.');
      }
      const d: IdentifyResult = await res.json();
      if (d.routed_to_rescue && d.contribute_url) {
        window.location.href = d.contribute_url;
        return;
      }
      if (d.debrief_url) {
        const t = new URL(d.debrief_url).searchParams.get('token') ?? '';
        setToken(t);
        setStep('loading');
        loadDebrief(t);
      }
    } catch (err: unknown) {
      setIdentifyErr(err instanceof Error ? err.message : 'Something went wrong. Try again.');
    } finally {
      setIdentifying(false);
    }
  }

  function updateAnswer(trackingId: string, patch: Partial<Answer>) {
    setAnswers(prev => ({ ...prev, [trackingId]: { ...prev[trackingId], ...patch } }));
  }

  function addManualPackage() {
    const id = newTrackingId.trim().toUpperCase();
    if (!id || answers[id]) return;
    setAnswers(prev => ({
      ...prev,
      [id]: {
        reason_code: '', other_detail: '', within_drive_time: null,
        source: 'manual', amazon_reason_code: null, package_status: null,
      },
    }));
    setNewTrackingId('');
  }

  function removeManualPackage(trackingId: string) {
    setAnswers(prev => {
      const next = { ...prev };
      delete next[trackingId];
      return next;
    });
  }

  const allComplete = Object.values(answers).every(isAnswerComplete);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!allComplete) return;
    setSubmitErr('');
    setStep('submitting');
    try {
      const res = await fetch(`${resolveApi()}/rts/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          packages: Object.entries(answers).map(([tracking_id, a]) => ({
            tracking_id,
            reason_code: a.reason_code,
            other_detail: a.other_detail || null,
            within_drive_time: a.within_drive_time,
            source: a.source,
            amazon_reason_code: a.amazon_reason_code,
          })),
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        setSubmitErr(d.detail ?? `Error ${res.status}`);
        setStep('form');
        return;
      }
      setResult(await res.json());
      setStep('done');
    } catch (err: unknown) {
      setErrorMsg(err instanceof Error ? err.message : 'Submission failed.');
      setStep('error');
    }
  }

  if (step === 'loading') {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 text-sm">Loading your debrief…</div>
      </div>
    );
  }

  if (step === 'identify') {
    return (
      <>
        <Head>
          <title>Return to Station — New Day Logistics</title>
          <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
          <meta name="theme-color" content="#0f172a" />
        </Head>
        <div className="min-h-screen bg-slate-900 px-4 py-8">
          <div className="w-full max-w-sm mx-auto space-y-5">
            <div className="text-center space-y-1">
              <p className="text-slate-400 text-xs uppercase tracking-widest">New Day Logistics</p>
              <h1 className="text-2xl font-bold text-white">Return to Station</h1>
              <p className="text-slate-400 text-sm">Enter your name and ADP kiosk PIN to continue.</p>
            </div>

            <form onSubmit={handleIdentify} className="space-y-4">
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
                  className="w-full bg-slate-800 border border-slate-600 rounded-xl px-4 py-4 text-white text-base tracking-widest"
                  required
                />
              </div>

              {identifyErr && <p className="text-red-400 text-sm text-center">{identifyErr}</p>}

              <button
                type="submit"
                disabled={identifying || !driverName}
                className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold py-5 rounded-2xl text-xl"
              >
                {identifying ? 'Checking…' : 'Continue →'}
              </button>
            </form>

            <p className="text-center text-slate-600 text-xs pb-4">Having trouble? Call dispatch directly.</p>
          </div>
        </div>
      </>
    );
  }

  if (step === 'error') {
    return (
      <>
        <Head><title>Return to Station — New Day Logistics</title></Head>
        <div className="min-h-screen bg-slate-900 flex items-center justify-center px-4">
          <div className="w-full max-w-sm text-center space-y-5">
            <div className="text-6xl">⚠️</div>
            <h1 className="text-xl font-bold text-white">Something went wrong</h1>
            <p className="text-slate-400 text-sm">{errorMsg}</p>
          </div>
        </div>
      </>
    );
  }

  if (step === 'done' && result) {
    const hasReattempts = result.reattempt_assigned_count > 0;
    return (
      <>
        <Head><title>RTS Debrief Complete — New Day Logistics</title></Head>
        <div className="min-h-screen bg-slate-900 px-4 py-10 flex items-start justify-center">
          <div className="w-full max-w-sm space-y-5">
            <div className="text-center space-y-2">
              <div className="text-6xl">{hasReattempts ? '🔄' : '✅'}</div>
              <h1 className="text-2xl font-bold text-white">
                {hasReattempts ? 'Make Your Reattempts' : 'Head Back to the Station'}
              </h1>
            </div>

            <div className="bg-slate-800 rounded-2xl p-5 space-y-3">
              {hasReattempts ? (
                <p className="text-slate-300 text-sm">
                  You have <span className="text-white font-bold">{result.reattempt_assigned_count}</span> package(s)
                  within a quick drive — attempt those first, then return to the station.
                </p>
              ) : (
                <p className="text-slate-300 text-sm">No reattempts within range — return to the station now.</p>
              )}
              {result.reattempt_skipped_count > 0 && (
                <p className="text-slate-500 text-xs">
                  {result.reattempt_skipped_count} package(s) were too far for a quick reattempt — dispatch will handle those.
                </p>
              )}
              {result.expected_return_time && (
                <div className="border-t border-slate-700 pt-3 flex items-center justify-between">
                  <span className="text-slate-400 text-sm">Expected arrival</span>
                  <span className="text-white font-bold text-lg">{result.expected_return_time}</span>
                </div>
              )}
            </div>

            <p className="text-center text-slate-600 text-xs pb-4">You may close this page.</p>
          </div>
        </div>
      </>
    );
  }

  const entries = Object.entries(answers);
  const reasonCodes = info?.reason_codes ?? [];

  return (
    <>
      <Head>
        <title>Return to Station — New Day Logistics</title>
        <meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1" />
        <meta name="theme-color" content="#0f172a" />
      </Head>
      <div className="min-h-screen bg-slate-900 px-4 py-8">
        <div className="w-full max-w-sm mx-auto space-y-5">
          <div className="text-center space-y-1">
            <p className="text-slate-400 text-xs uppercase tracking-widest">New Day Logistics</p>
            <h1 className="text-2xl font-bold text-white">Return to Station</h1>
            <p className="text-slate-400 text-sm">
              Hi {info?.driver_name?.split(',')[1]?.trim() ?? info?.driver_name} — what's coming back with you?
            </p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {entries.length === 0 && (
              <div className="bg-emerald-900/20 border border-emerald-700/40 rounded-2xl p-4 text-center">
                <p className="text-emerald-300 text-sm font-semibold">Nothing flagged for you — you're all clear!</p>
              </div>
            )}

            {entries.map(([trackingId, a]) => (
              <div key={trackingId} className={`rounded-2xl p-4 border ${a.amazon_reason_code === null ? 'bg-amber-900/20 border-amber-700/40' : 'bg-slate-800 border-slate-600'}`}>
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div>
                    <p className="text-white text-sm font-mono">{trackingId}</p>
                    {a.package_status && <p className="text-slate-400 text-xs">{a.package_status}</p>}
                  </div>
                  {a.source === 'manual' && (
                    <button type="button" onClick={() => removeManualPackage(trackingId)} className="text-slate-500 text-xs underline">
                      Remove
                    </button>
                  )}
                </div>

                {a.amazon_reason_code ? (
                  <p className="text-slate-400 text-xs mb-2">Amazon has this as: <span className="text-slate-300">{a.amazon_reason_code}</span> — confirm or change below.</p>
                ) : (
                  <p className="text-amber-300 text-xs mb-2 font-semibold">⚠️ No reason recorded yet — pick one before you can submit.</p>
                )}

                <select
                  value={a.reason_code}
                  onChange={e => updateAnswer(trackingId, { reason_code: e.target.value, within_drive_time: null })}
                  className="w-full bg-slate-900 border border-slate-600 rounded-xl px-3 py-3 text-white text-sm"
                  required
                >
                  <option value="">Select a reason…</option>
                  {reasonCodes.map(rc => (
                    <option key={rc.code} value={rc.code}>{rc.label}</option>
                  ))}
                </select>

                {a.reason_code === 'other' && (
                  <input
                    type="text"
                    value={a.other_detail}
                    onChange={e => updateAnswer(trackingId, { other_detail: e.target.value })}
                    placeholder="Briefly describe why"
                    className="w-full mt-2 bg-slate-900 border border-slate-600 rounded-xl px-3 py-3 text-white text-sm"
                  />
                )}

                {a.reason_code === 'reattemptable' && (
                  <div className="mt-2 flex gap-2">
                    <button
                      type="button"
                      onClick={() => updateAnswer(trackingId, { within_drive_time: true })}
                      className={`flex-1 py-2 rounded-xl text-sm font-semibold ${a.within_drive_time === true ? 'bg-blue-600 text-white' : 'bg-slate-900 text-slate-400 border border-slate-600'}`}
                    >
                      Quick drive (10-15 min)
                    </button>
                    <button
                      type="button"
                      onClick={() => updateAnswer(trackingId, { within_drive_time: false })}
                      className={`flex-1 py-2 rounded-xl text-sm font-semibold ${a.within_drive_time === false ? 'bg-blue-600 text-white' : 'bg-slate-900 text-slate-400 border border-slate-600'}`}
                    >
                      Too far
                    </button>
                  </div>
                )}
              </div>
            ))}

            <div className="bg-slate-800/60 border border-dashed border-slate-600 rounded-2xl p-4 space-y-2">
              <p className="text-slate-400 text-xs">Have a package that isn't listed above?</p>
              <div className="flex gap-2">
                <input
                  type="text"
                  value={newTrackingId}
                  onChange={e => setNewTrackingId(e.target.value)}
                  placeholder="Tracking ID (TBA…)"
                  className="flex-1 bg-slate-900 border border-slate-600 rounded-xl px-3 py-2 text-white text-sm"
                />
                <button type="button" onClick={addManualPackage} className="bg-slate-700 text-white px-4 rounded-xl text-sm font-semibold">
                  Add
                </button>
              </div>
            </div>

            {submitErr && <p className="text-red-400 text-sm text-center">{submitErr}</p>}

            <button
              type="submit"
              disabled={!allComplete || step === 'submitting'}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-bold py-5 rounded-2xl text-xl"
            >
              {step === 'submitting' ? 'Submitting…' : 'Submit Debrief →'}
            </button>
          </form>

          <p className="text-center text-slate-600 text-xs pb-4">Having trouble? Call dispatch directly.</p>
        </div>
      </div>
    </>
  );
}
