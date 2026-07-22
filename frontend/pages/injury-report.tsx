import { useState } from 'react';
import { useRouter } from 'next/router';
import ProtectedRoute from '../components/ProtectedRoute';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const inputStyle: React.CSSProperties = {
  width: '100%', background: '#0f172a', border: '1px solid #334155', borderRadius: 8,
  padding: '10px 12px', color: '#f1f5f9', fontSize: 14, boxSizing: 'border-box',
};
const labelStyle: React.CSSProperties = { fontSize: 13, color: '#94a3b8', marginBottom: 4, display: 'block' };
const fieldWrap: React.CSSProperties = { marginBottom: 14 };
const sectionStyle: React.CSSProperties = { background: '#1e293b', borderRadius: 12, padding: 24, marginBottom: 20, border: '1px solid #334155' };
const sectionTitle: React.CSSProperties = { fontSize: 16, fontWeight: 700, color: '#f1f5f9', margin: '0 0 4px' };
const sectionSub: React.CSSProperties = { fontSize: 13, color: '#64748b', margin: '0 0 18px' };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div style={fieldWrap}>
      <label style={labelStyle}>{label}</label>
      {children}
    </div>
  );
}

function YesNo({ value, onChange }: { value: boolean | null; onChange: (v: boolean) => void }) {
  return (
    <div style={{ display: 'flex', gap: 16 }}>
      <label style={{ fontSize: 13, color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: 6 }}>
        <input type="radio" checked={value === true} onChange={() => onChange(true)} /> Yes
      </label>
      <label style={{ fontSize: 13, color: '#e2e8f0', display: 'flex', alignItems: 'center', gap: 6 }}>
        <input type="radio" checked={value === false} onChange={() => onChange(false)} /> No
      </label>
    </div>
  );
}

export default function InjuryReportPage() {
  const router = useRouter();
  const api = resolveApi();

  const [form, setForm] = useState<Record<string, any>>({});
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState('');
  const [done, setDone] = useState(false);

  function set(field: string, value: any) {
    setForm(prev => ({ ...prev, [field]: value }));
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    if (!form.employee_name?.trim()) { setError('Employee name is required.'); return; }
    setSubmitting(true);
    try {
      const token = typeof window !== 'undefined' ? localStorage.getItem('access_token') : null;
      const user = typeof window !== 'undefined' ? localStorage.getItem('user') : null;
      const submittedBy = user ? (JSON.parse(user).name ?? JSON.parse(user).username) : undefined;
      const res = await fetch(`${api}/injury-reports`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token ? { Authorization: `Bearer ${token}` } : {}),
        },
        body: JSON.stringify({ ...form, submitted_by: submittedBy }),
      });
      if (!res.ok) {
        const d = await res.json().catch(() => null);
        throw new Error(d?.detail ?? 'Submission failed');
      }
      setDone(true);
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'Error submitting report.');
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <ProtectedRoute>
        <div style={{ minHeight: '100vh', background: '#0f172a', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif' }}>
          <div style={{ textAlign: 'center' }}>
            <p style={{ fontSize: 32 }}>✅</p>
            <p style={{ fontWeight: 700, fontSize: 18 }}>Injury Report Submitted</p>
            <p style={{ fontSize: 13, color: '#64748b', margin: '4px 0 20px' }}>
              It now appears on the Discipline Tracker awaiting Ops Manager sign-off, then HR.
            </p>
            <button
              onClick={() => router.push('/discipline-tracker')}
              style={{ background: '#1d4ed8', color: '#fff', border: 'none', borderRadius: 8, padding: '10px 24px', fontWeight: 600, cursor: 'pointer' }}
            >
              Go to Discipline Tracker
            </button>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <form onSubmit={handleSubmit} style={{ maxWidth: 720, margin: '0 auto' }}>
          <div style={{ marginBottom: 24 }}>
            <p style={{ fontSize: 12, color: '#64748b', textTransform: 'uppercase', letterSpacing: 2, margin: 0 }}>New Day Logistics</p>
            <h1 style={{ fontSize: 22, fontWeight: 700, color: '#f1f5f9', margin: '4px 0 0' }}>Injury / Incident Report</h1>
            <p style={{ fontSize: 13, color: '#64748b', margin: '4px 0 0' }}>
              Fill out both sections together with the employee at/soon after the incident.
            </p>
          </div>

          <div style={sectionStyle}>
            <p style={sectionTitle}>1. Employee Self-Report</p>
            <p style={sectionSub}>Completed with the injured employee's own account of what happened.</p>

            <Field label="Employee Name *">
              <input style={inputStyle} value={form.employee_name ?? ''} onChange={e => set('employee_name', e.target.value)} required />
            </Field>
            <Field label="What time did you immediately notify your supervisor?">
              <input style={inputStyle} value={form.supervisor_notified_at ?? ''} onChange={e => set('supervisor_notified_at', e.target.value)} placeholder="e.g. 2:15 PM" />
            </Field>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}><Field label="Date of Incident">
                <input type="date" style={inputStyle} value={form.incident_date ?? ''} onChange={e => set('incident_date', e.target.value)} />
              </Field></div>
              <div style={{ flex: 1 }}><Field label="Time of Incident">
                <input style={inputStyle} value={form.incident_time ?? ''} onChange={e => set('incident_time', e.target.value)} placeholder="e.g. 2:00 PM" />
              </Field></div>
            </div>
            <Field label="Where, exactly, did it happen?">
              <input style={inputStyle} value={form.location ?? ''} onChange={e => set('location', e.target.value)} />
            </Field>
            <Field label="What were you doing at the time?">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.activity_at_time ?? ''} onChange={e => set('activity_at_time', e.target.value)} />
            </Field>
            <Field label="Describe step by step what led up to the incident">
              <textarea style={{ ...inputStyle, minHeight: 90 }} value={form.incident_description ?? ''} onChange={e => set('incident_description', e.target.value)} />
            </Field>
            <Field label="What could have been done to prevent this incident?">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.prevention_suggestion ?? ''} onChange={e => set('prevention_suggestion', e.target.value)} />
            </Field>
            <Field label="What part(s) of your body were injured?">
              <input style={inputStyle} value={form.body_parts_injured ?? ''} onChange={e => set('body_parts_injured', e.target.value)} />
            </Field>
            <Field label="Do you want to seek medical care?">
              <YesNo value={form.wants_medical_care ?? null} onChange={v => set('wants_medical_care', v)} />
            </Field>
            {form.wants_medical_care && (
              <div style={{ display: 'flex', gap: 12 }}>
                <div style={{ flex: 1 }}><Field label="Whom did you see?">
                  <input style={inputStyle} value={form.medical_provider_name ?? ''} onChange={e => set('medical_provider_name', e.target.value)} />
                </Field></div>
                <div style={{ flex: 1 }}><Field label="Doctor's phone number">
                  <input style={inputStyle} value={form.medical_provider_phone ?? ''} onChange={e => set('medical_provider_phone', e.target.value)} />
                </Field></div>
              </div>
            )}
            <Field label="Has this part of your body been injured before?">
              <YesNo value={form.prior_injury ?? null} onChange={v => set('prior_injury', v)} />
            </Field>
            {form.prior_injury && (
              <Field label="If yes, when?">
                <input style={inputStyle} value={form.prior_injury_when ?? ''} onChange={e => set('prior_injury_when', e.target.value)} />
              </Field>
            )}
            <Field label="Supervisor">
              <input style={inputStyle} value={form.supervisor_name ?? ''} onChange={e => set('supervisor_name', e.target.value)} />
            </Field>
            <Field label="Employee Signature (type full name)">
              <input style={{ ...inputStyle, fontStyle: 'italic' }} value={form.employee_signature_name ?? ''} onChange={e => set('employee_signature_name', e.target.value)} placeholder="Type full name to sign" />
            </Field>
          </div>

          <div style={sectionStyle}>
            <p style={sectionTitle}>2. Supervisor's Accident Investigation</p>
            <p style={sectionSub}>Completed by the supervisor conducting the investigation.</p>

            <Field label="What part of the body was injured? Describe in detail.">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.investigation_body_part_detail ?? ''} onChange={e => set('investigation_body_part_detail', e.target.value)} />
            </Field>
            <Field label="What was the nature of the incident? Describe in detail.">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.incident_nature ?? ''} onChange={e => set('incident_nature', e.target.value)} />
            </Field>
            <Field label="Describe fully how the accident happened. What was the employee doing prior to the event? What equipment/tools were being used?">
              <textarea style={{ ...inputStyle, minHeight: 90 }} value={form.investigation_description ?? ''} onChange={e => set('investigation_description', e.target.value)} />
            </Field>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}><Field label="Date of Event">
                <input type="date" style={inputStyle} value={form.event_date ?? ''} onChange={e => set('event_date', e.target.value)} />
              </Field></div>
              <div style={{ flex: 1 }}><Field label="Time of Event">
                <input style={inputStyle} value={form.event_time ?? ''} onChange={e => set('event_time', e.target.value)} />
              </Field></div>
            </div>
            <Field label="Exact location of event">
              <input style={inputStyle} value={form.event_location_detail ?? ''} onChange={e => set('event_location_detail', e.target.value)} />
            </Field>
            <Field label="Weather">
              <input style={inputStyle} value={form.weather ?? ''} onChange={e => set('weather', e.target.value)} />
            </Field>
            <Field label="What caused the event and was it preventable?">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.cause_and_preventable ?? ''} onChange={e => set('cause_and_preventable', e.target.value)} />
            </Field>
            <Field label="Were safety regulations in place and being followed? If so describe; if not, why?">
              <YesNo value={form.safety_regs_followed ?? null} onChange={v => set('safety_regs_followed', v)} />
              <textarea style={{ ...inputStyle, minHeight: 50, marginTop: 8 }} value={form.safety_regs_detail ?? ''} onChange={e => set('safety_regs_detail', e.target.value)} placeholder="Explain" />
            </Field>
            <Field label="Recommended preventive action to prevent reoccurrence">
              <textarea style={{ ...inputStyle, minHeight: 60 }} value={form.recommended_preventive_action ?? ''} onChange={e => set('recommended_preventive_action', e.target.value)} />
            </Field>
            <Field label="Was medical care offered? If no, was a list of approved medical providers given to the employee?">
              <YesNo value={form.medical_care_offered ?? null} onChange={v => set('medical_care_offered', v)} />
            </Field>
            <Field label="Was an approved-provider list given?">
              <YesNo value={form.approved_provider_list_given ?? null} onChange={v => set('approved_provider_list_given', v)} />
            </Field>
            <div style={{ display: 'flex', gap: 12 }}>
              <div style={{ flex: 1 }}><Field label="Doctor's Name">
                <input style={inputStyle} value={form.actual_doctor_name ?? ''} onChange={e => set('actual_doctor_name', e.target.value)} />
              </Field></div>
              <div style={{ flex: 1 }}><Field label="Hospital Name">
                <input style={inputStyle} value={form.actual_hospital_name ?? ''} onChange={e => set('actual_hospital_name', e.target.value)} />
              </Field></div>
            </div>
            <Field label="Supervisor Signature (type full name)">
              <input style={{ ...inputStyle, fontStyle: 'italic' }} value={form.supervisor_signature_name ?? ''} onChange={e => set('supervisor_signature_name', e.target.value)} placeholder="Type full name to sign" />
            </Field>
          </div>

          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}

          <button
            type="submit"
            disabled={submitting}
            style={{ width: '100%', background: submitting ? '#334155' : '#1d4ed8', color: '#fff', border: 'none', borderRadius: 10, padding: '16px', fontSize: 16, fontWeight: 700, cursor: submitting ? 'wait' : 'pointer' }}
          >
            {submitting ? 'Submitting…' : 'Submit Injury Report'}
          </button>
        </form>
      </div>
    </ProtectedRoute>
  );
}
