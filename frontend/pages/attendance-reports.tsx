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

const EVENT_LABELS: Record<string, string> = {
  call_in: '📞 Call-In',
  no_show: '🚫 No Show',
  late_arrival: '⏰ Late',
  early_departure: '🚪 Early Out',
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

function ptColor(pts: number) {
  if (pts >= 10) return 'text-red-400';
  if (pts >= 7.5) return 'text-orange-400';
  if (pts >= 5) return 'text-amber-400';
  return 'text-green-400';
}

function ptBg(pts: number) {
  if (pts >= 10) return 'bg-red-900/30 border-red-700/50';
  if (pts >= 7.5) return 'bg-orange-900/30 border-orange-700/50';
  if (pts >= 5) return 'bg-amber-900/30 border-amber-700/50';
  return 'bg-green-900/10 border-green-800/30';
}

function statusLabel(s: string) {
  const map: Record<string, string> = {
    termination: '🚨 Termination',
    final_warning: '⚠️ Final Warning',
    written_warning: '📋 Written Warning',
  };
  return map[s] ?? '✅ Good Standing';
}

function fmtDate(iso: string | null) {
  if (!iso) return '—';
  return new Date(iso + 'T12:00:00').toLocaleDateString('en-US', {
    weekday: 'short', month: 'short', day: 'numeric',
  });
}

interface AEvent {
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

interface DriverSummary {
  driver_name: string;
  current_points: number;
  status: string;
  next_threshold: { points: number; label: string; points_away: number };
  event_count: number;
  period_start: string;
}

interface HistoryResp {
  driver_name: string;
  days: number;
  total_events: number;
  missed_shifts: number;
  non_compliant_callins: number;
  voluntary_resign_risk: boolean;
  events: AEvent[];
}

interface ComplianceResp {
  date: string;
  total_callins: number;
  compliant: number;
  non_compliant: number;
  unknown: number;
  details: AEvent[];
}

interface RankingDriver {
  driver_name: string;
  attendance_score: number;
  attendance_points: number;
  attendance_status: string;
  safety_score: number | null;
  quality_score: number | null;
  overall_standing: string | null;
  composite_score: number | null;
  quality_week: string | null;
}

type Tab = 'ranking' | 'scorecard' | 'history' | 'compliance' | 'rc_queue';

function AttendanceReportsContent() {
  const [tab, setTab] = useState<Tab>('ranking');
  const todayStr = new Date().toLocaleDateString('en-CA');

  // Composite Ranking
  const [ranking, setRanking] = useState<{ quality_week: string | null; driver_count: number; drivers: RankingDriver[] } | null>(null);
  const [rankingLoading, setRankingLoading] = useState(false);

  // Scorecard
  const [scorecard, setScorecard] = useState<{ total: number; drivers: DriverSummary[] } | null>(null);
  const [scorecardLoading, setScorecardLoading] = useState(false);

  // History
  const [rosterNames, setRosterNames] = useState<string[]>([]);
  const [historyDriver, setHistoryDriver] = useState('');
  const [history, setHistory] = useState<HistoryResp | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  // Compliance
  const [compDate, setCompDate] = useState(todayStr);
  const [compliance, setCompliance] = useState<ComplianceResp | null>(null);
  const [compLoading, setCompLoading] = useState(false);

  // RC Queue
  const [rcQueue, setRcQueue] = useState<{ total: number; events: AEvent[] } | null>(null);
  const [rcLoading, setRcLoading] = useState(false);

  // Edit / Void
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editForm, setEditForm] = useState({ event_type: '', reason_code: '', notes: '', logged_by: '' });
  const [voidId, setVoidId] = useState<number | null>(null);
  const [saving, setSaving] = useState<number | null>(null);
  const [feedback, setFeedback] = useState('');

  useEffect(() => {
    fetch(`${resolveApi()}/attendance/roster-names`)
      .then(r => r.json())
      .then(d => setRosterNames(d.names ?? []))
      .catch(() => {});
  }, []);

  const loadRanking = useCallback(async () => {
    setRankingLoading(true);
    try {
      const r = await fetch(`${resolveApi()}/attendance/composite-ranking`, { credentials: 'include' });
      if (r.ok) setRanking(await r.json());
    } finally { setRankingLoading(false); }
  }, []);

  const loadScorecard = useCallback(async () => {
    setScorecardLoading(true);
    try {
      const r = await fetch(`${resolveApi()}/attendance/all-points`, { credentials: 'include' });
      if (r.ok) setScorecard(await r.json());
    } finally { setScorecardLoading(false); }
  }, []);

  const loadHistory = useCallback(async (name: string) => {
    if (!name) return;
    setHistoryLoading(true);
    setHistory(null);
    try {
      const r = await fetch(
        `${resolveApi()}/attendance/driver/${encodeURIComponent(name)}?days=90`,
        { credentials: 'include' }
      );
      if (r.ok) setHistory(await r.json());
    } finally { setHistoryLoading(false); }
  }, []);

  const loadCompliance = useCallback(async (d: string) => {
    setCompLoading(true);
    try {
      const r = await fetch(
        `${resolveApi()}/attendance/compliance?for_date=${d}`,
        { credentials: 'include' }
      );
      if (r.ok) setCompliance(await r.json());
    } finally { setCompLoading(false); }
  }, []);

  const loadRcQueue = useCallback(async () => {
    setRcLoading(true);
    try {
      const r = await fetch(`${resolveApi()}/attendance/pending-review`, { credentials: 'include' });
      if (r.ok) setRcQueue(await r.json());
    } finally { setRcLoading(false); }
  }, []);

  useEffect(() => {
    if (tab === 'ranking') loadRanking();
    if (tab === 'scorecard') loadScorecard();
    if (tab === 'compliance') loadCompliance(compDate);
    if (tab === 'rc_queue') loadRcQueue();
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab]);

  const refresh = useCallback(() => {
    if (tab === 'ranking') loadRanking();
    if (tab === 'scorecard') loadScorecard();
    if (tab === 'history' && historyDriver) loadHistory(historyDriver);
    if (tab === 'compliance') loadCompliance(compDate);
    if (tab === 'rc_queue') loadRcQueue();
  }, [tab, historyDriver, compDate, loadRanking, loadScorecard, loadHistory, loadCompliance, loadRcQueue]);

  const startEdit = (e: AEvent) => {
    setEditingId(e.id);
    setVoidId(null);
    setFeedback('');
    setEditForm({
      event_type: e.event_type,
      reason_code: e.reason_code ?? '',
      notes: e.notes ?? '',
      logged_by: e.logged_by ?? '',
    });
  };

  const saveEdit = async (id: number) => {
    setSaving(id);
    try {
      const res = await fetch(`${resolveApi()}/attendance/events/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          event_type: editForm.event_type || undefined,
          reason_code: editForm.reason_code || undefined,
          notes: editForm.notes || undefined,
          logged_by: editForm.logged_by || undefined,
        }),
      });
      if (res.ok) {
        setFeedback('Event updated.');
        setEditingId(null);
        refresh();
      } else {
        const d = await res.json();
        setFeedback(`Error: ${d.detail}`);
      }
    } catch {
      setFeedback('Network error.');
    } finally {
      setSaving(null);
    }
  };

  const doVoid = async (id: number) => {
    setSaving(id);
    try {
      const res = await fetch(`${resolveApi()}/attendance/events/${id}`, {
        method: 'DELETE',
        credentials: 'include',
      });
      if (res.ok) {
        setFeedback('Event voided.');
        setVoidId(null);
        refresh();
      } else {
        const d = await res.json();
        setFeedback(`Error: ${d.detail}`);
      }
    } catch {
      setFeedback('Network error.');
    } finally {
      setSaving(null);
    }
  };

  // Shared event card renderer (render function, not React component, to avoid remount issues)
  const renderEvent = (e: AEvent, showDriver = true) => (
    <div key={e.id} className={`rounded-xl border px-4 py-3 ${EVENT_COLORS[e.event_type] ?? 'bg-slate-800 border-slate-700'}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="flex-1 min-w-0">
          {showDriver && <p className="font-semibold text-sm">{e.driver_name}</p>}
          <p className="text-xs opacity-80 mt-0.5">
            {fmtDate(e.event_date)} · {EVENT_LABELS[e.event_type] ?? e.event_type}
            {e.reason_code ? ` · ${e.reason_code.replace('_', ' ')}` : ''}
            {e.scheduled_wave ? ` · Wave ${e.scheduled_wave}` : ''}
          </p>
          {e.logged_by && (
            <p className="text-xs opacity-50 mt-0.5">By: {e.logged_by}</p>
          )}
          {e.notes && (
            <p className="text-xs opacity-70 mt-1 italic">{e.notes}</p>
          )}
        </div>
        <div className="flex gap-1.5 flex-shrink-0">
          <button
            onClick={() => editingId === e.id ? setEditingId(null) : startEdit(e)}
            className="text-xs px-2 py-1 rounded bg-black/20 hover:bg-black/30 border border-white/10"
          >
            {editingId === e.id ? 'Cancel' : 'Edit'}
          </button>
          <button
            onClick={() => setVoidId(voidId === e.id ? null : e.id)}
            className="text-xs px-2 py-1 rounded bg-red-900/40 hover:bg-red-800/60 text-red-200 border border-red-700/30"
          >
            Void
          </button>
        </div>
      </div>

      {/* Compliance / flag badges */}
      <div className="flex flex-wrap gap-2 mt-2">
        {e.compliant === false && (
          <span className="text-xs bg-red-700/50 text-red-200 px-2 py-0.5 rounded-full">
            ⚠️ Non-compliant — {Math.abs(e.hours_before_shift ?? 0).toFixed(1)}h before shift
          </span>
        )}
        {e.compliant === true && (
          <span className="text-xs bg-green-700/50 text-green-200 px-2 py-0.5 rounded-full">
            ✅ {(e.hours_before_shift ?? 0).toFixed(1)}h before shift
          </span>
        )}
        {e.voluntary_resign_flag && (
          <span className="text-xs bg-red-800/70 text-red-200 px-2 py-0.5 rounded-full font-semibold">
            🚨 Resign Risk
          </span>
        )}
        {e.ringcentral_call_id && (
          <span className="text-xs bg-slate-700 text-slate-300 px-2 py-0.5 rounded-full">
            📞 RingCentral
          </span>
        )}
      </div>

      {/* Inline edit panel */}
      {editingId === e.id && (
        <div className="mt-3 pt-3 border-t border-white/10 space-y-2">
          <div className="grid grid-cols-2 gap-2">
            <div>
              <label className="text-xs opacity-60 block mb-0.5">Event Type</label>
              <select
                value={editForm.event_type}
                onChange={ev => setEditForm(f => ({ ...f, event_type: ev.target.value }))}
                className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-xs text-white"
              >
                {EVENT_TYPES.map(t => (
                  <option key={t} value={t}>{EVENT_LABELS[t] ?? t}</option>
                ))}
              </select>
            </div>
            <div>
              <label className="text-xs opacity-60 block mb-0.5">Reason Code</label>
              <select
                value={editForm.reason_code}
                onChange={ev => setEditForm(f => ({ ...f, reason_code: ev.target.value }))}
                className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-xs text-white"
              >
                <option value="">— none —</option>
                {REASON_CODES.map(r => (
                  <option key={r} value={r}>{r.replace('_', ' ')}</option>
                ))}
              </select>
            </div>
          </div>
          <input
            value={editForm.notes}
            onChange={ev => setEditForm(f => ({ ...f, notes: ev.target.value }))}
            placeholder="Notes…"
            className="w-full bg-slate-800 border border-slate-600 rounded px-2 py-1.5 text-xs text-white"
          />
          <div className="flex gap-2">
            <button
              onClick={() => saveEdit(e.id)}
              disabled={saving === e.id}
              className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-xs font-medium px-3 py-1.5 rounded"
            >
              {saving === e.id ? 'Saving…' : 'Save Changes'}
            </button>
            <button
              onClick={() => setEditingId(null)}
              className="bg-slate-700 hover:bg-slate-600 text-slate-300 text-xs px-3 py-1.5 rounded"
            >
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* Void confirmation */}
      {voidId === e.id && (
        <div className="mt-3 pt-3 border-t border-red-700/30 flex flex-wrap items-center gap-3">
          <p className="text-xs text-red-300">Permanently delete this event?</p>
          <button
            onClick={() => doVoid(e.id)}
            disabled={saving === e.id}
            className="bg-red-700 hover:bg-red-600 disabled:opacity-50 text-white text-xs font-medium px-3 py-1 rounded"
          >
            {saving === e.id ? 'Voiding…' : 'Confirm Void'}
          </button>
          <button
            onClick={() => setVoidId(null)}
            className="text-slate-400 hover:text-slate-200 text-xs"
          >
            Cancel
          </button>
        </div>
      )}
    </div>
  );

  return (
    <div className="min-h-screen bg-slate-900 text-white">
      <PageHeader title="Attendance Reports" showBack />

      <div className="max-w-5xl mx-auto px-4 py-6 space-y-6">

        {feedback && (
          <div className="flex items-center justify-between text-sm text-slate-200 bg-slate-800 border border-slate-600 rounded-lg px-4 py-2">
            {feedback}
            <button
              onClick={() => setFeedback('')}
              className="text-slate-400 hover:text-slate-200 text-lg leading-none ml-4"
            >
              ×
            </button>
          </div>
        )}

        {/* Tab bar */}
        <div className="flex flex-wrap gap-2 border-b border-slate-700 pb-1">
          {([
            { key: 'ranking',    label: '🏆 Composite Ranking' },
            { key: 'scorecard',  label: '📊 Points Scorecard' },
            { key: 'history',    label: '📋 Driver History' },
            { key: 'compliance', label: '⏱️ Compliance' },
            { key: 'rc_queue',   label: '📞 RC Queue' },
          ] as const).map(t => (
            <button
              key={t.key}
              onClick={() => setTab(t.key)}
              className={`px-4 py-2 text-sm font-medium rounded-t transition-colors ${
                tab === t.key ? 'bg-slate-700 text-white' : 'text-slate-400 hover:text-white'
              }`}
            >
              {t.label}
            </button>
          ))}
        </div>

        {/* ── COMPOSITE RANKING ── */}
        {tab === 'ranking' && (
          <div className="space-y-4">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-200">
                  Composite Driver Ranking
                </p>
                <p className="text-xs text-slate-400 mt-0.5">
                  Attendance 20% · Safety 40% · Quality 40%
                  {ranking?.quality_week ? ` · Quality data: ${ranking.quality_week}` : ' · No quality data loaded yet'}
                </p>
              </div>
              <button onClick={loadRanking} className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0">
                Refresh
              </button>
            </div>

            {/* Weight legend */}
            <div className="grid grid-cols-3 gap-2 text-xs">
              {[
                { label: 'Attendance', weight: '20%', note: '0 pts = 100 · 10 pts = 0', color: 'border-blue-700/50 text-blue-300' },
                { label: 'Safety', weight: '40%', note: 'Avg of Amazon safety sub-scores', color: 'border-amber-700/50 text-amber-300' },
                { label: 'Quality', weight: '40%', note: 'Amazon overall score', color: 'border-green-700/50 text-green-300' },
              ].map(({ label, weight, note, color }) => (
                <div key={label} className={`border rounded-lg px-3 py-2 bg-slate-800/60 ${color}`}>
                  <p className="font-bold text-sm">{weight} {label}</p>
                  <p className="opacity-70 mt-0.5">{note}</p>
                </div>
              ))}
            </div>

            {rankingLoading && <p className="text-slate-400 text-sm animate-pulse">Loading…</p>}

            {!rankingLoading && ranking?.drivers.map((d, i) => {
              const hasComposite = d.composite_score !== null;
              return (
                <div
                  key={d.driver_name}
                  className={`rounded-xl border px-4 py-3 ${
                    hasComposite
                      ? d.composite_score! >= 85 ? 'bg-green-900/20 border-green-700/40'
                      : d.composite_score! >= 70 ? 'bg-blue-900/20 border-blue-700/40'
                      : d.composite_score! >= 55 ? 'bg-amber-900/20 border-amber-700/40'
                      : 'bg-red-900/20 border-red-700/40'
                      : 'bg-slate-800 border-slate-700'
                  }`}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                      <span className="text-slate-500 text-xs w-5 text-right">{i + 1}</span>
                      <div>
                        <p className="font-semibold text-sm text-white">{d.driver_name}</p>
                        {d.overall_standing && (
                          <p className="text-xs text-slate-400 mt-0.5">Amazon: {d.overall_standing}</p>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-4">
                      {/* Component scores */}
                      <div className="flex gap-3 text-xs text-right">
                        <div>
                          <p className={`font-semibold ${ptColor(d.attendance_points)}`}>
                            {d.attendance_score.toFixed(0)}
                          </p>
                          <p className="text-slate-500">Att.</p>
                        </div>
                        <div>
                          <p className={`font-semibold ${d.safety_score !== null ? 'text-amber-300' : 'text-slate-600'}`}>
                            {d.safety_score !== null ? d.safety_score.toFixed(0) : '—'}
                          </p>
                          <p className="text-slate-500">Safety</p>
                        </div>
                        <div>
                          <p className={`font-semibold ${d.quality_score !== null ? 'text-green-300' : 'text-slate-600'}`}>
                            {d.quality_score !== null ? d.quality_score.toFixed(0) : '—'}
                          </p>
                          <p className="text-slate-500">Quality</p>
                        </div>
                      </div>

                      {/* Composite */}
                      <div className="text-right min-w-[3rem]">
                        {hasComposite ? (
                          <>
                            <p className={`text-2xl font-bold ${
                              d.composite_score! >= 85 ? 'text-green-400'
                              : d.composite_score! >= 70 ? 'text-blue-400'
                              : d.composite_score! >= 55 ? 'text-amber-400'
                              : 'text-red-400'
                            }`}>
                              {d.composite_score!.toFixed(1)}
                            </p>
                            <p className="text-xs text-slate-500">composite</p>
                          </>
                        ) : (
                          <p className="text-slate-600 text-xs">no data</p>
                        )}
                      </div>
                    </div>
                  </div>
                </div>
              );
            })}

            {!rankingLoading && ranking?.driver_count === 0 && (
              <p className="text-center text-slate-500 py-12 text-sm">No active drivers found.</p>
            )}
          </div>
        )}

        {/* ── SCORECARD ── */}
        {tab === 'scorecard' && (
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <p className="text-xs text-slate-400">
                {scorecard ? `${scorecard.total} active drivers — trailing 90 days — sorted by points` : ''}
              </p>
              <button onClick={loadScorecard} className="text-xs text-blue-400 hover:text-blue-300">
                Refresh
              </button>
            </div>

            {scorecardLoading && (
              <p className="text-slate-400 text-sm animate-pulse">Loading…</p>
            )}

            {!scorecardLoading && scorecard?.drivers.map(d => (
              <div
                key={d.driver_name}
                className={`rounded-xl border px-4 py-3 flex flex-wrap items-center justify-between gap-3 ${ptBg(d.current_points)}`}
              >
                <div>
                  <p className="font-semibold text-sm text-white">{d.driver_name}</p>
                  <p className="text-xs text-slate-400 mt-0.5">
                    {d.event_count} event{d.event_count !== 1 ? 's' : ''} · since {d.period_start}
                    {d.next_threshold.points_away > 0
                      ? ` · ${d.next_threshold.points_away}pt${d.next_threshold.points_away !== 1 ? 's' : ''} from ${d.next_threshold.label}`
                      : ''}
                  </p>
                </div>
                <div className="text-right">
                  <p className={`text-2xl font-bold ${ptColor(d.current_points)}`}>
                    {d.current_points.toFixed(1)}
                  </p>
                  <p className={`text-xs ${ptColor(d.current_points)}`}>{statusLabel(d.status)}</p>
                </div>
              </div>
            ))}

            {!scorecardLoading && scorecard?.total === 0 && (
              <p className="text-center text-slate-500 py-12 text-sm">No active drivers found.</p>
            )}
          </div>
        )}

        {/* ── DRIVER HISTORY ── */}
        {tab === 'history' && (
          <div className="space-y-4">
            <div>
              <label className="text-xs text-slate-400 block mb-1">Select Driver</label>
              <select
                value={historyDriver}
                onChange={e => {
                  setHistoryDriver(e.target.value);
                  setEditingId(null);
                  setVoidId(null);
                  if (e.target.value) loadHistory(e.target.value);
                }}
                className="w-full bg-slate-700 border border-slate-600 rounded-lg px-3 py-2.5 text-sm"
              >
                <option value="">— Select a driver —</option>
                {rosterNames.map(n => (
                  <option key={n} value={n}>{n}</option>
                ))}
              </select>
            </div>

            {historyLoading && (
              <p className="text-slate-400 text-sm animate-pulse">Loading…</p>
            )}

            {history && (
              <div className="space-y-4">
                {/* Summary cards */}
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Total Events', value: history.total_events, color: 'text-slate-200' },
                    {
                      label: 'Missed Shifts',
                      value: history.missed_shifts,
                      color: history.missed_shifts >= 2 ? 'text-red-400' : 'text-amber-400',
                    },
                    {
                      label: 'Non-Compliant',
                      value: history.non_compliant_callins,
                      color: history.non_compliant_callins > 0 ? 'text-orange-400' : 'text-green-400',
                    },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-slate-800 border border-slate-700 rounded-xl p-3 text-center">
                      <p className={`text-2xl font-bold ${color}`}>{value}</p>
                      <p className="text-xs text-slate-400 mt-0.5">{label}</p>
                    </div>
                  ))}
                </div>

                {history.voluntary_resign_risk && (
                  <div className="bg-red-900/30 border border-red-700/50 rounded-lg px-4 py-3 text-red-300 text-sm font-semibold">
                    🚨 Voluntary Resignation Risk — 2+ missed shifts in the last 90 days
                  </div>
                )}

                <div className="space-y-2">
                  {history.events.length === 0 ? (
                    <p className="text-center text-slate-500 py-8 text-sm">
                      No events in the last 90 days.
                    </p>
                  ) : (
                    history.events.map(e => renderEvent(e, false))
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── COMPLIANCE ── */}
        {tab === 'compliance' && (
          <div className="space-y-4">
            <div className="flex gap-3 items-end flex-wrap">
              <div>
                <label className="text-xs text-slate-400 block mb-1">Date</label>
                <input
                  type="date"
                  value={compDate}
                  onChange={e => setCompDate(e.target.value)}
                  className="bg-slate-700 border border-slate-600 rounded-lg px-3 py-2 text-sm text-white"
                />
              </div>
              <button
                onClick={() => loadCompliance(compDate)}
                disabled={compLoading}
                className="bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white text-sm font-medium px-4 py-2 rounded-lg"
              >
                {compLoading ? 'Loading…' : 'Load'}
              </button>
            </div>

            {compliance && (
              <div className="space-y-4">
                <div className="grid grid-cols-3 gap-3">
                  {[
                    { label: 'Compliant', value: compliance.compliant, color: 'text-green-400' },
                    { label: 'Non-Compliant', value: compliance.non_compliant, color: 'text-red-400' },
                    { label: 'Unknown', value: compliance.unknown, color: 'text-slate-400' },
                  ].map(({ label, value, color }) => (
                    <div key={label} className="bg-slate-800 border border-slate-700 rounded-xl p-3 text-center">
                      <p className={`text-2xl font-bold ${color}`}>{value}</p>
                      <p className="text-xs text-slate-400 mt-0.5">{label}</p>
                    </div>
                  ))}
                </div>

                <div className="space-y-2">
                  {compliance.details.length === 0 ? (
                    <p className="text-center text-slate-500 py-8 text-sm">
                      No call-ins logged for {compliance.date}.
                    </p>
                  ) : (
                    compliance.details.map(e => renderEvent(e))
                  )}
                </div>
              </div>
            )}
          </div>
        )}

        {/* ── RC QUEUE ── */}
        {tab === 'rc_queue' && (
          <div className="space-y-3">
            <div className="flex items-start justify-between gap-4">
              <div>
                <p className="text-sm font-medium text-slate-200">
                  {rcQueue ? `${rcQueue.total} event${rcQueue.total !== 1 ? 's' : ''} pending review` : 'RC Pending Review'}
                </p>
                <p className="text-xs text-slate-400 mt-0.5">
                  Auto-logged from inbound RingCentral calls. Use Edit to assign a reason code and close each out.
                </p>
              </div>
              <button onClick={loadRcQueue} className="text-xs text-blue-400 hover:text-blue-300 flex-shrink-0">
                Refresh
              </button>
            </div>

            {rcLoading && (
              <p className="text-slate-400 text-sm animate-pulse">Loading…</p>
            )}

            {!rcLoading && rcQueue?.total === 0 && (
              <div className="text-center py-12 text-slate-500">
                <p className="text-3xl mb-3">✅</p>
                <p className="text-sm">All caught up — no RC events pending review.</p>
              </div>
            )}

            <div className="space-y-2">
              {rcQueue?.events.map(e => renderEvent(e))}
            </div>
          </div>
        )}

      </div>
    </div>
  );
}

export default function AttendanceReportsPage() {
  return (
    <ProtectedRoute>
      <AttendanceReportsContent />
    </ProtectedRoute>
  );
}
