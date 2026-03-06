'use client';

import { useState } from 'react';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';

/**
 * Simplified Daily Screenshot Audit
 * 
 * Workflows:
 * 1. Data Entry: Enter Cortex & WST metrics
 * 2. Validation: Check variance (routes match, packages ≤25)
 * 3. Disputes: Flag discrepancies, add notes, audit trail
 */

interface ValidationResult {
  routesMatch: boolean;
  routesDifference: number;
  packagesVariance: number;
  packagesWithinTolerance: boolean;
  issues: string[];
}

interface DisputeRecord {
  id: string;
  date: string;
  type: 'route_mismatch' | 'package_variance';
  cortexValue: number;
  wstValue: number;
  notes: string;
  resolved: boolean;
}

export default function DailyScreenshotAudit() {
  const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000/api';
  const [step, setStep] = useState<'entry' | 'validation' | 'disputes'>('entry');
  const [selectedDate, setSelectedDate] = useState(new Date().toISOString().slice(0, 10));

  // ===== STEP 1: DATA ENTRY =====
  const [cortexRoutes, setCortexRoutes] = useState('');
  const [cortexPackages, setCortexPackages] = useState('');
  const [wstRoutes, setWstRoutes] = useState('');
  const [wstPackages, setWstPackages] = useState('');
  const [packageStatusBreakdown, setPackageStatusBreakdown] = useState({
    remaining: 0,
    undeliverable: 0,
    returnedToStation: 0,
    pickupFailed: 0,
  });

  // ===== STEP 2: VALIDATION =====
  const [validationResult, setValidationResult] = useState<ValidationResult | null>(null);

  // ===== STEP 3: DISPUTES =====
  const [disputes, setDisputes] = useState<DisputeRecord[]>([]);
  const [disputeNotes, setDisputeNotes] = useState('');
  const [auditLog, setAuditLog] = useState<string[]>([]);

  // ===== HELPERS =====
  const parseText = (text: string, type: 'cortex' | 'wst') => {
    // Clean up text
    const lines = text.split('\n').map(l => l.trim()).filter(l => l.length > 0);
    
    if (type === 'cortex') {
      // Extract routes: Look for "Total" label with preceding number
      let routes = 0;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].toLowerCase() === 'total' && i > 0) {
          const prevLine = lines[i - 1]?.trim();
          if (prevLine && /^\d+$/.test(prevLine)) {
            routes = parseInt(prevLine);
            break;
          }
        }
      }
      
      // Extract packages: Sum numerators from "X/Y deliveries" lines
      let rawPackages = 0;
      const regex = /(\d+)\/(\d+)\s+deliveries/gi;
      let match;
      while ((match = regex.exec(text)) !== null) {
        rawPackages += parseInt(match[1]);
      }
      
      // Extract Package Status items to subtract (Remaining, Undeliverable, Returned to station, Pickup failed)
      let packageStatusSubtract = 0;
      const breakdown = { remaining: 0, undeliverable: 0, returnedToStation: 0, pickupFailed: 0 };
      const statusItems = [
        { key: 'remaining', label: 'remaining' },
        { key: 'undeliverable', label: 'undeliverable' },
        { key: 'returnedToStation', label: 'returned to station' },
        { key: 'pickupFailed', label: 'pickup failed' },
      ];
      for (const item of statusItems) {
        for (let i = 0; i < lines.length; i++) {
          if (lines[i].toLowerCase() === item.label) {
            const nextLine = lines[i + 1]?.trim();
            if (nextLine && /^\d+$/.test(nextLine)) {
              const num = parseInt(nextLine);
              breakdown[item.key as keyof typeof breakdown] = num;
              packageStatusSubtract += num;
              break;
            }
          }
        }
      }
      setPackageStatusBreakdown(breakdown);

      
      // Extract Customer Returns Complete to add
      let customerReturnsAdd = 0;
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].toLowerCase() === 'complete') {
          const prevLine = lines[i - 1]?.trim();
          if (prevLine && /^\d+$/.test(prevLine)) {
            customerReturnsAdd = parseInt(prevLine);
            break;
          }
        }
      }
      
      // Calculate final adjusted packages
      const adjustedPackages = rawPackages - packageStatusSubtract + customerReturnsAdd;
      
      setCortexRoutes(routes.toString());
      setCortexPackages(adjustedPackages.toString());
      const details = `raw: ${rawPackages} | subtract: ${packageStatusSubtract} | add: ${customerReturnsAdd} | final: ${adjustedPackages}`;
      logAction(`Cortex parsed: ${routes} routes, ${adjustedPackages} packages (${details}`);
    } else {
      // WST: Get total routes from FIRST "completed routes", validate with sum of rest
      let routes = 0;
      let packages = 0;
      let routeCheckSum = 0;
      let foundFirstRoutes = false;
      let foundFirstPackages = false;
      
      for (let i = 0; i < lines.length; i++) {
        if (lines[i].toLowerCase().includes('completed routes')) {
          const prevLine = lines[i - 1]?.trim();
          if (prevLine && /^[\d,]+$/.test(prevLine)) {
            const num = parseInt(prevLine.replace(/,/g, ''));
            if (!foundFirstRoutes) {
              // First instance = total routes
              routes = num;
              foundFirstRoutes = true;
            } else {
              // Subsequent instances = sum for validation check
              routeCheckSum += num;
            }
          }
        }
        // Get number preceding FIRST "Delivered Packages"
        if (!foundFirstPackages && lines[i].toLowerCase().includes('delivered packages')) {
          const prevLine = lines[i - 1]?.trim();
          if (prevLine && /^[\d,]+$/.test(prevLine)) {
            packages = parseInt(prevLine.replace(/,/g, ''));
            foundFirstPackages = true;
          }
        }
      }
      
      setWstRoutes(routes.toString());
      setWstPackages(packages.toString());
      const checkMsg = routeCheckSum > 0 ? ` (validation: ${routeCheckSum})` : '';
      logAction(`WST parsed: ${routes} total routes${checkMsg}, ${packages} packages`);
    }
  };

  const validateData = async () => {
    const cr = parseInt(cortexRoutes) || 0;
    const cp = parseInt(cortexPackages.replace(/,/g, '')) || 0;
    const wr = parseInt(wstRoutes) || 0;
    const wp = parseInt(wstPackages.replace(/,/g, '')) || 0;

    try {
      const response = await fetch(`${API_URL}/audit/screenshot/validate-metrics`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          cortex_routes: cr,
          cortex_packages: cp,
          wst_routes: wr,
          wst_packages: wp,
        }),
      });

      if (!response.ok) throw new Error('Validation failed');

      const result = await response.json();

      const validation: ValidationResult = {
        routesMatch: result.routes_match,
        routesDifference: result.routes_difference,
        packagesVariance: result.packages_variance,
        packagesWithinTolerance: result.packages_ok,
        issues: result.issues,
      };

      setValidationResult(validation);
      logAction(`Validated data - Routes: ${validation.routesMatch ? '✓' : '✗'}, Packages: ${validation.packagesWithinTolerance ? '✓' : '✗'}`);
      setStep('validation');
    } catch (error) {
      console.error('Validation error:', error);
      alert('Error validating data. Check console.');
    }
  };

  const markDispute = (type: 'route_mismatch' | 'package_variance') => {
    const cr = parseInt(cortexRoutes) || 0;
    const cp = parseInt(cortexPackages.replace(/,/g, '')) || 0;
    const wr = parseInt(wstRoutes) || 0;
    const wp = parseInt(wstPackages.replace(/,/g, '')) || 0;

    const value = type === 'route_mismatch' 
      ? { cortex: cr, wst: wr }
      : { cortex: cp, wst: wp };

    const dispute: DisputeRecord = {
      id: Date.now().toString(),
      date: selectedDate,
      type,
      cortexValue: value.cortex,
      wstValue: value.wst,
      notes: disputeNotes,
      resolved: false,
    };

    setDisputes([...disputes, dispute]);
    setDisputeNotes('');
    logAction(`Flagged dispute: ${type}`);
  };

  const logAction = (action: string) => {
    const timestamp = new Date().toLocaleTimeString('en-US', {
      hour12: false,
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
    });
    setAuditLog([`${timestamp} - ${action}`, ...auditLog]);
  };

  const resetForm = () => {
    setCortexRoutes('');
    setCortexPackages('');
    setWstRoutes('');
    setWstPackages('');
    setPackageStatusBreakdown({ remaining: 0, undeliverable: 0, returnedToStation: 0, pickupFailed: 0 });
    setValidationResult(null);
    setDisputes([]);
    setDisputeNotes('');
    setAuditLog([]);
    setStep('entry');
  };

  return (
    <ProtectedRoute>
      <PageHeader title="Daily Screenshot Audit" />

      <div className="p-6 max-w-6xl mx-auto">
        {/* STEP INDICATORS */}
        <div className="mb-8 flex gap-8">
          {['entry', 'validation', 'disputes'].map((s, i) => (
            <div
              key={s}
              className={`flex items-center gap-2 pb-2 border-b-4 ${
                step === s ? 'border-blue-600' : 'border-gray-200'
              }`}
            >
              <div className={`w-8 h-8 rounded-full flex items-center justify-center font-bold text-white ${
                step === s ? 'bg-blue-600' : 'bg-gray-400'
              }`}>
                {i + 1}
              </div>
              <span className={step === s ? 'font-bold' : 'text-gray-600'}>
                {s === 'entry' ? 'Data Entry' : s === 'validation' ? 'Validation' : 'Disputes'}
              </span>
            </div>
          ))}
        </div>

        {/* STEP 1: DATA ENTRY */}
        {step === 'entry' && (
          <div className="space-y-6">
            <div>
              <label className="block text-sm font-semibold mb-2">Date</label>
              <input
                type="date"
                value={selectedDate}
                onChange={(e) => setSelectedDate(e.target.value)}
                className="w-full px-4 py-2 border rounded-lg"
              />
            </div>

            <div className="grid grid-cols-2 gap-6">
              {/* CORTEX INPUTS */}
              <div className="border rounded-lg p-6 bg-blue-50">
                <h3 className="text-lg font-bold mb-4">Cortex</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-semibold mb-1">Paste Cortex Data</label>
                    <textarea
                      placeholder="Paste your Cortex screenshot text here (routes & X/Y deliveries)"
                      onBlur={(e) => {
                        if (e.target.value.trim()) {
                          parseText(e.target.value, 'cortex');
                          e.target.value = '';
                        }
                      }}
                      className="w-full px-4 py-2 border rounded-lg h-24 text-sm font-mono"
                    />
                  </div>
                  <div className="space-y-2 pt-2 border-t">
                    <label className="block text-xs font-semibold text-gray-600">Extracted Values</label>
                    <div>
                      <span className="text-xs text-gray-600">Routes: </span>
                      <input
                        type="number"
                        placeholder="auto-extracted"
                        value={cortexRoutes}
                        onChange={(e) => setCortexRoutes(e.target.value)}
                        className="w-full px-3 py-1 border rounded bg-white text-sm"
                      />
                    </div>
                    <div>
                      <span className="text-xs text-gray-600">Packages: </span>
                      <input
                        type="text"
                        placeholder="auto-extracted"
                        value={cortexPackages}
                        onChange={(e) => setCortexPackages(e.target.value)}
                        className="w-full px-3 py-1 border rounded bg-white text-sm"
                      />
                    </div>
                  </div>
                </div>
              </div>

              {/* WST INPUTS */}
              <div className="border rounded-lg p-6 bg-green-50">
                <h3 className="text-lg font-bold mb-4">WST</h3>
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-semibold mb-1">Paste WST Data</label>
                    <textarea
                      placeholder="Paste your WST screenshot text here (routes & delivered packages)"
                      onBlur={(e) => {
                        if (e.target.value.trim()) {
                          parseText(e.target.value, 'wst');
                          e.target.value = '';
                        }
                      }}
                      className="w-full px-4 py-2 border rounded-lg h-24 text-sm font-mono"
                    />
                  </div>
                  <div className="space-y-2 pt-2 border-t">
                    <label className="block text-xs font-semibold text-gray-600">Extracted Values</label>
                    <div>
                      <span className="text-xs text-gray-600">Routes: </span>
                      <input
                        type="number"
                        placeholder="auto-extracted"
                        value={wstRoutes}
                        onChange={(e) => setWstRoutes(e.target.value)}
                        className="w-full px-3 py-1 border rounded bg-white text-sm"
                      />
                    </div>
                    <div>
                      <span className="text-xs text-gray-600">Packages: </span>
                      <input
                        type="text"
                        placeholder="auto-extracted"
                        value={wstPackages}
                        onChange={(e) => setWstPackages(e.target.value)}
                        className="w-full px-3 py-1 border rounded bg-white text-sm"
                      />
                    </div>
                  </div>
                </div>
              </div>
            </div>

            {/* SUMMARY MATRIX */}
            {(cortexRoutes || wstRoutes) && (
              <div className="border rounded-lg p-6 bg-gray-50">
                <h3 className="text-lg font-bold mb-4">📊 Extracted Data Summary</h3>
                
                {/* Routes Comparison */}
                <div className="mb-6">
                  <h4 className="font-semibold text-sm mb-2 text-gray-700">Routes</h4>
                  <table className="w-full text-sm border-collapse">
                    <tbody>
                      <tr className="border-b">
                        <td className="font-semibold py-2 px-3">Cortex</td>
                        <td className="text-right py-2 px-3">{cortexRoutes || '—'}</td>
                      </tr>
                      <tr className="border-b">
                        <td className="font-semibold py-2 px-3">WST</td>
                        <td className="text-right py-2 px-3">{wstRoutes || '—'}</td>
                      </tr>
                      <tr className={cortexRoutes && wstRoutes && parseInt(cortexRoutes) === parseInt(wstRoutes) ? 'bg-green-100' : 'bg-red-100'}>
                        <td className="font-semibold py-2 px-3">Variance</td>
                        <td className="text-right py-2 px-3 font-semibold">
                          {cortexRoutes && wstRoutes ? Math.abs(parseInt(cortexRoutes) - parseInt(wstRoutes)) : '—'}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>

                {/* Package Status Breakdown */}
                <div className="mb-6 p-4 bg-yellow-50 border border-yellow-200 rounded">
                  <h4 className="font-semibold text-sm mb-2 text-yellow-900">Returned Packages (Subtracted from Cortex Total)</h4>
                  <p className="text-xs text-gray-600 mb-2">These items were extracted from Cortex &quot;Package Status&quot; section and subtracted from raw delivery total</p>
                  <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="flex justify-between p-1"><span>Remaining:</span><span className="font-mono">{packageStatusBreakdown.remaining || '—'}</span></div>
                    <div className="flex justify-between p-1"><span>Undeliverable:</span><span className="font-mono">{packageStatusBreakdown.undeliverable || '—'}</span></div>
                    <div className="flex justify-between p-1"><span>Returned to Station:</span><span className="font-mono">{packageStatusBreakdown.returnedToStation || '—'}</span></div>
                    <div className="flex justify-between p-1"><span>Pickup Failed:</span><span className="font-mono">{packageStatusBreakdown.pickupFailed || '—'}</span></div>
                  </div>
                  <div className="flex justify-between p-2 mt-2 border-t">
                    <span className="font-semibold">Total Subtracted:</span>
                    <span className="font-mono font-semibold">{packageStatusBreakdown.remaining + packageStatusBreakdown.undeliverable + packageStatusBreakdown.returnedToStation + packageStatusBreakdown.pickupFailed || '—'}</span>
                  </div>
                </div>

                {/* Delivered Packages Comparison */}
                <div>
                  <h4 className="font-semibold text-sm mb-2 text-gray-700">Delivered Packages</h4>
                  <table className="w-full text-sm border-collapse">
                    <tbody>
                      <tr className="border-b">
                        <td className="font-semibold py-2 px-3">Cortex (Adjusted)</td>
                        <td className="text-right py-2 px-3">{cortexPackages || '—'}</td>
                      </tr>
                      <tr className="border-b">
                        <td className="font-semibold py-2 px-3">WST</td>
                        <td className="text-right py-2 px-3">{wstPackages || '—'}</td>
                      </tr>
                      <tr className={cortexPackages && wstPackages && Math.abs(parseInt(cortexPackages) - parseInt(wstPackages)) <= 100 ? 'bg-green-100' : 'bg-red-100'}>
                        <td className="font-semibold py-2 px-3">Variance</td>
                        <td className="text-right py-2 px-3 font-semibold">
                          {cortexPackages && wstPackages ? Math.abs(parseInt(cortexPackages) - parseInt(wstPackages)) : '—'}
                        </td>
                      </tr>
                    </tbody>
                  </table>
                </div>
              </div>
            )}

            <div className="flex gap-4 justify-end">
              <button
                onClick={resetForm}
                className="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Clear
              </button>
              <button
                onClick={validateData}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-semibold"
              >
                Validate & Continue
              </button>
            </div>
          </div>
        )}

        {/* STEP 2: VALIDATION */}
        {step === 'validation' && validationResult && (
          <div className="space-y-6">
            <div className="grid grid-cols-2 gap-6">
              {/* RESULTS */}
              <div className="space-y-4">
                <div className="border rounded-lg p-6">
                  <h3 className="text-lg font-bold mb-4">📊 Validation Results</h3>
                  
                  <div className={`p-4 rounded-lg mb-4 ${validationResult.routesMatch ? 'bg-green-100 border border-green-400' : 'bg-red-100 border border-red-400'}`}>
                    <div className="font-semibold mb-1">Routes Match</div>
                    <div className="text-sm">
                      {validationResult.routesMatch ? '✓ Cortex and WST routes match' : `✗ Difference: ${validationResult.routesDifference}`}
                    </div>
                  </div>

                  <div className={`p-4 rounded-lg ${validationResult.packagesWithinTolerance ? 'bg-green-100 border border-green-400' : 'bg-red-100 border border-red-400'}`}>
                    <div className="font-semibold mb-1">Package Variance</div>
                    <div className="text-sm">
                      {validationResult.packagesVariance.toFixed(0)} packages
                      {validationResult.packagesWithinTolerance ? ' ✓ Within 25 package tolerance' : ' ✗ Exceeds 25 package tolerance'}
                    </div>
                  </div>
                </div>

                {validationResult.issues.length > 0 && (
                  <div className="border border-yellow-400 rounded-lg p-4 bg-yellow-50">
                    <h4 className="font-semibold mb-2 text-yellow-900">⚠️ Issues Found</h4>
                    <ul className="space-y-1 text-sm text-yellow-900">
                      {validationResult.issues.map((issue, i) => (
                        <li key={i}>• {issue}</li>
                      ))}
                    </ul>
                  </div>
                )}
              </div>

              {/* SUMMARY TABLE */}
              <div className="border rounded-lg p-6">
                <h3 className="text-lg font-bold mb-4">📋 Metrics Summary</h3>
                <table className="w-full text-sm">
                  <tbody>
                    <tr className="border-b">
                      <td className="font-semibold py-2">Routes (Cortex)</td>
                      <td className="text-right">{cortexRoutes || '—'}</td>
                    </tr>
                    <tr className="border-b">
                      <td className="font-semibold py-2">Routes (WST)</td>
                      <td className="text-right">{wstRoutes || '—'}</td>
                    </tr>
                    <tr className="border-b bg-gray-50">
                      <td className="font-semibold py-2">Match</td>
                      <td className="text-right font-semibold">{validationResult.routesMatch ? '✓' : '✗'}</td>
                    </tr>
                    <tr className="border-b">
                      <td className="font-semibold py-2">Packages (Cortex)</td>
                      <td className="text-right">{cortexPackages || '—'}</td>
                    </tr>
                    <tr className="border-b">
                      <td className="font-semibold py-2">Packages (WST)</td>
                      <td className="text-right">{wstPackages || '—'}</td>
                    </tr>
                    <tr className="bg-gray-50">
                      <td className="font-semibold py-2">Variance</td>
                      <td className="text-right font-semibold">{validationResult.packagesVariance.toFixed(0)} packages</td>
                    </tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div className="flex gap-4 justify-end">
              <button
                onClick={() => setStep('entry')}
                className="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Back
              </button>
              <button
                onClick={() => {
                  logAction('Processed validation, moving to disputes');
                  setStep('disputes');
                }}
                className="px-6 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 font-semibold"
              >
                Continue to Disputes
              </button>
            </div>
          </div>
        )}

        {/* STEP 3: DISPUTES */}
        {step === 'disputes' && (
          <div className="space-y-6">
            <div className="grid grid-cols-3 gap-6">
              {/* DISPUTE ENTRY */}
              <div className="col-span-2 border rounded-lg p-6">
                <h3 className="text-lg font-bold mb-4">🚩 Flag Disputes</h3>

                {validationResult && !validationResult.routesMatch && (
                  <div className="mb-4 p-4 border border-red-300 rounded-lg bg-red-50">
                    <h4 className="font-semibold text-red-900 mb-2">Routes Mismatch</h4>
                    <p className="text-sm text-red-800 mb-3">
                      Cortex: {cortexRoutes} | WST: {wstRoutes} | Difference: {validationResult.routesDifference}
                    </p>
                    <textarea
                      placeholder="Add notes about this discrepancy..."
                      value={disputes.some(d => d.type === 'route_mismatch') ? '' : disputeNotes}
                      onChange={(e) => setDisputeNotes(e.target.value)}
                      className="w-full px-3 py-2 border rounded mb-2 text-sm"
                      rows={2}
                    />
                    <button
                      onClick={() => markDispute('route_mismatch')}
                      disabled={disputes.some(d => d.type === 'route_mismatch')}
                      className="w-full px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-gray-400 text-sm font-semibold"
                    >
                      {disputes.some(d => d.type === 'route_mismatch') ? '✓ Flagged' : 'Flag Dispute'}
                    </button>
                  </div>
                )}

                {validationResult && !validationResult.packagesWithinTolerance && (
                  <div className="p-4 border border-red-300 rounded-lg bg-red-50">
                    <h4 className="font-semibold text-red-900 mb-2">Package Variance</h4>
                    <p className="text-sm text-red-800 mb-3">
                      Cortex: {cortexPackages} | WST: {wstPackages} | Variance: {validationResult.packagesVariance.toFixed(0)} packages
                    </p>
                    <textarea
                      placeholder="Add notes about this discrepancy..."
                      value={disputes.some(d => d.type === 'package_variance') ? '' : disputeNotes}
                      onChange={(e) => setDisputeNotes(e.target.value)}
                      className="w-full px-3 py-2 border rounded mb-2 text-sm"
                      rows={2}
                    />
                    <button
                      onClick={() => markDispute('package_variance')}
                      disabled={disputes.some(d => d.type === 'package_variance')}
                      className="w-full px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-gray-400 text-sm font-semibold"
                    >
                      {disputes.some(d => d.type === 'package_variance') ? '✓ Flagged' : 'Flag Dispute'}
                    </button>
                  </div>
                )}

                {validationResult && validationResult.routesMatch && validationResult.packagesWithinTolerance && (
                  <div className="p-4 bg-green-50 border border-green-300 rounded-lg">
                    <p className="text-green-900 font-semibold">✓ All metrics pass validation!</p>
                  </div>
                )}
              </div>

              {/* AUDIT LOG */}
              <div className="border rounded-lg p-6 bg-gray-50">
                <h3 className="text-lg font-bold mb-4">📝 Audit Log</h3>
                <div className="space-y-2 text-sm max-h-96 overflow-y-auto">
                  {auditLog.length === 0 ? (
                    <p className="text-gray-500">No actions logged yet</p>
                  ) : (
                    auditLog.map((log, i) => (
                      <div key={i} className="py-1 border-b text-gray-700 font-mono">
                        {log}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>

            {/* DISPUTES SUMMARY */}
            {disputes.length > 0 && (
              <div className="border rounded-lg p-6 bg-yellow-50 border-yellow-300">
                <h3 className="text-lg font-bold mb-4">📋 Disputed Items ({disputes.length})</h3>
                <div className="space-y-3">
                  {disputes.map((dispute) => (
                    <div key={dispute.id} className="border rounded p-3 bg-white">
                      <div className="flex justify-between items-start mb-2">
                        <div>
                          <span className="font-semibold text-yellow-900">
                            {dispute.type === 'route_mismatch' ? '🚩 Route Mismatch' : '🚩 Package Variance'}
                          </span>
                          <span className="text-sm text-gray-600 ml-2">Date: {dispute.date}</span>
                        </div>
                      </div>
                      <p className="text-sm mb-2">
                        Cortex: {dispute.cortexValue} | WST: {dispute.wstValue}
                      </p>
                      {dispute.notes && (
                        <p className="text-sm text-gray-700 italic">Notes: {dispute.notes}</p>
                      )}
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="flex gap-4 justify-end">
              <button
                onClick={() => setStep('validation')}
                className="px-6 py-2 border border-gray-300 rounded-lg hover:bg-gray-50"
              >
                Back
              </button>
              <button
                onClick={() => {
                  logAction('Audit complete - submitted for review');
                  alert(`✓ Audit submitted!\n\nFound ${disputes.length} dispute(s)\nDate: ${selectedDate}`);
                  resetForm();
                }}
                className="px-6 py-2 bg-green-600 text-white rounded-lg hover:bg-green-700 font-semibold"
              >
                Submit Audit
              </button>
            </div>
          </div>
        )}
      </div>
    </ProtectedRoute>
  );
}
