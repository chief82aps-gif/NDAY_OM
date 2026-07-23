import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface Item {
  type: 'dvic' | 'attendance';
  id: number;
  label: string;
  transporter_id?: string;
  week?: string;
  stage?: number;
  event_type?: string;
  event_date?: string;
}

type Step = 'loading' | 'list' | 'error';

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 560, boxShadow: '0 4px 24px rgba(0,0,0,0.4)' } as React.CSSProperties,
  item: { background: '#0f172a', border: '1px solid #334155', borderRadius: 10, padding: 16, marginBottom: 14 } as React.CSSProperties,
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6, fontWeight: 600 } as React.CSSProperties,
  input: { width: '100%', boxSizing: 'border-box' as const, background: '#1e293b', border: '1px solid #334155', borderRadius: 8, padding: '10px 14px', color: '#f1f5f9', fontSize: 15, marginBottom: 10 },
  btn: (disabled: boolean) => ({
    width: '100%', background: disabled ? '#1e293b' : '#16a34a',
    color: '#fff', border: 'none', borderRadius: 8, padding: '12px 0',
    fontSize: 15, fontWeight: 700, cursor: disabled ? 'default' : 'pointer',
  } as React.CSSProperties),
};

export default function OutstandingItemsPage() {
  const router = useRouter();
  const { token } = router.query as { token?: string };
  const api = resolveApi();

  const [step, setStep] = useState<Step>('loading');
  const [driverName, setDriverName] = useState('');
  const [items, setItems] = useState<Item[]>([]);
  const [errorMsg, setErrorMsg] = useState('');
  const [signatures, setSignatures] = useState<Record<number, string>>({});
  const [busy, setBusy] = useState<number | null>(null);
  const [itemErrors, setItemErrors] = useState<Record<number, string>>({});

  const load = () => {
    if (!token) return;
    fetch(`${api}/rostering/outstanding-items-by-token?token=${encodeURIComponent(token)}`)
      .then(r => {
        if (!r.ok) throw new Error(r.status === 401 ? 'expired' : 'invalid');
        return r.json();
      })
      .then((data: { driver_name: string; items: Item[] }) => {
        setDriverName(data.driver_name);
        setItems(data.items);
        setStep('list');
      })
      .catch(err => {
        setErrorMsg(
          err.message === 'expired'
            ? 'This link has expired. Ask dispatch to send a new one.'
            : 'This link is invalid.'
        );
        setStep('error');
      });
  };

  useEffect(load, [token, api]);

  async function acknowledgeDvic(item: Item) {
    if (!item.week) return;
    setBusy(item.id);
    setItemErrors(prev => ({ ...prev, [item.id]: '' }));
    try {
      const res = await fetch(`${api}/dvic/acknowledge`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          transporter_id: item.transporter_id,
          week: item.week,
          signature_name: driverName,
        }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? 'Could not acknowledge.');
      }
      load();
    } catch (err: unknown) {
      setItemErrors(prev => ({ ...prev, [item.id]: err instanceof Error ? err.message : 'Failed.' }));
    } finally {
      setBusy(null);
    }
  }

  async function signAttendance(item: Item) {
    const name = (signatures[item.id] || '').trim();
    const normalize = (v: string) => v.trim().toLowerCase().replace(/\s+/g, ' ');
    if (!name) {
      setItemErrors(prev => ({ ...prev, [item.id]: 'Please type your full name to sign.' }));
      return;
    }
    if (normalize(name) !== normalize(driverName)) {
      setItemErrors(prev => ({ ...prev, [item.id]: `Name must match exactly: "${driverName}"` }));
      return;
    }
    setBusy(item.id);
    setItemErrors(prev => ({ ...prev, [item.id]: '' }));
    try {
      const res = await fetch(`${api}/attendance/events/${item.id}/driver-sign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ signature_name: name }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => ({}));
        throw new Error(d.detail ?? 'Could not sign.');
      }
      load();
    } catch (err: unknown) {
      setItemErrors(prev => ({ ...prev, [item.id]: err instanceof Error ? err.message : 'Failed.' }));
    } finally {
      setBusy(null);
    }
  }

  if (step === 'loading') return (
    <div style={s.page}><div style={{ color: '#94a3b8' }}>Verifying your link…</div></div>
  );

  if (step === 'error') return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ color: '#f87171', margin: '0 0 8px' }}>Something went wrong</h2>
        <p style={{ color: '#94a3b8' }}>{errorMsg}</p>
      </div>
    </div>
  );

  if (items.length === 0) return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>You're all set, {driverName?.split(' ')[0]}</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          Everything's acknowledged. Your route details will follow shortly.
        </p>
      </div>
    </div>
  );

  return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ margin: '0 0 4px', color: '#f1f5f9' }}>A Few Things First</h2>
        <p style={{ margin: '0 0 20px', color: '#64748b', fontSize: 13 }}>
          Hi {driverName?.split(' ')[0]} — please clear these before your route details are sent.
        </p>

        {items.map(item => (
          <div key={`${item.type}-${item.id}`} style={s.item}>
            <p style={{ margin: '0 0 8px', color: '#f1f5f9', fontWeight: 700, fontSize: 14 }}>{item.label}</p>

            {item.type === 'dvic' && (
              <>
                {itemErrors[item.id] && <p style={{ color: '#f87171', fontSize: 13 }}>{itemErrors[item.id]}</p>}
                <button
                  onClick={() => acknowledgeDvic(item)}
                  disabled={busy === item.id}
                  style={s.btn(busy === item.id)}
                >
                  {busy === item.id ? 'Submitting…' : 'Acknowledge'}
                </button>
              </>
            )}

            {item.type === 'attendance' && (
              <>
                <label style={s.label}>Type your full name to sign</label>
                <input
                  style={s.input}
                  value={signatures[item.id] || ''}
                  onChange={e => setSignatures(prev => ({ ...prev, [item.id]: e.target.value }))}
                  placeholder={driverName}
                />
                {itemErrors[item.id] && <p style={{ color: '#f87171', fontSize: 13 }}>{itemErrors[item.id]}</p>}
                <button
                  onClick={() => signAttendance(item)}
                  disabled={busy === item.id}
                  style={s.btn(busy === item.id)}
                >
                  {busy === item.id ? 'Submitting…' : 'Sign'}
                </button>
              </>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
