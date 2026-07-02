'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';
import Head from 'next/head';

function resolveApi() {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined') {
    const h = window.location.hostname;
    if (h !== 'localhost' && h !== '127.0.0.1') return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8000';
}

interface ConfirmData {
  already_confirmed: boolean;
  driver_name: string;
  first_name: string;
  route_code: string;
  stage_location: string;
  van_number: string;
  wave: string;
  packages: number | null;
  date: string;
  acknowledged_at: string | null;
}

export default function ConfirmPage() {
  const router = useRouter();
  const { token } = router.query;

  const [data, setData] = useState<ConfirmData | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (!token || typeof token !== 'string') return;

    fetch(`${resolveApi()}/daily-notify/confirm?token=${encodeURIComponent(token)}`)
      .then(async (res) => {
        if (res.ok) {
          setData(await res.json());
        } else {
          const err = await res.json().catch(() => ({}));
          setError(err.detail ?? 'Invalid or expired link.');
        }
      })
      .catch(() => setError('Network error — please try again.'))
      .finally(() => setLoading(false));
  }, [token]);

  const formatDate = (d: string) =>
    new Date(d + 'T12:00:00').toLocaleDateString('en-US', {
      weekday: 'long',
      month: 'long',
      day: 'numeric',
    });

  return (
    <>
      <Head>
        <title>Attendance Confirmation — New Day Logistics</title>
        <meta name="viewport" content="width=device-width, initial-scale=1" />
      </Head>

      <div className="min-h-screen bg-slate-900 flex items-center justify-center px-4 py-12">
        <div className="w-full max-w-sm">

          {loading && (
            <div className="text-center text-slate-400 text-sm animate-pulse">
              Confirming attendance…
            </div>
          )}

          {!loading && error && (
            <div className="bg-red-900/30 border border-red-700 rounded-2xl p-6 text-center">
              <div className="text-4xl mb-4">❌</div>
              <p className="text-red-300 font-semibold">{error}</p>
              <p className="text-red-400 text-sm mt-2">
                Contact dispatch if you need help.
              </p>
            </div>
          )}

          {!loading && data && (
            <div className="bg-slate-800 border border-slate-700 rounded-2xl overflow-hidden shadow-xl">
              {/* Header */}
              <div className="bg-green-600 px-6 py-5 text-center">
                <div className="text-5xl mb-2">✅</div>
                <h1 className="text-white font-bold text-xl">
                  {data.already_confirmed
                    ? 'Already Confirmed'
                    : `You're confirmed, ${data.first_name}!`}
                </h1>
                {data.date && (
                  <p className="text-green-100 text-sm mt-1">{formatDate(data.date)}</p>
                )}
              </div>

              {/* Route details */}
              <div className="px-6 py-5 space-y-3">
                {[
                  { icon: '📍', label: 'Route', value: data.route_code },
                  { icon: '🏢', label: 'Stage', value: data.stage_location },
                  { icon: '🚐', label: 'Van', value: data.van_number },
                  { icon: '⏰', label: 'Wave', value: data.wave },
                  {
                    icon: '📦',
                    label: 'Planned Packages',
                    value: data.packages ? String(data.packages) : null,
                  },
                ]
                  .filter((row) => row.value)
                  .map((row) => (
                    <div key={row.label} className="flex items-center gap-3">
                      <span className="text-xl w-7 flex-shrink-0">{row.icon}</span>
                      <div>
                        <p className="text-xs text-slate-400 uppercase tracking-wide">
                          {row.label}
                        </p>
                        <p className="text-white font-semibold text-sm">{row.value}</p>
                      </div>
                    </div>
                  ))}
              </div>

              {/* Footer note */}
              <div className="border-t border-slate-700 px-6 py-4 text-center">
                <p className="text-slate-400 text-xs">
                  For wave lead info and updates, connect on <strong className="text-slate-300">Zello</strong>.
                </p>
                <p className="text-slate-500 text-xs mt-1">New Day Logistics LLC · DLV3</p>
              </div>
            </div>
          )}

        </div>
      </div>
    </>
  );
}
