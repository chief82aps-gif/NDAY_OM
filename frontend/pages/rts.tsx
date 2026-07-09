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

type Step = 'loading' | 'form' | 'submitting' | 'done' | 'error';

interface DebriefInfo {
  driver_name: string;
  route_id: string | null;
  shift_date: string;
}

interface SubmitResult {
  reattempt_assigned_count: number;
  reattempt_skipped_count: number;
  expected_return_time: string | null;
}

function CountStepper({
  label, hint, value, onChange,
}: { label: string; hint?: string; value: number; onChange: (n: number) => void }) {
  return (
    <div className="bg-slate-800 border border-slate-600 rounded-2xl p-4">
      <div className="flex items-center justify-between">
        <div>
          <p className="text-white text-sm font-semibold">{label}</p>
          {hint && <p className="text-slate-400 text-xs mt-0.5">{hint}</p>}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={() => onChange(Math.max(0, value - 1))}
            className="w-9 h-9 rounded-xl bg-slate-700 text-white text-lg font-bold"
          >
            −
          </button>
          <span className="text-white text-xl font-bold w-6 text-center">{value}</span>
          <button
            type="button"
            onClick={() => onChange(value + 1)}
            className="w-9 h-9 rounded-xl bg-slate-700 text-white text-lg font-bold"
          >
            +
          </button>
        </div>
      </div>
    </div>
  );
}

export default function RtsPage() {
  const [step, setStep] = useState<Step>('loading');
  const [token, setToken] = useState('');
  const [info, setInfo] = useState<DebriefInfo | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [result, setResult] = useState<SubmitResult | null>(null);

  const [damaged, setDamaged] = useState(0);
  const [reverse, setReverse] = useState(0);
  const [excluded, setExcluded] = useState(0);
  const [reattemptEligible, setReattemptEligible] = useState(0);
  const [reattemptWithinDrive, setReattemptWithinDrive] = useState(0);

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const t = params.get('token');
    if (!t) {
      setErrorMsg('This page requires a personal link. Use the Return to Station button in Slack.');
      setStep('error');
      return;
    }
    setToken(t);
    fetch(`${resolveApi()}/rts/debrief?token=${encodeURIComponent(t)}`)
      .then(async r => {
        if (!r.ok) {
          const d = await r.json().catch(() => ({}));
          throw new Error(d.detail ?? 'This link is invalid or has expired.');
        }
        return r.json();
      })
      .then(d => { setInfo(d); setStep('form'); })
      .catch(err => {
        setErrorMsg(err instanceof Error ? err.message : 'This link is invalid.');
        setStep('error');
      });
  }, []);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStep('submitting');
    try {
      const res = await fetch(`${resolveApi()}/rts/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          token,
          damaged_count: damaged,
          reverse_count: reverse,
          excluded_count: excluded,
          reattempt_eligible_count: reattemptEligible,
          reattempt_within_drive_time: reattemptWithinDrive,
        }),
      });
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

  if (step === 'loading') {
    return (
      <div className="min-h-screen bg-slate-900 flex items-center justify-center">
        <div className="text-slate-400 text-sm">Loading your debrief…</div>
      </div>
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
            <CountStepper label="Damaged" hint="Marked as damaged / undeliverable" value={damaged} onChange={setDamaged} />
            <CountStepper label="Reverse" hint="Customer returns / SWA pickups" value={reverse} onChange={setReverse} />
            <CountStepper
              label="Excluded"
              hint="Business closed, refused, or rescheduled — not reattemptable"
              value={excluded}
              onChange={setExcluded}
            />
            <CountStepper
              label="Re-Attemptable"
              hint="Could still be delivered today"
              value={reattemptEligible}
              onChange={n => {
                setReattemptEligible(n);
                if (reattemptWithinDrive > n) setReattemptWithinDrive(n);
              }}
            />

            {reattemptEligible > 0 && (
              <div className="bg-amber-900/20 border border-amber-700/40 rounded-2xl p-4 space-y-3">
                <p className="text-amber-300 text-sm font-semibold">
                  Of those {reattemptEligible}, how many are a 10–15 min drive or less?
                </p>
                <p className="text-amber-200/70 text-xs">Don't include anything that would add a longer drive to your day.</p>
                <div className="flex items-center justify-center gap-4">
                  <button
                    type="button"
                    onClick={() => setReattemptWithinDrive(Math.max(0, reattemptWithinDrive - 1))}
                    className="w-10 h-10 rounded-xl bg-slate-700 text-white text-xl font-bold"
                  >
                    −
                  </button>
                  <span className="text-white text-2xl font-bold w-8 text-center">{reattemptWithinDrive}</span>
                  <button
                    type="button"
                    onClick={() => setReattemptWithinDrive(Math.min(reattemptEligible, reattemptWithinDrive + 1))}
                    className="w-10 h-10 rounded-xl bg-slate-700 text-white text-xl font-bold"
                  >
                    +
                  </button>
                </div>
              </div>
            )}

            <button
              type="submit"
              disabled={step === 'submitting'}
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
