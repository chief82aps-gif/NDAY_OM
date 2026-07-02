'use client';

import { useState, useEffect, useMemo } from 'react';
import { format } from 'date-fns';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';

/**
 * Enhanced Daily Screenshot Audit v2
 * 
 * Features:
 * - OCR image upload + copy-paste
 * - Semantic service type matching
 * - Training/Excluded service detection
 * - Variance validation with dispute workflow
 * - DA/route performance tracking
 * - Disputs summary generation
 */

// ============================================================================
// TYPE DEFINITIONS
// ============================================================================

interface ParsedMetrics {
  completed_routes: {header_value?: number; line_items?: any[]};
  delivered_packages: {header_value?: number; line_items?: any[]};
  dsp_late_cancel_max?: {header_value?: number};
}

interface ServiceMatch {
  [key: string]: {
    matched_type: string | null;
    is_training: boolean;
    is_excluded: boolean;
    canonical_name: string;
  };
}

interface Dispute {
  dispute_type: string;
  metric?: string;
  cortex_value?: number;
  wst_value?: number;
  reason: string;
  status: 'acknowledged' | 'dispute_submitted';
}

interface DARouteStat {
  driver_name: string;
  route_code: string;
  service_type: string;
  completed_stops: number;
  total_stops: number;
  completed_deliveries: number;
  total_deliveries: number;
  hours_worked: number;
}

interface ExcludedService {
  name: string;
  status: 'acknowledged' | 'dispute_submitted' | null;
  reason: string;
}

// ============================================================================
// MAIN COMPONENT
// ============================================================================

export default function EnhancedDailyScreenshotAudit() {
  const [step, setStep] = useState<'data-entry' | 'validation' | 'training' | 'excluded' | 'disputes' | 'summary'>(
    'data-entry'
  );

  // Data inputs
  const [selectedDate, setSelectedDate] = useState(format(new Date(), 'yyyy-MM-dd'));
  const [cortexRaw, setCortexRaw] = useState('');
  const [wstRaw, setWstRaw] = useState('');
  const [cortexFile, setCortexFile] = useState<File | null>(null);
  const [wstFile, setWstFile] = useState<File | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);

  // Parsed data
  const [cortexMetrics, setCortexMetrics] = useState<ParsedMetrics | null>(null);
  const [wstMetrics, setWstMetrics] = useState<ParsedMetrics | null>(null);
  const [statusMessage, setStatusMessage] = useState('');

  // Validation results
  const [validationIssues, setValidationIssues] = useState<string[]>([]);
  const [routeCountMatch, setRouteCountMatch] = useState(true);
  const [packageVariance, setPackageVariance] = useState(0);

  // Service matching
  const [serviceMatches, setServiceMatches] = useState<ServiceMatch>({});
  const [trainingServices, setTrainingServices] = useState<string[]>([]);
  const [excludedServices, setExcludedServices] = useState<ExcludedService[]>([]);

  // DA/Route stats
  const [daRouteStats, setDaRouteStats] = useState<DARouteStat[]>([]);
  const [newDAStat, setNewDAStat] = useState<Partial<DARouteStat>>({});

  // Disputes
  const [disputes, setDisputes] = useState<Dispute[]>([]);
  const [disputeModal, setDisputeModal] = useState<{isOpen: boolean; type?: string}>({isOpen: false});
  const [disputeReason, setDisputeReason] = useState('');

  const todayIso = format(new Date(), 'yyyy-MM-dd');

  // Real-time parsing: call backend parser when text is pasted
  const parseText = async (cortexText: string, wstText: string) => {
    if (!cortexText.trim() || !wstText.trim()) return;
    
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      const token = localStorage.getItem('access_token');
      const headers: Record<string, string> = {'Content-Type': 'application/json'};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      
      const response = await fetch(`${API_URL}/audit/screenshot/validate-text`, {
        method: 'POST',
        headers,
        body: JSON.stringify({cortex_raw: cortexText, wst_raw: wstText}),
      });

      if (response.ok) {
        const validation = await response.json();
        // Extract parsed metrics from validation response
        setSummaryData({
          cortexRoutes: validation.cortex_route_count || null,
          wstRoutes: validation.wst_route_count || null,
          cortexPackages: validation.cortex_package_count || null,
          wstPackages: validation.wst_package_count || null,
        });
      }
    } catch (error) {
      console.error('Error parsing text:', error);
    }
  };

  // State to store parsed summary data from backend
  const [summaryData, setSummaryData] = useState<{
    cortexRoutes: number | null;
    wstRoutes: number | null;
    cortexPackages: number | null;
    wstPackages: number | null;
  }>({
    cortexRoutes: null,
    wstRoutes: null,
    cortexPackages: null,
    wstPackages: null,
  });

  // ========================================================================
  // STEP 1: DATA ENTRY (OCR Upload + Copy-Paste)
  // ========================================================================

  const handleCortexPaste = (text: string) => {
    setCortexRaw(text);
    parseText(text, wstRaw);
  };

  const handleWstPaste = (text: string) => {
    setWstRaw(text);
    parseText(cortexRaw, text);
  };

  const handleFileUpload = async (file: File, type: 'cortex' | 'wst') => {
    setIsProcessing(true);
    const processingMsg = `⏳ Processing ${type.toUpperCase()} image with Google Cloud Vision...`;
    setStatusMessage(processingMsg);
    console.log(`[${type.toUpperCase()}] Starting OCR upload...`);
    
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      const token = localStorage.getItem('access_token');
      const formData = new FormData();
      formData.append('file', file);
      
      // Use the appropriate OCR endpoint with full path
      const endpoint = type === 'cortex' ? '/audit/screenshot/ocr-upload-cortex' : '/audit/screenshot/ocr-upload-wst';
      const fullUrl = `${API_URL}${endpoint}`;
      
      console.log(`[${type.toUpperCase()}] POST ${fullUrl}`);
      
      const response = await fetch(fullUrl, {
        method: 'POST',
        headers: token ? {'Authorization': `Bearer ${token}`} : {},
        body: formData,
      });

      console.log(`[${type.toUpperCase()}] Response status: ${response.status}`);
      
      if (response.ok) {
        const data = await response.json();
        
        console.log(`[${type.toUpperCase()}] OCR Response:`, data);
        
        // Extract text from response - check for text field
        const extractedText = data.text || '';
        
        console.log(`[${type.toUpperCase()}] Extracted text length: ${extractedText.length}`);
        
        if (!extractedText) {
          const msg = `⚠️ No text extracted from ${type.toUpperCase()} image (${extractedText.length} chars). Please paste text manually.`;
          setStatusMessage(msg);
          console.warn(`[${type.toUpperCase()}] ${msg}`);
          setIsProcessing(false);
          return;
        }
        
        if (type === 'cortex') {
          setCortexRaw(extractedText);
          console.log(`[CORTEX] Text set to cortexRaw state (${extractedText.length} chars)`);
        } else {
          setWstRaw(extractedText);
          console.log(`[WST] Text set to wstRaw state (${extractedText.length} chars)`);
        }
        
        const successMsg = `✅ ${type.toUpperCase()} text extracted successfully! (${extractedText.length} chars). Ready to validate.`;
        setStatusMessage(successMsg);
        console.log(`[${type.toUpperCase()}] ${successMsg}`);
      } else {
        const errorText = await response.text();
        console.error(`[${type.toUpperCase()}] Error response (${response.status}):`, errorText);
        
        let errorDetail = 'Unknown error';
        try {
          const errorJson = JSON.parse(errorText);
          errorDetail = errorJson.detail || errorText;
        } catch {
          errorDetail = errorText;
        }
        
        const msg = `❌ Error processing ${type} image: ${errorDetail}`;
        setStatusMessage(msg);
        console.error(`[${type.toUpperCase()}] ${msg}`);
      }
    } catch (error) {
      const msg = `❌ Network error: ${error instanceof Error ? error.message : 'Failed to process image'}`;
      setStatusMessage(msg);
      console.error(`[${type.toUpperCase()}] ${msg}`, error);
    } finally {
      setIsProcessing(false);
    }
  };

  const parseAndValidate = async (type: 'cortex' | 'wst', rawText: string) => {
    // TODO: Call backend parseSnapshot via /audit/screenshot/validate-text
    setStatusMessage(`Parsing ${type} data...`);
    
    // Placeholder - in real implementation, call backend
    // const response = await fetch(`/audit/screenshot/validate-text`, {
    //   method: 'POST',
    //   body: JSON.stringify({cortex_raw: cortexRaw, wst_raw: wstRaw})
    // });
  };

  const handleValidateData = async () => {
    if (!cortexRaw.trim() || !wstRaw.trim()) {
      setStatusMessage('Please enter data for both Cortex and WST');
      return;
    }

    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      const token = localStorage.getItem('access_token');
      const headers: Record<string, string> = {'Content-Type': 'application/json'};
      if (token) {
        headers['Authorization'] = `Bearer ${token}`;
      }
      const response = await fetch(`${API_URL}/audit/screenshot/validate-text`, {
        method: 'POST',
        headers,
        body: JSON.stringify({cortex_raw: cortexRaw, wst_raw: wstRaw}),
      });

      if (response.ok) {
        const validation = await response.json();
        setValidationIssues(validation.critical_issues);
        setRouteCountMatch(validation.route_count_match);
        setPackageVariance(validation.package_variance);

        // If there are disputes required, add them
        const newDisputes: Dispute[] = [];
        if (!validation.route_count_match) {
          newDisputes.push({
            dispute_type: 'route_count_mismatch',
            cortex_value: validation.cortex_route_count,
            wst_value: validation.wst_route_count,
            reason: '',
            status: 'dispute_submitted',
          });
        }
        if (validation.package_variance < -25) {
          newDisputes.push({
            dispute_type: 'package_variance',
            cortex_value: validation.cortex_package_count,
            wst_value: validation.wst_package_count,
            reason: '',
            status: 'dispute_submitted',
          });
        }
        setDisputes(newDisputes);

        setStep('validation');
        setStatusMessage('Validation complete. Review issues below.');
      }
    } catch (error) {
      setStatusMessage(`Error validating: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  // ========================================================================
  // STEP 2-4: SERVICE MATCHING, TRAINING, EXCLUDED
  // ========================================================================

  const handleMatchServices = async () => {
    if (!wstRaw.trim()) {
      setStatusMessage('Please enter WST data first');
      return;
    }

    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      
      // Extract service names from parsed data (placeholder)
      const serviceNames = ['Nursery Route Level 2', 'Standard Parcel Electric - Rivian'];
      
      const response = await fetch(`${API_URL}/audit/screenshot/match-services`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({service_names: serviceNames}),
      });

      if (response.ok) {
        const matches: ServiceMatch = await response.json();
        setServiceMatches(matches);

        // Separate training and excluded
        const training: string[] = [];
        const excluded: ExcludedService[] = [];

        for (const [name, match] of Object.entries(matches)) {
          if (match.is_training) {
            training.push(name);
          }
          if (match.is_excluded) {
            excluded.push({name, status: null, reason: ''});
          }
        }

        setTrainingServices(training);
        setExcludedServices(excluded);

        if (training.length > 0 || excluded.length > 0) {
          setStep('training');
        } else {
          setStep('disputes');
        }
      }
    } catch (error) {
      setStatusMessage(`Error matching services: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleExcludedServiceStatus = (index: number, status: 'acknowledged' | 'dispute_submitted') => {
    const updated = [...excludedServices];
    updated[index].status = status;
    setExcludedServices(updated);
  };

  // ========================================================================
  // STEP 5: DISPUTES
  // ========================================================================

  const canAddDispute = (type: string): boolean => {
    if (type === 'route_count_mismatch' && !routeCountMatch) return true;
    if (type === 'package_variance' && packageVariance < -25) return true;
    return true; // Can always add excluded/training disputes
  };

  const handleAddDispute = (type: string) => {
    setDisputeModal({isOpen: true, type});
    setDisputeReason('');
  };

  const handleSaveDispute = () => {
    if (!disputeModal.type || !disputeReason.trim()) {
      setStatusMessage('Please enter a reason for the dispute');
      return;
    }

    const newDispute: Dispute = {
      dispute_type: disputeModal.type,
      reason: disputeReason,
      status: 'dispute_submitted',
    };

    setDisputes([...disputes, newDispute]);
    setDisputeModal({isOpen: false});
    setDisputeReason('');
  };

  // ========================================================================
  // STEP 6: SUMMARY & SUBMISSION
  // ========================================================================

  const handleGenerateSummary = async () => {
    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      
      const response = await fetch(`${API_URL}/audit/screenshot/generate-dispute-summary`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({disputes, max_chars: 350}),
      });

      if (response.ok) {
        const result = await response.json();
        setStatusMessage(`Summary (${result.character_count}/350 chars): ${result.summary}`);
        setStep('summary');
      }
    } catch (error) {
      setStatusMessage(`Error generating summary: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleSubmitAudit = async () => {
    if (!selectedDate || !cortexRaw.trim() || !wstRaw.trim()) {
      setStatusMessage('Please complete all required fields');
      return;
    }

    try {
      const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';
      const token = localStorage.getItem('access_token');

      const payload = {
        audit_date: selectedDate,
        station: 'Daily Audit',
        cortex_raw: cortexRaw,
        wst_raw: wstRaw,
        cortex_route_count: 0, // TODO: Extract from parsed metrics
        wst_route_count: 0,
        cortex_package_count: 0,
        wst_package_count: 0,
        training_routes: trainingServices,
        excluded_services: excludedServices,
        disputes,
        da_route_stats: daRouteStats,
        notes: `Training routes: ${trainingServices.length}, Excluded: ${excludedServices.length}`,
      };

      const response = await fetch(`${API_URL}/audit/screenshot/submit-audit`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token && {'Authorization': `Bearer ${token}`}),
        },
        body: JSON.stringify(payload),
      });

      if (response.ok) {
        const result = await response.json();
        setStatusMessage(`✓ Audit submitted! ID: ${result.audit_id}`);
        // Reset form
        handleReset();
      }
    } catch (error) {
      setStatusMessage(`Error submitting: ${error instanceof Error ? error.message : 'Unknown error'}`);
    }
  };

  const handleReset = () => {
    setCortexRaw('');
    setWstRaw('');
    setCortexFile(null);
    setWstFile(null);
    setStep('data-entry');
    setServiceMatches({});
    setTrainingServices([]);
    setExcludedServices([]);
    setDisputes([]);
    setDaRouteStats([]);
    setStatusMessage('');
  };

  // ========================================================================
  // RENDER
  // ========================================================================

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
        <PageHeader title="Daily Screenshot Audit - Enhanced" showBack />

        <main className="max-w-7xl mx-auto px-4 py-8 space-y-6">
          
          {/* Progress indicator */}
          <div className="bg-white rounded-lg shadow-md p-4">
            <div className="flex gap-2 text-sm">
              {(['data-entry', 'validation', 'training', 'excluded', 'disputes', 'summary'] as const).map((s, idx) => (
                <div
                  key={s}
                  className={`px-3 py-1 rounded ${
                    step === s
                      ? 'bg-blue-600 text-white'
                      : ['data-entry', 'validation', 'training'].includes(s)
                      ? 'bg-gray-200 text-gray-700'
                      : 'bg-gray-100 text-gray-600'
                  }`}
                >
                  {s.replace('-', ' ')}
                </div>
              ))}
            </div>
          </div>

          {/* Status message */}
          {statusMessage && (
            <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
              <p className="text-sm text-blue-800">{statusMessage}</p>
            </div>
          )}

          {/* STEP 1: DATA ENTRY */}
          {step === 'data-entry' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 1: Data Entry</h2>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                {/* Cortex */}
                <div className="border rounded-lg p-4 space-y-3">
                  <h3 className="font-semibold text-gray-900">Cortex Data</h3>
                  <div>
                    <label className="block text-xs text-gray-600 mb-2">Upload Image or Paste Text</label>
                    <input
                      type="file"
                      accept="image/*"
                      className="w-full border rounded p-2 text-sm mb-2"
                      onChange={(e) => {
                        if (e.target.files?.[0]) {
                          setCortexFile(e.target.files[0]);
                          handleFileUpload(e.target.files[0], 'cortex');
                        }
                      }}
                    />
                  </div>
                  <textarea
                    className="w-full border rounded p-2 text-sm font-mono"
                    rows={10}
                    placeholder="Or paste Cortex text here..."
                    value={cortexRaw}
                    onChange={(e) => handleCortexPaste(e.target.value)}
                  />
                </div>

                {/* WST */}
                <div className="border rounded-lg p-4 space-y-3">
                  <h3 className="font-semibold text-gray-900">WST Data</h3>
                  <div>
                    <label className="block text-xs text-gray-600 mb-2">Upload Image or Paste Text</label>
                    <input
                      type="file"
                      accept="image/*"
                      className="w-full border rounded p-2 text-sm mb-2"
                      onChange={(e) => {
                        if (e.target.files?.[0]) {
                          setWstFile(e.target.files[0]);
                          handleFileUpload(e.target.files[0], 'wst');
                        }
                      }}
                    />
                  </div>
                  <textarea
                    className="w-full border rounded p-2 text-sm font-mono"
                    rows={10}
                    placeholder="Or paste WST text here..."
                    value={wstRaw}
                    onChange={(e) => handleWstPaste(e.target.value)}
                  />
                </div>
              </div>

              {/* Summary Matrix */}
              {(cortexRaw.trim() || wstRaw.trim()) && (
                <div className="border rounded-lg p-4 bg-gray-50">
                  <h3 className="font-semibold text-gray-900 mb-4">📊 Key Variables Summary</h3>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-gray-800">
                      <thead>
                        <tr className="border-b bg-gray-100">
                          <th className="text-left p-2 font-semibold">Metric</th>
                          <th className="text-center p-2 font-semibold">Cortex</th>
                          <th className="text-center p-2 font-semibold">WST</th>
                          <th className="text-center p-2 font-semibold">Variance</th>
                        </tr>
                      </thead>
                      <tbody>
                        <tr className="border-b hover:bg-gray-100">
                          <td className="p-2 font-semibold">Total Routes</td>
                          <td className="text-center p-2">{summaryData.cortexRoutes ?? '—'}</td>
                          <td className="text-center p-2">{summaryData.wstRoutes ?? '—'}</td>
                          <td className="text-center p-2">
                            {summaryData.cortexRoutes && summaryData.wstRoutes ? (
                              <span className={summaryData.wstRoutes - summaryData.cortexRoutes === 0 ? 'text-green-600' : 'text-red-600'}>
                                {summaryData.wstRoutes - summaryData.cortexRoutes > 0 ? '+' : ''}{summaryData.wstRoutes - summaryData.cortexRoutes}
                              </span>
                            ) : '—'}
                          </td>
                        </tr>
                        <tr className="border-b hover:bg-gray-100">
                          <td className="p-2 font-semibold">Total Packages</td>
                          <td className="text-center p-2">{summaryData.cortexPackages ?? '—'}</td>
                          <td className="text-center p-2">{summaryData.wstPackages ?? '—'}</td>
                          <td className="text-center p-2">
                            {summaryData.cortexPackages && summaryData.wstPackages ? (
                              <span className={Math.abs(summaryData.wstPackages - summaryData.cortexPackages) <= 25 ? 'text-green-600' : 'text-red-600'}>
                                {summaryData.wstPackages - summaryData.cortexPackages > 0 ? '+' : ''}{summaryData.wstPackages - summaryData.cortexPackages} ({((summaryData.wstPackages - summaryData.cortexPackages) / summaryData.cortexPackages * 100).toFixed(0)}%)
                              </span>
                            ) : '—'}
                          </td>
                        </tr>
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={handleReset}
                >
                  Reset
                </button>
                <button
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700 disabled:bg-gray-400"
                  disabled={!cortexRaw.trim() || !wstRaw.trim()}
                  onClick={handleValidateData}
                >
                  Validate & Continue
                </button>
              </div>
            </div>
          )}

          {/* STEP 2: VALIDATION */}
          {step === 'validation' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 2: Validation</h2>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <div className={`p-4 rounded-lg border-2 ${routeCountMatch ? 'border-green-500 bg-green-50' : 'border-red-500 bg-red-50'}`}>
                  <p className="font-semibold text-gray-900">Route Count Match</p>
                  <p className="text-sm text-gray-600">{routeCountMatch ? '✓ Routes reconciled' : '✗ Routes do not match'}</p>
                </div>
                <div className={`p-4 rounded-lg border-2 ${packageVariance >= -25 ? 'border-green-500 bg-green-50' : 'border-red-500 bg-red-50'}`}>
                  <p className="font-semibold text-gray-900">Package Variance</p>
                  <p className="text-sm text-gray-600">{packageVariance >= 0 ? '+' : ''}{packageVariance}% {Math.abs(packageVariance) > 25 ? '(exceeds ±25%)' : '(within tolerance)'}</p>
                </div>
              </div>

              {validationIssues.length > 0 && (
                <div className="bg-yellow-50 border-l-4 border-yellow-500 p-4">
                  <p className="font-semibold text-yellow-900 mb-2">Issues Found:</p>
                  <ul className="list-disc list-inside space-y-1">
                    {validationIssues.map((issue, idx) => (
                      <li key={idx} className="text-sm text-yellow-800">{issue}</li>
                    ))}
                  </ul>
                </div>
              )}

              <div className="border rounded-lg p-4">
                <h3 className="font-semibold text-gray-900 mb-3">Auto-Detected Disputes</h3>
                <div className="space-y-2">
                  {disputes.length === 0 ? (
                    <p className="text-sm text-gray-600">No disputes detected. Data is valid.</p>
                  ) : (
                    disputes.map((d, idx) => (
                      <div key={idx} className="bg-blue-50 p-3 rounded border border-blue-200">
                        <p className="text-sm font-semibold text-blue-900">{d.dispute_type.replace(/_/g, ' ')}</p>
                        {d.cortex_value !== undefined && d.wst_value !== undefined && (
                          <p className="text-xs text-blue-700">Cortex: {d.cortex_value} | WST: {d.wst_value}</p>
                        )}
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={() => setStep('data-entry')}
                >
                  Back
                </button>
                <button
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700"
                  onClick={() => {
                    handleMatchServices();
                    if (trainingServices.length > 0) {
                      setStep('training');
                    } else if (excludedServices.length > 0) {
                      setStep('excluded');
                    } else {
                      setStep('disputes');
                    }
                  }}
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* STEP 3: TRAINING SERVICES */}
          {step === 'training' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 3: Training Services</h2>
              
              {trainingServices.length === 0 ? (
                <p className="text-gray-600">No training services detected.</p>
              ) : (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600">The following services are marked as Training. Confirm or add notes:</p>
                  {trainingServices.map((service, idx) => (
                    <div key={idx} className="bg-purple-50 border border-purple-200 rounded-lg p-4">
                      <div className="flex justify-between items-start">
                        <div>
                          <p className="font-semibold text-gray-900">{service}</p>
                          <p className="text-xs text-gray-600 mt-1">Training Service</p>
                        </div>
                        <span className="inline-block bg-purple-200 text-purple-900 text-xs px-2 py-1 rounded">Training</span>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={() => setStep('validation')}
                >
                  Back
                </button>
                <button
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700"
                  onClick={() => setStep(excludedServices.length > 0 ? 'excluded' : 'disputes')}
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* STEP 4: EXCLUDED SERVICES */}
          {step === 'excluded' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 4: Excluded Services</h2>
              
              {excludedServices.length === 0 ? (
                <p className="text-gray-600">No excluded services detected.</p>
              ) : (
                <div className="space-y-4">
                  <p className="text-sm text-gray-600">Review excluded services and confirm status:</p>
                  {excludedServices.map((service, idx) => (
                    <div key={idx} className="bg-orange-50 border border-orange-200 rounded-lg p-4">
                      <div className="flex justify-between items-start mb-3">
                        <p className="font-semibold text-gray-900">{service.name}</p>
                        <span className="inline-block bg-orange-200 text-orange-900 text-xs px-2 py-1 rounded">Excluded</span>
                      </div>
                      <div className="flex gap-2">
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name={`service-${idx}`}
                            checked={service.status === 'acknowledged'}
                            onChange={() => handleExcludedServiceStatus(idx, 'acknowledged')}
                          />
                          <span className="text-sm text-gray-700">Acknowledged</span>
                        </label>
                        <label className="flex items-center gap-2 cursor-pointer">
                          <input
                            type="radio"
                            name={`service-${idx}`}
                            checked={service.status === 'dispute_submitted'}
                            onChange={() => handleExcludedServiceStatus(idx, 'dispute_submitted')}
                          />
                          <span className="text-sm text-gray-700">Dispute</span>
                        </label>
                      </div>
                    </div>
                  ))}
                </div>
              )}

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={() => setStep('training')}
                >
                  Back
                </button>
                <button
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700"
                  onClick={() => setStep('disputes')}
                >
                  Continue
                </button>
              </div>
            </div>
          )}

          {/* STEP 5: DISPUTES */}
          {step === 'disputes' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 5: Disputes</h2>
              
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <button
                  className="p-4 border rounded-lg hover:bg-blue-50 hover:border-blue-300 text-left"
                  onClick={() => handleAddDispute('route_count_mismatch')}
                  disabled={!canAddDispute('route_count_mismatch')}
                >
                  <p className="font-semibold text-gray-900">Route Count Mismatch</p>
                  <p className="text-xs text-gray-600">Add dispute for route variance</p>
                </button>
                <button
                  className="p-4 border rounded-lg hover:bg-blue-50 hover:border-blue-300 text-left"
                  onClick={() => handleAddDispute('package_variance')}
                  disabled={!canAddDispute('package_variance')}
                >
                  <p className="font-semibold text-gray-900">Package Variance</p>
                  <p className="text-xs text-gray-600">Add dispute for package mismatch</p>
                </button>
              </div>

              {disputes.length > 0 && (
                <div className="border rounded-lg p-4">
                  <h3 className="font-semibold text-gray-900 mb-3">Current Disputes ({disputes.length})</h3>
                  <div className="space-y-2">
                    {disputes.map((d, idx) => (
                      <div key={idx} className="bg-red-50 p-3 rounded border border-red-200">
                        <p className="text-sm font-semibold text-red-900">{d.dispute_type.replace(/_/g, ' ')}</p>
                        {d.reason && <p className="text-xs text-red-700 mt-1">{d.reason}</p>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Dispute Modal */}
              {disputeModal.isOpen && (
                <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50">
                  <div className="bg-white rounded-lg p-6 max-w-md w-full mx-4 space-y-4">
                    <h3 className="text-lg font-bold text-gray-900">Add Dispute: {disputeModal.type?.replace(/_/g, ' ')}</h3>
                    <textarea
                      className="w-full border rounded-lg p-2 text-sm"
                      rows={4}
                      maxLength={350}
                      placeholder="Explain the dispute (max 350 characters)"
                      value={disputeReason}
                      onChange={(e) => setDisputeReason(e.target.value)}
                    />
                    <p className="text-xs text-gray-600">{disputeReason.length}/350 characters</p>
                    <div className="flex gap-2">
                      <button
                        className="flex-1 px-4 py-2 bg-gray-300 text-gray-900 rounded-lg font-semibold hover:bg-gray-400"
                        onClick={() => setDisputeModal({isOpen: false})}
                      >
                        Cancel
                      </button>
                      <button
                        className="flex-1 px-4 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700"
                        onClick={handleSaveDispute}
                      >
                        Save Dispute
                      </button>
                    </div>
                  </div>
                </div>
              )}

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={() => setStep('excluded')}
                >
                  Back
                </button>
                <button
                  className="px-6 py-2 bg-blue-600 text-white rounded-lg font-semibold hover:bg-blue-700"
                  onClick={() => {
                    handleGenerateSummary();
                  }}
                >
                  Generate Summary
                </button>
              </div>
            </div>
          )}

          {/* STEP 6: SUMMARY & SUBMIT */}
          {step === 'summary' && (
            <div className="bg-white rounded-lg shadow-md p-6 space-y-6">
              <h2 className="text-2xl font-bold text-gray-900">Step 6: Summary & Submit</h2>
              
              <div className="bg-green-50 border border-green-200 rounded-lg p-4">
                <p className="font-semibold text-green-900 mb-2">Audit Summary</p>
                <div className="space-y-2 text-sm">
                  <p className="text-gray-700"><strong>Date:</strong> {selectedDate}</p>
                  <p className="text-gray-700"><strong>Training Services:</strong> {trainingServices.length}</p>
                  <p className="text-gray-700"><strong>Excluded Services:</strong> {excludedServices.length}</p>
                  <p className="text-gray-700"><strong>Disputes:</strong> {disputes.length}</p>
                  <p className="text-gray-700"><strong>Dispute Summary:</strong> {statusMessage.includes('Summary') ? statusMessage.split(': ')[1] : 'Generated upon submission'}</p>
                </div>
              </div>

              <div className="border rounded-lg p-4">
                <h3 className="font-semibold text-gray-900 mb-3">Disputes ({disputes.length})</h3>
                {disputes.length === 0 ? (
                  <p className="text-sm text-gray-600">No disputes recorded.</p>
                ) : (
                  <div className="space-y-2">
                    {disputes.map((d, idx) => (
                      <div key={idx} className="bg-gray-50 p-2 rounded text-sm">
                        <p className="font-semibold text-gray-900">{d.dispute_type.replace(/_/g, ' ')}</p>
                        {d.reason && <p className="text-gray-700">{d.reason}</p>}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="flex justify-between gap-4 pt-4 border-t">
                <button
                  className="px-6 py-2 bg-gray-400 text-white rounded-lg font-semibold hover:bg-gray-500"
                  onClick={() => setStep('disputes')}
                >
                  Back
                </button>
                <button
                  className="px-6 py-2 bg-green-600 text-white rounded-lg font-semibold hover:bg-green-700"
                  onClick={handleSubmitAudit}
                >
                  Submit Audit
                </button>
              </div>
            </div>
          )}
        </main>
      </div>
    </ProtectedRoute>
  );
}
