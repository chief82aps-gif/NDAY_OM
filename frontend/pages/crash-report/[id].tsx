import { useEffect, useState, useCallback } from 'react';
import { useRouter } from 'next/router';
import ProtectedRoute from '../../components/ProtectedRoute';

type Report = Record<string, any>;

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

type FieldType = 'text' | 'number' | 'date' | 'boolean' | 'select' | 'photo' | 'textarea';

interface FieldDef {
  key: string;
  label: string;
  type: FieldType;
  options?: string[];
}

interface Step {
  title: string;
  helper?: string;
  fields: FieldDef[];
  showIf?: (r: Report) => boolean;
}

const STEPS: Step[] = [
  {
    title: 'Safety First',
    helper: 'Complete these immediately at the scene.',
    fields: [
      { key: 'flashers_on', label: 'Emergency flashers turned on', type: 'boolean' },
      { key: 'vehicle_secured', label: 'Vehicle shut down and secured', type: 'boolean' },
      { key: 'police_called', label: 'Police called (if needed)', type: 'boolean' },
      { key: 'medical_requested', label: 'Medical assistance requested (if needed)', type: 'boolean' },
      { key: 'vehicle_not_moved', label: 'Vehicle not moved until police arrived', type: 'boolean' },
      { key: 'hotline_called', label: 'Called On-Road Emergency Hotline (1-844-311-0406)', type: 'boolean' },
      { key: 'hotline_call_at', label: 'Date/time of hotline call', type: 'date' },
      { key: 'dsp_owner_notified', label: 'DSP Owner/Dispatcher called (if applicable)', type: 'boolean' },
    ],
  },
  {
    title: 'General Information',
    fields: [
      { key: 'accident_date', label: 'Accident Date', type: 'date' },
      { key: 'accident_time', label: 'Time of Accident', type: 'text' },
      { key: 'accident_ampm', label: 'AM/PM', type: 'select', options: ['AM', 'PM'] },
      { key: 'location_address', label: 'Location of Accident (Address)', type: 'text' },
      { key: 'city_state_zip', label: 'City, State, Zip', type: 'text' },
      { key: 'driver_name', label: "Driver's Name", type: 'text' },
      { key: 'driver_license_number', label: 'Driver License #', type: 'text' },
      { key: 'driver_license_state', label: 'Driver License State', type: 'text' },
      { key: 'dsp_code', label: 'DSP Code', type: 'text' },
    ],
  },
  {
    title: 'Vehicle Information',
    fields: [
      { key: 'vehicle_year', label: 'Vehicle Year', type: 'text' },
      { key: 'vehicle_make_model', label: 'Vehicle Make & Model', type: 'text' },
      { key: 'license_plate_state', label: 'License Plate & State', type: 'text' },
      { key: 'equipment_number', label: 'Equipment # (Van)', type: 'text' },
      { key: 'vin', label: 'VIN', type: 'text' },
      { key: 'amzl_station_origin', label: 'AMZL Station (Origin)', type: 'text' },
      { key: 'destination_type', label: 'Destination', type: 'select', options: ['Delivery', 'AMZL Station', 'Vehicle Service'] },
    ],
  },
  {
    title: 'Third Party',
    helper: 'Only fill this out if another vehicle was involved.',
    fields: [
      { key: 'third_party_involved', label: 'Was another vehicle involved?', type: 'boolean' },
      { key: 'third_party_driver_name', label: "Third Party Driver's Name", type: 'text' },
      { key: 'third_party_driver_address', label: "Third Party Driver's Address", type: 'text' },
      { key: 'third_party_driver_phone', label: "Third Party Driver's Phone #", type: 'text' },
      { key: 'third_party_insurance', label: 'Insurance Co. & Policy No.', type: 'text' },
      { key: 'third_party_vehicle_year', label: 'Vehicle Year', type: 'text' },
      { key: 'third_party_vehicle_make_model', label: 'Make & Model', type: 'text' },
      { key: 'third_party_license_plate_state', label: 'License Plate & State', type: 'text' },
      { key: 'third_party_license_no', label: 'Driver License No.', type: 'text' },
      { key: 'third_party_license_state', label: 'State', type: 'text' },
    ],
  },
  {
    title: 'What Happened',
    fields: [
      { key: 'accident_description', label: 'Describe the accident and how it happened', type: 'textarea' },
    ],
  },
  {
    title: 'Conditions (Optional)',
    helper: 'Skip anything you don’t know — the police report typically covers this.',
    fields: [
      { key: 'num_lanes', label: 'Number of Lanes (each direction)', type: 'number' },
      { key: 'road_construction', label: 'Road Construction', type: 'select', options: ['Asphalt', 'Concrete', 'Gravel', 'Shell', 'Dirt'] },
      { key: 'road_attitude', label: 'Road Attitude', type: 'select', options: ['Straightaway', 'Intersection', 'Downhill', 'Curve', 'Uphill', 'Circle'] },
      { key: 'traffic_conditions', label: 'Traffic Conditions', type: 'select', options: ['Light', 'Medium', 'Congested', 'No Traffic'] },
      { key: 'light_conditions', label: 'Light Conditions', type: 'select', options: ['Daylight', 'Dawn/Dusk', 'Dark/Road Lighted', 'Dark/No Light'] },
      { key: 'road_conditions', label: 'Road Conditions', type: 'select', options: ['Dry/Normal', 'Wet', 'Muddy', 'Ice', 'Snow'] },
      { key: 'weather_conditions', label: 'Weather Conditions', type: 'select', options: ['Clear', 'Cloudy', 'Foggy', 'Raining', 'Sleeting', 'Snowing', 'Hailing', 'Dust Storm', 'Other'] },
    ],
  },
  {
    title: 'Police Report',
    helper: 'Only applicable if police were dispatched.',
    showIf: (r) => !!r.police_called,
    fields: [
      { key: 'police_department', label: 'Police Department Reported', type: 'text' },
      { key: 'officer_name', label: "Officer's Name", type: 'text' },
      { key: 'police_phone', label: 'Phone No.', type: 'text' },
      { key: 'police_report_no', label: 'Report No.', type: 'text' },
      { key: 'citation_issued', label: 'Citation Issued?', type: 'boolean' },
      { key: 'police_report_provided', label: 'Copy of police report provided to manager', type: 'boolean' },
    ],
  },
  {
    title: 'Photos & Diagram',
    fields: [
      { key: 'photo_urls', label: '360° photos of the scene', type: 'photo' },
      { key: 'diagram_url', label: 'Diagram of accident scene (photo)', type: 'photo' },
    ],
  },
];

function Field({ def, value, onChange, onPhoto }: {
  def: FieldDef;
  value: any;
  onChange: (v: any) => void;
  onPhoto?: (file: File) => void;
}) {
  const labelStyle: React.CSSProperties = { display: 'block', fontSize: 13, color: '#94a3b8', marginBottom: 6 };
  const inputStyle: React.CSSProperties = {
    width: '100%', padding: '10px 12px', borderRadius: 8, border: '1px solid #334155',
    background: '#1e293b', color: '#e2e8f0', fontSize: 14,
  };

  if (def.type === 'boolean') {
    return (
      <label style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 0', fontSize: 14, color: '#e2e8f0', cursor: 'pointer' }}>
        <input type="checkbox" checked={!!value} onChange={(e) => onChange(e.target.checked)} style={{ width: 18, height: 18 }} />
        {def.label}
      </label>
    );
  }
  if (def.type === 'select') {
    return (
      <div style={{ marginBottom: 16 }}>
        <label style={labelStyle}>{def.label}</label>
        <select value={value || ''} onChange={(e) => onChange(e.target.value)} style={inputStyle}>
          <option value="">— Select —</option>
          {def.options?.map((o) => <option key={o} value={o}>{o}</option>)}
        </select>
      </div>
    );
  }
  if (def.type === 'textarea') {
    return (
      <div style={{ marginBottom: 16 }}>
        <label style={labelStyle}>{def.label}</label>
        <textarea value={value || ''} onChange={(e) => onChange(e.target.value)} rows={6} style={{ ...inputStyle, resize: 'vertical' }} />
      </div>
    );
  }
  if (def.type === 'photo') {
    return (
      <div style={{ marginBottom: 16 }}>
        <label style={labelStyle}>{def.label}</label>
        <input
          type="file"
          accept="image/*"
          capture="environment"
          onChange={(e) => e.target.files?.[0] && onPhoto?.(e.target.files[0])}
          style={{ fontSize: 13, color: '#94a3b8' }}
        />
        {def.key === 'photo_urls' && Array.isArray(value) && value.length > 0 && (
          <div style={{ fontSize: 12, color: '#10b981', marginTop: 6 }}>{value.length} photo(s) uploaded</div>
        )}
        {def.key === 'diagram_url' && value && (
          <div style={{ fontSize: 12, color: '#10b981', marginTop: 6 }}>Diagram uploaded</div>
        )}
      </div>
    );
  }
  return (
    <div style={{ marginBottom: 16 }}>
      <label style={labelStyle}>{def.label}</label>
      <input
        type={def.type === 'date' ? 'date' : def.type === 'number' ? 'number' : 'text'}
        value={value || ''}
        onChange={(e) => onChange(e.target.value)}
        style={inputStyle}
      />
    </div>
  );
}

export default function CrashReportWizardPage() {
  const router = useRouter();
  const { id } = router.query;
  const api = resolveApi();

  const [report, setReport] = useState<Report | null>(null);
  const [step, setStep] = useState(0);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [missing, setMissing] = useState<string[] | null>(null);
  const [done, setDone] = useState(false);

  const load = useCallback(async () => {
    if (!id) return;
    const res = await fetch(`${api}/crash-report/${id}`, { cache: 'no-store' });
    if (res.ok) setReport(await res.json());
  }, [api, id]);

  useEffect(() => { load(); }, [load]);

  const activeSteps = STEPS.filter((s) => !s.showIf || s.showIf(report || {}));
  const current = activeSteps[step];

  const setField = (key: string, value: any) => {
    setReport((r) => (r ? { ...r, [key]: value } : r));
  };

  const saveStep = async () => {
    if (!report || !current) return true;
    setSaving(true);
    setError(null);
    try {
      const fields: Record<string, any> = {};
      current.fields.forEach((f) => { if (f.type !== 'photo') fields[f.key] = report[f.key] ?? null; });
      const res = await fetch(`${api}/crash-report/${id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields }),
      });
      if (!res.ok) throw new Error(await res.text());
      return true;
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to save');
      return false;
    } finally {
      setSaving(false);
    }
  };

  const next = async () => {
    if (await saveStep()) setStep((s) => Math.min(s + 1, activeSteps.length - 1));
  };
  const back = () => setStep((s) => Math.max(s - 1, 0));

  const uploadPhoto = async (kind: 'scene' | 'diagram', file: File) => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${api}/crash-report/${id}/upload-photo?kind=${kind}`, { method: 'POST', body: form });
    if (res.ok) await load();
  };

  const submit = async () => {
    if (!(await saveStep())) return;
    setSaving(true);
    setError(null);
    setMissing(null);
    try {
      const res = await fetch(`${api}/crash-report/${id}/submit`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok) {
        setMissing(data.detail?.missing_fields || null);
        return;
      }
      setDone(true);
    } catch (e: unknown) {
      setError(e instanceof Error ? e.message : 'Failed to submit');
    } finally {
      setSaving(false);
    }
  };

  if (!report) {
    return (
      <ProtectedRoute>
        <div style={{ minHeight: '100vh', background: '#0f172a', color: '#64748b', padding: 32 }}>Loading…</div>
      </ProtectedRoute>
    );
  }

  if (done) {
    return (
      <ProtectedRoute>
        <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
          <div style={{ maxWidth: 520, margin: '80px auto', textAlign: 'center' }}>
            <div style={{ fontSize: 48, marginBottom: 16 }}>✅</div>
            <h1 style={{ fontSize: 22, color: '#f1f5f9' }}>Report {report.report_number} Submitted</h1>
            <p style={{ color: '#94a3b8', fontSize: 14 }}>Management has been notified.</p>
          </div>
        </div>
      </ProtectedRoute>
    );
  }

  return (
    <ProtectedRoute>
      <div style={{ minHeight: '100vh', background: '#0f172a', color: '#e2e8f0', fontFamily: 'system-ui, sans-serif', padding: '32px 24px' }}>
        <div style={{ maxWidth: 560, margin: '0 auto' }}>
          <div style={{ marginBottom: 8, fontSize: 13, color: '#64748b' }}>
            Report {report.report_number} · Step {step + 1} of {activeSteps.length}
          </div>
          <h1 style={{ margin: '0 0 4px', fontSize: 20, fontWeight: 700, color: '#f1f5f9' }}>{current?.title}</h1>
          {current?.helper && <p style={{ margin: '0 0 20px', fontSize: 13, color: '#64748b' }}>{current.helper}</p>}

          <div style={{ background: '#1e293b', border: '1px solid #334155', borderRadius: 12, padding: 20, marginBottom: 20 }}>
            {current?.fields.map((f) => (
              <Field
                key={f.key}
                def={f}
                value={report[f.key]}
                onChange={(v) => setField(f.key, v)}
                onPhoto={(file) => uploadPhoto(f.key === 'diagram_url' ? 'diagram' : 'scene', file)}
              />
            ))}
          </div>

          {error && (
            <div style={{ background: '#450a0a', border: '1px solid #7f1d1d', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fca5a5' }}>
              {error}
            </div>
          )}
          {missing && (
            <div style={{ background: '#451a03', border: '1px solid #7c2d12', borderRadius: 8, padding: 12, marginBottom: 16, fontSize: 13, color: '#fdba74' }}>
              <b>Still needed before you can submit:</b>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {missing.map((m) => <li key={m}>{m}</li>)}
              </ul>
            </div>
          )}

          <div style={{ display: 'flex', gap: 10 }}>
            {step > 0 && (
              <button onClick={back} style={{ background: '#1e293b', color: '#94a3b8', border: '1px solid #334155', borderRadius: 8, padding: '12px 20px', cursor: 'pointer', fontWeight: 600, fontSize: 14 }}>
                Back
              </button>
            )}
            {step < activeSteps.length - 1 ? (
              <button onClick={next} disabled={saving} style={{ flex: 1, background: '#0ea5e9', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 20px', cursor: saving ? 'default' : 'pointer', fontWeight: 700, fontSize: 14 }}>
                {saving ? 'Saving…' : 'Next'}
              </button>
            ) : (
              <button onClick={submit} disabled={saving} style={{ flex: 1, background: '#ef4444', color: '#fff', border: 'none', borderRadius: 8, padding: '12px 20px', cursor: saving ? 'default' : 'pointer', fontWeight: 700, fontSize: 14 }}>
                {saving ? 'Submitting…' : 'Submit Report'}
              </button>
            )}
          </div>
        </div>
      </div>
    </ProtectedRoute>
  );
}
