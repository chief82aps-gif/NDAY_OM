import { useEffect, useState } from 'react';
import { useRouter } from 'next/router';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

interface DriverInfo {
  transporter_id: string;
  driver_name: string;
  roster_id: number;
  van_number: string | null;
  wave: string | null;
  role: string;
  already_submitted: boolean;
  submitted_at: string | null;
}

type Step = 'auth' | 'survey' | 'done' | 'already_done' | 'error';

const GAS_OPTIONS = ['Full', '> 3/4 Tank', '< 3/4 Tank', 'Electric — Charging', 'Electric — Not Charging'];

const s = {
  page: { minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '24px 16px', display: 'flex', alignItems: 'flex-start', justifyContent: 'center' } as React.CSSProperties,
  card: { background: '#1e293b', borderRadius: 12, padding: 24, width: '100%', maxWidth: 540, boxShadow: '0 4px 24px rgba(0,0,0,0.4)' } as React.CSSProperties,
  label: { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6, fontWeight: 600 } as React.CSSProperties,
  input: { width: '100%', boxSizing: 'border-box' as const, background: '#0f172a', border: '1px solid #334155', borderRadius: 8, padding: '10px 14px', color: '#f1f5f9', fontSize: 15, marginBottom: 14 },
  section: { borderTop: '1px solid #334155', paddingTop: 16, marginTop: 16 } as React.CSSProperties,
  row: { display: 'flex', gap: 8, marginBottom: 14 } as React.CSSProperties,
  btn: (active: boolean, color = '#1e40af') => ({
    flex: 1, background: active ? color : '#0f172a', color: active ? '#fff' : '#64748b',
    border: `1px solid ${active ? color : '#334155'}`, borderRadius: 8,
    padding: '10px 0', cursor: 'pointer', fontWeight: 700, fontSize: 14,
  } as React.CSSProperties),
  submit: (disabled: boolean) => ({
    width: '100%', background: disabled ? '#1e293b' : '#16a34a',
    color: '#fff', border: 'none', borderRadius: 8, padding: '14px 0',
    fontSize: 16, fontWeight: 700, cursor: disabled ? 'default' : 'pointer', marginTop: 8,
  } as React.CSSProperties),
  chip: { background: '#172554', color: '#93c5fd', borderRadius: 6, padding: '4px 12px', fontSize: 12, fontWeight: 600 } as React.CSSProperties,
  pre: { background: '#0f172a', borderRadius: 8, padding: '10px 14px', fontSize: 13, color: '#94a3b8', marginBottom: 14 } as React.CSSProperties,
};

export default function EodPage() {
  const router = useRouter();
  const { tid, token } = router.query as { tid?: string; token?: string };
  const hasAuth = Boolean(tid || token);
  const api = resolveApi();
  const today = new Date().toISOString().slice(0, 10);

  const [step, setStep] = useState<Step>('auth');
  const [driverInfo, setDriverInfo] = useState<DriverInfo | null>(null);
  const [errorMsg, setErrorMsg] = useState('');
  const [submitting, setSubmitting] = useState(false);

  // Auth fields
  const [nameHint, setNameHint] = useState('');
  const [pin, setPin] = useState('');
  const [authLoading, setAuthLoading] = useState(false);

  // Survey fields
  const [vanNumber, setVanNumber] = useState('');
  const [wave, setWave] = useState('');
  const [role, setRole] = useState('Driver');
  const [clockedInOnTime, setClockedInOnTime] = useState<boolean | null>(null);
  const [actualClockIn, setActualClockIn] = useState('');
  const [clockInReason, setClockInReason] = useState('');
  const [vanIssues, setVanIssues] = useState<boolean | null>(null);
  const [vanDesc, setVanDesc] = useState('');
  const [incident, setIncident] = useState<boolean | null>(null);
  const [incidentReport, setIncidentReport] = useState<boolean | null>(null);
  const [injury, setInjury] = useState<boolean | null>(null);
  const [injuryReport, setInjuryReport] = useState<boolean | null>(null);
  const [medReview, setMedReview] = useState<boolean | null>(null);
  const [postDvic, setPostDvic] = useState<boolean | null>(null);
  const [gasLevel, setGasLevel] = useState('');
  const [packagesRts, setPackagesRts] = useState('0');
  const [routeIssues, setRouteIssues] = useState<boolean | null>(null);
  const [routeDesc, setRouteDesc] = useState('');
  const [sweep, setSweep] = useState<boolean | null>(null);
  const [sweepDetails, setSweepDetails] = useState('');
  const [tookLunch, setTookLunch] = useState<boolean | null>(null);
  const [lunchOut, setLunchOut] = useState('');
  const [lunchIn, setLunchIn] = useState('');
  const [clockOut, setClockOut] = useState('');
  const [pockets, setPockets] = useState<boolean | null>(null);
  const [mgmt, setMgmt] = useState<boolean | null>(null);
  const [equipOk, setEquipOk] = useState<boolean | null>(null);
  const [missingEquip, setMissingEquip] = useState('');

  const authenticate = async () => {
    if (!hasAuth && !pin) return;   // token/tid (Slack DM link) alone is sufficient; otherwise PIN is required
    setAuthLoading(true);
    setErrorMsg('');
    try {
      const params = new URLSearchParams();
      if (token) {
        params.set('token', token);
      } else if (tid) {
        params.set('transporter_id', tid);
      } else {
        params.set('pin', pin);
        if (nameHint.trim()) params.set('driver_name_hint', nameHint.trim());
      }
      const res = await fetch(`${api}/eod-survey/driver-lookup?${params}`);
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail || 'Authentication failed');
      }
      const info: DriverInfo = await res.json();
      setDriverInfo(info);
      if (info.already_submitted) {
        setStep('already_done');
        return;
      }
      // Pre-fill from assignment
      if (info.van_number) setVanNumber(info.van_number);
      if (info.wave) setWave(info.wave);
      if (info.role) setRole(info.role);
      setStep('survey');
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : 'Could not verify identity.');
    } finally {
      setAuthLoading(false);
    }
  };

  const canSubmit = () => (
    clockedInOnTime !== null &&
    vanIssues !== null &&
    incident !== null &&
    injury !== null &&
    postDvic !== null &&
    gasLevel !== '' &&
    routeIssues !== null &&
    sweep !== null &&
    tookLunch !== null &&
    pockets !== null &&
    mgmt !== null &&
    equipOk !== null &&
    clockOut !== ''
  );

  const submitSurvey = async () => {
    if (!driverInfo || !canSubmit()) return;
    setSubmitting(true);
    try {
      const body = {
        token: token || undefined,
        transporter_id: token ? undefined : driverInfo.transporter_id,
        pin,
        survey_date: today,
        van_number: vanNumber || driverInfo.van_number,
        wave: wave || driverInfo.wave,
        role,
        clocked_in_on_time: clockedInOnTime,
        actual_clock_in_time: clockedInOnTime ? null : actualClockIn,
        clock_in_reason: clockedInOnTime ? null : clockInReason,
        van_issues: vanIssues,
        van_issue_description: vanIssues ? vanDesc : null,
        incident_occurred: incident,
        incident_report_filed: incident ? incidentReport : null,
        injury_occurred: injury,
        injury_report_submitted: injury ? injuryReport : null,
        medical_review_completed: injury ? medReview : null,
        post_trip_dvic_completed: postDvic,
        gas_level: gasLevel,
        packages_rts: parseInt(packagesRts, 10) || 0,
        route_issues: routeIssues,
        route_issue_description: routeIssues ? routeDesc : null,
        performed_sweep: sweep,
        sweep_details: sweep ? sweepDetails : null,
        took_lunch: tookLunch,
        lunch_clock_out: tookLunch ? lunchOut : null,
        lunch_clock_in: tookLunch ? lunchIn : null,
        clock_out_time: clockOut,
        pockets_checked: pockets,
        needs_management_contact: mgmt,
        all_equipment_present: equipOk,
        missing_equipment: equipOk ? null : missingEquip,
      };
      const res = await fetch(`${api}/eod-survey/submit`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) {
        const e = await res.json();
        throw new Error(e.detail || 'Submission failed');
      }
      setStep('done');
    } catch (e: unknown) {
      setErrorMsg(e instanceof Error ? e.message : 'Submission error');
    } finally {
      setSubmitting(false);
    }
  };

  // ── YesNo toggle ───────────────────────────────────────────────────────────
  const YN = ({ value, onChange, yesLabel = 'Yes', noLabel = 'No', yesColor = '#16a34a', noColor = '#dc2626' }: {
    value: boolean | null;
    onChange: (v: boolean) => void;
    yesLabel?: string; noLabel?: string; yesColor?: string; noColor?: string;
  }) => (
    <div style={s.row}>
      <button style={s.btn(value === true, yesColor)} onClick={() => onChange(true)}>{yesLabel}</button>
      <button style={s.btn(value === false, noColor)} onClick={() => onChange(false)}>{noLabel}</button>
    </div>
  );

  // ── Auth step ──────────────────────────────────────────────────────────────
  if (step === 'auth') return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ margin: '0 0 4px', color: '#f1f5f9' }}>End of Day Survey</h2>
        <p style={{ margin: '0 0 20px', color: '#64748b', fontSize: 13 }}>
          {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
        </p>
        {!hasAuth && (
          <>
            <label style={s.label}>Your Full Name</label>
            <input style={s.input} placeholder="First Last" value={nameHint} onChange={e => setNameHint(e.target.value)} />
            <label style={s.label}>PIN (SSN Last 4)</label>
            <input style={s.input} type="password" placeholder="••••" maxLength={4} value={pin}
              onChange={e => setPin(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && authenticate()}
            />
          </>
        )}
        {hasAuth && (
          <p style={{ color: '#64748b', fontSize: 13, margin: '0 0 16px' }}>
            You're accessing this from your personal Slack link — no PIN needed.
          </p>
        )}
        {errorMsg && <p style={{ color: '#f87171', fontSize: 13, margin: '-8px 0 10px' }}>{errorMsg}</p>}
        <button
          onClick={authenticate}
          disabled={authLoading || (!hasAuth && !pin)}
          style={s.submit((!hasAuth && !pin) || authLoading)}
        >
          {authLoading ? 'Verifying…' : 'Start Survey'}
        </button>
      </div>
    </div>
  );

  if (step === 'already_done') return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Already Submitted</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          {driverInfo?.driver_name}, your end of day survey for today was already submitted.
          {driverInfo?.submitted_at && ` Submitted at ${new Date(driverInfo.submitted_at).toLocaleTimeString()}.`}
        </p>
      </div>
    </div>
  );

  if (step === 'done') return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ fontSize: 48, textAlign: 'center', marginBottom: 12 }}>✅</div>
        <h2 style={{ color: '#4ade80', textAlign: 'center', margin: '0 0 8px' }}>Survey Complete</h2>
        <p style={{ color: '#94a3b8', textAlign: 'center' }}>
          Thanks {driverInfo?.driver_name?.split(' ')[0]}! Your check-out has been recorded. Drive safe and see you next shift.
        </p>
      </div>
    </div>
  );

  if (step === 'error') return (
    <div style={s.page}>
      <div style={s.card}>
        <h2 style={{ color: '#f87171' }}>Error</h2>
        <p style={{ color: '#94a3b8' }}>{errorMsg}</p>
      </div>
    </div>
  );

  // ── Survey step ────────────────────────────────────────────────────────────
  return (
    <div style={s.page}>
      <div style={s.card}>
        <div style={{ marginBottom: 20 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <h2 style={{ margin: 0, color: '#f1f5f9', fontSize: 18 }}>End of Day Survey</h2>
            <span style={s.chip}>{driverInfo?.driver_name?.split(' ')[0]}</span>
          </div>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#64748b' }}>
            {new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}
          </p>
        </div>

        {/* Pre-filled info */}
        <div style={s.pre}>
          <strong style={{ color: '#f1f5f9' }}>{driverInfo?.driver_name}</strong>
          {vanNumber && <span style={{ color: '#64748b' }}> · Van {vanNumber}</span>}
          {wave && <span style={{ color: '#64748b' }}> · Wave {wave}</span>}
          {role && <span style={{ color: '#64748b' }}> · {role}</span>}
        </div>

        {/* 1. Clock-in */}
        <label style={s.label}>Did you clock in on time (10:00 AM)?</label>
        <YN value={clockedInOnTime} onChange={setClockedInOnTime} />
        {clockedInOnTime === false && (
          <>
            <label style={s.label}>Actual clock-in time</label>
            <input style={s.input} type="time" value={actualClockIn} onChange={e => setActualClockIn(e.target.value)} />
            <label style={s.label}>Reason for late / early clock-in</label>
            <input style={s.input} placeholder="Brief explanation" value={clockInReason} onChange={e => setClockInReason(e.target.value)} />
          </>
        )}

        {/* 2. Van issues */}
        <div style={s.section}>
          <label style={s.label}>Any issues to report with the van?</label>
          <YN value={vanIssues} onChange={setVanIssues} yesColor='#f59e0b' noColor='#16a34a' />
          {vanIssues && (
            <>
              <label style={s.label}>Describe the issue</label>
              <input style={s.input} placeholder="What's wrong with the van?" value={vanDesc} onChange={e => setVanDesc(e.target.value)} />
            </>
          )}
        </div>

        {/* 3. Incident */}
        <div style={s.section}>
          <label style={s.label}>Were you involved in any incident or property damage?</label>
          <YN value={incident} onChange={setIncident} yesColor='#dc2626' noColor='#16a34a' />
          {incident && (
            <>
              <label style={s.label}>Was an incident / crash report filed?</label>
              <YN value={incidentReport} onChange={setIncidentReport} />
            </>
          )}
        </div>

        {/* 4. Injury */}
        <div style={s.section}>
          <label style={s.label}>Did you incur any injury while on route?</label>
          <YN value={injury} onChange={setInjury} yesColor='#dc2626' noColor='#16a34a' />
          {injury && (
            <>
              <label style={s.label}>Did you submit an injury report?</label>
              <YN value={injuryReport} onChange={setInjuryReport} />
              <label style={s.label}>Was a medical review completed?</label>
              <YN value={medReview} onChange={setMedReview} />
            </>
          )}
        </div>

        {/* 5. Post-trip DVIC */}
        <div style={s.section}>
          <label style={s.label}>Post-trip DVIC completed?</label>
          <YN value={postDvic} onChange={setPostDvic} />
        </div>

        {/* 6. Gas level */}
        <div style={s.section}>
          <label style={s.label}>Gas / charge level</label>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 14 }}>
            {GAS_OPTIONS.map(opt => (
              <button key={opt}
                onClick={() => setGasLevel(opt)}
                style={{
                  background: gasLevel === opt ? '#1e40af' : '#0f172a',
                  color: gasLevel === opt ? '#fff' : '#94a3b8',
                  border: `1px solid ${gasLevel === opt ? '#1e40af' : '#334155'}`,
                  borderRadius: 8, padding: '8px 14px', cursor: 'pointer', fontSize: 13,
                }}
              >{opt}</button>
            ))}
          </div>
        </div>

        {/* 7. RTS */}
        <div style={s.section}>
          <label style={s.label}>Packages returned to station (RTS)</label>
          <input style={s.input} type="number" min="0" value={packagesRts} onChange={e => setPackagesRts(e.target.value)} />
        </div>

        {/* 8. Route issues */}
        <div style={s.section}>
          <label style={s.label}>Any route issues to report?</label>
          <YN value={routeIssues} onChange={setRouteIssues} yesColor='#f59e0b' noColor='#16a34a' />
          {routeIssues && (
            <>
              <label style={s.label}>Briefly describe the issue</label>
              <input style={s.input} placeholder="What happened?" value={routeDesc} onChange={e => setRouteDesc(e.target.value)} />
            </>
          )}
        </div>

        {/* 9. Sweep */}
        <div style={s.section}>
          <label style={s.label}>Did you perform a sweep today?</label>
          <YN value={sweep} onChange={setSweep} />
          {sweep && (
            <>
              <label style={s.label}>Who did you sweep and how many packages?</label>
              <input style={s.input} placeholder="Driver name · X packages" value={sweepDetails} onChange={e => setSweepDetails(e.target.value)} />
            </>
          )}
        </div>

        {/* 10. Lunch */}
        <div style={s.section}>
          <label style={s.label}>Did you take a lunch break?</label>
          <YN value={tookLunch} onChange={setTookLunch} />
          {tookLunch && (
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}>
                <label style={s.label}>Clocked out</label>
                <input style={s.input} type="time" value={lunchOut} onChange={e => setLunchOut(e.target.value)} />
              </div>
              <div style={{ flex: 1 }}>
                <label style={s.label}>Clocked back in</label>
                <input style={s.input} type="time" value={lunchIn} onChange={e => setLunchIn(e.target.value)} />
              </div>
            </div>
          )}
        </div>

        {/* 11. Clock-out */}
        <div style={s.section}>
          <label style={s.label}>What time did you clock out?</label>
          <input style={s.input} type="time" value={clockOut} onChange={e => setClockOut(e.target.value)} />
        </div>

        {/* 12. Pockets */}
        <div style={s.section}>
          <label style={s.label}>Did you check your pockets for keys, gas cards, or chargers?</label>
          <YN value={pockets} onChange={setPockets} />
        </div>

        {/* 13. Equipment */}
        <div style={s.section}>
          <label style={s.label}>Was all required equipment present in the van?</label>
          <YN value={equipOk} onChange={setEquipOk} yesColor='#16a34a' noColor='#f59e0b' />
          {equipOk === false && (
            <>
              <label style={s.label}>What was missing?</label>
              <input style={s.input} placeholder="e.g. First Aid Kit, Dolly…" value={missingEquip} onChange={e => setMissingEquip(e.target.value)} />
            </>
          )}
        </div>

        {/* 14. Management */}
        <div style={s.section}>
          <label style={s.label}>Do you need to speak with HR or management about any issue?</label>
          <YN value={mgmt} onChange={setMgmt} yesColor='#f59e0b' noColor='#16a34a' />
        </div>

        {/* Submit */}
        <div style={{ marginTop: 24 }}>
          {errorMsg && <p style={{ color: '#f87171', fontSize: 13, marginBottom: 10 }}>{errorMsg}</p>}
          <button
            onClick={submitSurvey}
            disabled={!canSubmit() || submitting}
            style={s.submit(!canSubmit() || submitting)}
          >
            {submitting ? 'Submitting…' : 'Submit End of Day Survey'}
          </button>
          {!canSubmit() && (
            <p style={{ fontSize: 11, color: '#64748b', textAlign: 'center', marginTop: 8 }}>
              Please answer all questions above before submitting.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}
