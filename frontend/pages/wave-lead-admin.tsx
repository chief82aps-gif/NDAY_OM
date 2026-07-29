import { useEffect, useState, useCallback } from 'react';
import ProtectedRoute from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';

interface TeamStanding {
  team_id: number;
  wave_number: number;
  half: string;
  team_label: string;
  member_count: number;
  scored_member_count: number;
  avg_score: number | null;
  rank: number;
}

interface TeamMember {
  roster_id: number;
  payroll_name: string;
}

interface WaveLeadRole {
  id: number;
  wave_number: number | null;
  half: string | null;
  roster_id: number;
  payroll_name: string;
  assigned_at: string | null;
}

interface RosterDriver {
  id: number;
  payroll_name: string;
  has_pin: boolean;
}

interface TeamSuggestion {
  roster_id: number;
  payroll_name: string;
  sample_size: number;
  suggested_wave: number;
  suggested_half: string;
  suggested_team_id: number | null;
  suggested_team_label: string | null;
  current_team_id: number | null;
  current_team_label: string | null;
  matches_current: boolean;
}

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' } as React.CSSProperties,
  wrap: { maxWidth: 1000, margin: '0 auto' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 10, padding: 20, marginBottom: 20 } as React.CSSProperties,
  select: { background: '#0f172a', color: '#e2e8f0', border: '1px solid #334155', borderRadius: 6, padding: '6px 10px', fontSize: 13 } as React.CSSProperties,
  btn: { background: '#2563eb', color: '#fff', border: 'none', borderRadius: 6, padding: '6px 14px', fontSize: 13, fontWeight: 600, cursor: 'pointer' } as React.CSSProperties,
  btnDanger: { background: '#dc2626', color: '#fff', border: 'none', borderRadius: 6, padding: '4px 10px', fontSize: 12, fontWeight: 600, cursor: 'pointer' } as React.CSSProperties,
};

export default function WaveLeadAdminPage() {
  const { user } = useAuth();
  const api = resolveApi();

  const [drivers, setDrivers] = useState<RosterDriver[]>([]);
  const [teams, setTeams] = useState<TeamStanding[]>([]);
  const [members, setMembers] = useState<Record<number, TeamMember[]>>({});
  const [wave5Leads, setWave5Leads] = useState<{ id: number; roster_id: number; payroll_name: string }[]>([]);
  const [seniorLeads, setSeniorLeads] = useState<WaveLeadRole[]>([]);
  const [selectedDriver, setSelectedDriver] = useState<Record<number, string>>({});
  const [leadDriver, setLeadDriver] = useState<Record<number, string>>({});
  const [seniorLeadDriver, setSeniorLeadDriver] = useState<Record<string, string>>({});
  const [status, setStatus] = useState('');
  const [suggestions, setSuggestions] = useState<TeamSuggestion[] | null>(null);
  const [showMatching, setShowMatching] = useState(false);

  const loadAll = useCallback(async () => {
    try {
      const [driversRes, teamsRes, rolesRes] = await Promise.all([
        fetch(`${api}/attendance/roster-list`),
        fetch(`${api}/wave-lead/teams`),
        fetch(`${api}/wave-lead/roles`),
      ]);
      const driversData = await driversRes.json();
      const teamsData = await teamsRes.json();
      const rolesData = await rolesRes.json();

      setDrivers(driversData.drivers ?? []);
      setTeams(teamsData.teams ?? []);
      setWave5Leads((rolesData.roles ?? []).filter((r: WaveLeadRole) => r.wave_number === 5));
      setSeniorLeads((rolesData.roles ?? []).filter((r: WaveLeadRole) => !!r.half));

      const memberEntries = await Promise.all(
        (teamsData.teams ?? []).map((t: TeamStanding) =>
          fetch(`${api}/wave-lead/teams/${t.team_id}/members`).then(r => r.json())
        )
      );
      const memberMap: Record<number, TeamMember[]> = {};
      (teamsData.teams ?? []).forEach((t: TeamStanding, i: number) => {
        memberMap[t.team_id] = memberEntries[i]?.members ?? [];
      });
      setMembers(memberMap);
    } catch {
      setStatus('Failed to load.');
    }
  }, [api]);

  useEffect(() => { loadAll(); }, [loadAll]);

  const assignToTeam = async (teamId: number) => {
    const rosterId = selectedDriver[teamId];
    if (!rosterId) return;
    setStatus('Saving...');
    try {
      const res = await fetch(`${api}/wave-lead/teams/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ roster_id: parseInt(rosterId, 10), team_id: teamId, assigned_by: user?.username ?? 'dispatch' }),
      });
      if (res.ok) { setStatus('Assigned.'); await loadAll(); } else { setStatus('Failed to assign.'); }
    } catch { setStatus('Network error.'); }
  };

  const removeFromTeam = async (rosterId: number) => {
    setStatus('Removing...');
    try {
      const res = await fetch(`${api}/wave-lead/teams/members/${rosterId}`, { method: 'DELETE' });
      if (res.ok) { setStatus('Removed.'); await loadAll(); } else { setStatus('Failed to remove.'); }
    } catch { setStatus('Network error.'); }
  };

  const assignLead = async (waveNumber: number) => {
    const rosterId = leadDriver[waveNumber];
    if (!rosterId) return;
    setStatus('Saving...');
    try {
      const res = await fetch(`${api}/wave-lead/roles/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ wave_number: waveNumber, roster_id: parseInt(rosterId, 10), assigned_by: user?.username ?? 'dispatch' }),
      });
      if (res.ok) { setStatus('Lead assigned.'); await loadAll(); } else { setStatus('Failed to assign lead.'); }
    } catch { setStatus('Network error.'); }
  };

  const assignSeniorLead = async (half: 'front' | 'back') => {
    const rosterId = seniorLeadDriver[half];
    if (!rosterId) return;
    setStatus('Saving...');
    try {
      const res = await fetch(`${api}/wave-lead/senior/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ half, roster_id: parseInt(rosterId, 10), assigned_by: user?.username ?? 'dispatch' }),
      });
      if (res.ok) { setStatus('Senior Wave Lead assigned.'); await loadAll(); } else { setStatus('Failed to assign lead.'); }
    } catch { setStatus('Network error.'); }
  };

  const deactivateLead = async (roleId: number) => {
    setStatus('Removing lead...');
    try {
      const res = await fetch(`${api}/wave-lead/roles/${roleId}`, { method: 'DELETE' });
      if (res.ok) { setStatus('Lead removed.'); await loadAll(); } else { setStatus('Failed to remove lead.'); }
    } catch { setStatus('Network error.'); }
  };

  const loadSuggestions = async () => {
    setStatus('Analyzing 6 weeks of schedule history...');
    try {
      const res = await fetch(`${api}/wave-lead/suggest-teams?weeks=6`);
      const data = await res.json();
      setSuggestions(data.suggestions ?? []);
      setStatus('');
    } catch { setStatus('Failed to load suggestions.'); }
  };

  const applySuggestion = async (s: TeamSuggestion) => {
    if (!s.suggested_team_id) return;
    setStatus('Applying...');
    try {
      const res = await fetch(`${api}/wave-lead/teams/assign`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ roster_id: s.roster_id, team_id: s.suggested_team_id, assigned_by: user?.username ?? 'dispatch' }),
      });
      if (res.ok) {
        setStatus('Applied.');
        await loadAll();
        setSuggestions(prev => prev ? prev.map(x => x.roster_id === s.roster_id ? { ...x, current_team_id: s.suggested_team_id, current_team_label: s.suggested_team_label, matches_current: true } : x) : prev);
      } else { setStatus('Failed to apply.'); }
    } catch { setStatus('Network error.'); }
  };

  const teamsByWave: Record<number, TeamStanding[]> = {};
  for (const t of teams) {
    if (!teamsByWave[t.wave_number]) teamsByWave[t.wave_number] = [];
    teamsByWave[t.wave_number].push(t);
  }

  return (
    <ProtectedRoute>
      <div style={s.page}>
        <div style={s.wrap}>
          <h1 style={{ fontSize: 22, fontWeight: 700, margin: '0 0 6px' }}>🌊 Wave Lead Admin</h1>
          <p style={{ color: '#94a3b8', fontSize: 13, margin: '0 0 20px' }}>
            Assign standing wave leads and team membership. See Governance/05_NDL_Wave_Lead_Module_SRD.md for the full design.
          </p>
          {status && <p style={{ color: '#60a5fa', fontSize: 13 }}>{status}</p>}

          {/* Team suggestions from real schedule history */}
          <div style={s.card}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
              <h2 style={{ fontSize: 15, margin: 0 }}>🔎 Suggest Teams from Schedule History</h2>
              <button style={s.btn} onClick={loadSuggestions}>{suggestions ? 'Refresh' : 'Analyze Last 6 Weeks'}</button>
            </div>
            {!suggestions && (
              <p style={{ color: '#94a3b8', fontSize: 13, margin: 0 }}>
                Looks at each driver's actual wave time and days worked over the last 6 weeks to suggest which team they belong on — a starting point to review, not an auto-apply.
              </p>
            )}
            {suggestions && (
              <>
                <label style={{ fontSize: 12, color: '#94a3b8', display: 'flex', alignItems: 'center', gap: 6, marginBottom: 10 }}>
                  <input type="checkbox" checked={showMatching} onChange={e => setShowMatching(e.target.checked)} />
                  Show drivers whose suggestion already matches their current team
                </label>
                <div style={{ maxHeight: 420, overflowY: 'auto' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 13 }}>
                    <thead>
                      <tr style={{ color: '#94a3b8', textAlign: 'left' }}>
                        <th style={{ padding: '4px 8px' }}>Driver</th>
                        <th style={{ padding: '4px 8px' }}>Suggested</th>
                        <th style={{ padding: '4px 8px' }}>Current</th>
                        <th style={{ padding: '4px 8px' }}>Sample</th>
                        <th style={{ padding: '4px 8px' }}></th>
                      </tr>
                    </thead>
                    <tbody>
                      {suggestions.filter(row => showMatching || !row.matches_current).map(row => (
                        <tr key={row.roster_id} style={{ borderTop: '1px solid #334155' }}>
                          <td style={{ padding: '6px 8px' }}>{row.payroll_name}</td>
                          <td style={{ padding: '6px 8px', color: '#4ade80', fontWeight: 600 }}>{row.suggested_team_label ?? '—'}</td>
                          <td style={{ padding: '6px 8px', color: '#94a3b8' }}>{row.current_team_label ?? 'unassigned'}</td>
                          <td style={{ padding: '6px 8px', color: '#64748b' }}>{row.sample_size}</td>
                          <td style={{ padding: '6px 8px' }}>
                            {!row.matches_current && (
                              <button style={{ ...s.btn, padding: '3px 10px', fontSize: 12 }} onClick={() => applySuggestion(row)}>Apply</button>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}
          </div>

          {/* Senior Wave Lead — one per half, roving across ALL of waves 1-4 */}
          <div style={s.card}>
            <h2 style={{ fontSize: 15, margin: '0 0 4px' }}>👑 Senior Wave Lead</h2>
            <p style={{ color: '#94a3b8', fontSize: 12, margin: '0 0 12px' }}>
              One per half, independent/roving across all of Waves 1-4 — not tied to any single wave.
            </p>
            <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
              {(['front', 'back'] as const).map(half => {
                const lead = seniorLeads.find(l => l.half === half);
                return (
                  <div key={half}>
                    <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>{half === 'front' ? 'Front Half' : 'Back Half'}</div>
                    {lead ? (
                      <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                        <span>{lead.payroll_name}</span>
                        <button style={s.btnDanger} onClick={() => deactivateLead(lead.id)}>Remove</button>
                      </div>
                    ) : (
                      <div style={{ display: 'flex', gap: 8 }}>
                        <select
                          style={{ ...s.select, flex: 1 }}
                          value={seniorLeadDriver[half] ?? ''}
                          onChange={e => setSeniorLeadDriver(p => ({ ...p, [half]: e.target.value }))}
                        >
                          <option value="">— select driver —</option>
                          {drivers.map(d => <option key={d.id} value={d.id}>{d.payroll_name}</option>)}
                        </select>
                        <button style={s.btn} onClick={() => assignSeniorLead(half)}>Assign</button>
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          </div>

          {/* Wave 5 — 4x4 truck */}
          <div style={s.card}>
            <h2 style={{ fontSize: 15, margin: '0 0 10px' }}>🚚 Wave 5 — 4x4 Truck (independent, no team)</h2>
            {wave5Leads.map(l => (
              <div key={l.id} style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                <span>{l.payroll_name}</span>
                <button style={s.btnDanger} onClick={() => deactivateLead(l.id)}>Remove</button>
              </div>
            ))}
            <div style={{ display: 'flex', gap: 8, marginTop: 8 }}>
              <select style={s.select} value={leadDriver[5] ?? ''} onChange={e => setLeadDriver(p => ({ ...p, 5: e.target.value }))}>
                <option value="">— select driver —</option>
                {drivers.map(d => <option key={d.id} value={d.id}>{d.payroll_name}</option>)}
              </select>
              <button style={s.btn} onClick={() => assignLead(5)}>Add Wave 5 Lead</button>
            </div>
          </div>

          {/* Waves 1-4 */}
          {[1, 2, 3, 4].map(waveNumber => {
            return (
              <div key={waveNumber} style={s.card}>
                <h2 style={{ fontSize: 16, margin: '0 0 10px' }}>Wave {waveNumber}</h2>

                <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 16 }}>
                  {(teamsByWave[waveNumber] ?? []).map(team => (
                    <div key={team.team_id}>
                      <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>
                        {team.team_label}
                        {team.avg_score !== null && (
                          <span style={{ color: '#60a5fa', fontWeight: 700, marginLeft: 8 }}>#{team.rank} · {team.avg_score} avg</span>
                        )}
                      </div>
                      <ul style={{ margin: '0 0 8px', paddingLeft: 18, fontSize: 13 }}>
                        {(members[team.team_id] ?? []).map(m => (
                          <li key={m.roster_id} style={{ marginBottom: 3 }}>
                            {m.payroll_name}{' '}
                            <button style={{ ...s.btnDanger, padding: '1px 6px', fontSize: 10 }} onClick={() => removeFromTeam(m.roster_id)}>x</button>
                          </li>
                        ))}
                        {(members[team.team_id] ?? []).length === 0 && <li style={{ color: '#555' }}>No members yet</li>}
                      </ul>
                      <div style={{ display: 'flex', gap: 6 }}>
                        <select
                          style={{ ...s.select, flex: 1 }}
                          value={selectedDriver[team.team_id] ?? ''}
                          onChange={e => setSelectedDriver(p => ({ ...p, [team.team_id]: e.target.value }))}
                        >
                          <option value="">— add driver —</option>
                          {drivers.map(d => <option key={d.id} value={d.id}>{d.payroll_name}</option>)}
                        </select>
                        <button style={s.btn} onClick={() => assignToTeam(team.team_id)}>Add</button>
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </ProtectedRoute>
  );
}
