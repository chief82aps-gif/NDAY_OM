import { useState, useCallback, useEffect } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface CatalogItem {
  id: number;
  name: string;
  description: string | null;
  point_cost: number;
  active: boolean;
}

interface RedemptionRequest {
  id: number;
  driver: string;
  item: string;
  is_cash_out: boolean;
  point_cost: number;
  requested_at: string | null;
}

export default function SwagStoreAdminPage() {
  const api = resolveApi();
  const [catalog, setCatalog] = useState<CatalogItem[]>([]);
  const [requests, setRequests] = useState<RedemptionRequest[]>([]);
  const [error, setError] = useState('');
  const [newName, setNewName] = useState('');
  const [newDesc, setNewDesc] = useState('');
  const [newCost, setNewCost] = useState('');

  const load = useCallback(async () => {
    setError('');
    try {
      const [catRes, reqRes] = await Promise.all([
        fetch(`${api}/nday-points/catalog?active_only=false`),
        fetch(`${api}/nday-points/pending-redemptions`),
      ]);
      const catData = await catRes.json();
      const reqData = await reqRes.json();
      setCatalog(catData.items ?? []);
      setRequests(reqData.requests ?? []);
    } catch {
      setError('Network error.');
    }
  }, [api]);

  useEffect(() => { load(); }, [load]);

  const addItem = async () => {
    if (!newName.trim() || !newCost.trim()) return;
    try {
      await fetch(`${api}/nday-points/catalog`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: newName, description: newDesc || null, point_cost: parseInt(newCost, 10), created_by: 'dispatch_console' }),
      });
      setNewName(''); setNewDesc(''); setNewCost('');
      load();
    } catch {
      setError('Failed to add item.');
    }
  };

  const deactivate = async (id: number) => {
    try {
      await fetch(`${api}/nday-points/catalog/${id}/deactivate`, { method: 'POST' });
      load();
    } catch {
      setError('Failed to deactivate item.');
    }
  };

  const fulfill = async (id: number) => {
    try {
      await fetch(`${api}/nday-points/redemptions/${id}/fulfill`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fulfilled_by: 'dispatch_console' }),
      });
      load();
    } catch {
      setError('Failed to mark fulfilled.');
    }
  };

  const inputStyle = {
    background: '#0f172a', border: '1px solid #334155', borderRadius: 6,
    color: '#e2e8f0', padding: '8px 10px', fontSize: 13,
  };

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 900, margin: '0 auto' }}>
          <div style={{ marginBottom: 20 }}>
            <h1 style={{ margin: 0, fontSize: 22, fontWeight: 700, color: '#f1f5f9' }}>🎁 Swag Store — NDAY Points</h1>
            <p style={{ margin: '4px 0 0', fontSize: 13, color: '#64748b' }}>
              Manage the redemption catalog and fulfill pending requests.
            </p>
          </div>

          {error && (
            <div style={{ background: '#3b1e1e', border: '1px solid #7f1d1d', borderRadius: 8, padding: 16, color: '#f87171', marginBottom: 16 }}>
              {error}
            </div>
          )}

          <h2 style={{ fontSize: 16, fontWeight: 700, color: '#f1f5f9', marginBottom: 10 }}>Pending Redemptions</h2>
          {requests.length === 0 && <p style={{ color: '#64748b', marginBottom: 20 }}>Nothing pending.</p>}
          {requests.map(r => (
            <div key={r.id} style={{ background: '#1e293b', borderRadius: 10, padding: 14, marginBottom: 8, border: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <strong style={{ color: '#f1f5f9' }}>{r.driver}</strong>
                <span style={{ color: '#94a3b8', marginLeft: 8 }}>{r.item} ({r.point_cost} pts{r.is_cash_out ? ' — cash-out' : ''})</span>
                <div style={{ fontSize: 12, color: '#64748b' }}>{r.requested_at ? new Date(r.requested_at).toLocaleString() : ''}</div>
              </div>
              <button
                onClick={() => fulfill(r.id)}
                style={{ padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #2563eb', background: '#2563eb', color: '#fff' }}
              >
                Mark Fulfilled
              </button>
            </div>
          ))}

          <h2 style={{ fontSize: 16, fontWeight: 700, color: '#f1f5f9', margin: '28px 0 10px' }}>Catalog</h2>
          <div style={{ display: 'flex', gap: 8, marginBottom: 16, flexWrap: 'wrap' }}>
            <input placeholder="Item name" value={newName} onChange={e => setNewName(e.target.value)} style={{ ...inputStyle, flex: 2 }} />
            <input placeholder="Description (optional)" value={newDesc} onChange={e => setNewDesc(e.target.value)} style={{ ...inputStyle, flex: 3 }} />
            <input placeholder="Point cost" type="number" value={newCost} onChange={e => setNewCost(e.target.value)} style={{ ...inputStyle, width: 110 }} />
            <button onClick={addItem} style={{ padding: '8px 16px', borderRadius: 6, fontSize: 13, fontWeight: 600, cursor: 'pointer', border: '1px solid #2563eb', background: '#2563eb', color: '#fff' }}>
              Add Item
            </button>
          </div>

          {catalog.map(item => (
            <div key={item.id} style={{ background: '#1e293b', borderRadius: 10, padding: 14, marginBottom: 8, border: '1px solid #334155', display: 'flex', justifyContent: 'space-between', alignItems: 'center', opacity: item.active ? 1 : 0.5 }}>
              <div>
                <strong style={{ color: '#f1f5f9' }}>{item.name}</strong>
                <span style={{ color: '#94a3b8', marginLeft: 8 }}>{item.point_cost} pts</span>
                {item.description && <div style={{ fontSize: 12, color: '#64748b' }}>{item.description}</div>}
                {!item.active && <div style={{ fontSize: 11, color: '#f87171' }}>Inactive</div>}
              </div>
              {item.active && (
                <button
                  onClick={() => deactivate(item.id)}
                  style={{ padding: '6px 12px', borderRadius: 6, fontSize: 12, fontWeight: 600, cursor: 'pointer', border: '1px solid #334155', background: '#0f172a', color: '#94a3b8' }}
                >
                  Deactivate
                </button>
              )}
            </div>
          ))}
        </div>
      </div>
    </ProtectedRoute>
  );
}
