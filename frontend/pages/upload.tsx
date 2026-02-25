'use client';

import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/router';
import UploadZone from '../components/UploadZone';
import StatusDisplay from '../components/StatusDisplay';
import PageHeader from '../components/PageHeader';
import CapacityAlert from '../components/CapacityAlert';
import ElectricVanViolationsAlert from '../components/ElectricVanViolationsAlert';
import ManualVehicleAssignment from '../components/ManualVehicleAssignment';
import { ProtectedRoute } from '../components/ProtectedRoute';

interface IngestStatus {
  dop_uploaded: boolean;
  fleet_uploaded: boolean;
  cortex_uploaded: boolean;
  route_sheets_uploaded: boolean;
  dop_record_count: number;
  fleet_record_count: number;
  cortex_record_count: number;
  route_sheets_count: number;
  assignments_count: number;
  validation_errors: string[];
  validation_warnings: string[];
  // Financial Data
  variable_invoice_uploaded?: boolean;
  fleet_invoice_uploaded?: boolean;
  weekly_incentive_uploaded?: boolean;
  dsp_scorecard_uploaded?: boolean;
  pod_report_uploaded?: boolean;
  // Performance Data
  wst_zip_uploaded?: boolean;
}

interface StatusMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function Upload() {
  const router = useRouter();
  const [userRole, setUserRole] = useState<string | null>(null);
  const [view, setView] = useState<'daily' | 'financial' | 'performance'>('daily');
  const showDaily = view === 'daily';
  const showFinancial = view === 'financial';
  const showPerformance = view === 'performance';
  const pageTitle =
    view === 'financial'
      ? 'Financial Data Uploads'
      : view === 'performance'
        ? 'Performance & Delivery Data'
        : 'Daily Driver Assignment';
  const [status, setStatus] = useState<IngestStatus>({
    dop_uploaded: false,
    fleet_uploaded: false,
    cortex_uploaded: false,
    route_sheets_uploaded: false,
    dop_record_count: 0,
    fleet_record_count: 0,
    cortex_record_count: 0,
    route_sheets_count: 0,
    assignments_count: 0,
    validation_errors: [],
    validation_warnings: [],
    variable_invoice_uploaded: false,
    fleet_invoice_uploaded: false,
    weekly_incentive_uploaded: false,
    dsp_scorecard_uploaded: false,
    pod_report_uploaded: false,
    wst_zip_uploaded: false,
  });

  const [messages, setMessages] = useState<StatusMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [pdfGenerated, setPdfGenerated] = useState(false);
  const [violationsRefreshTrigger, setViolationsRefreshTrigger] = useState(0);
  const [failedRoutes, setFailedRoutes] = useState<any[]>([]);
  const [showManualAssignment, setShowManualAssignment] = useState(false);

  // Extract user role from JWT token
  useEffect(() => {
    try {
      const token = localStorage.getItem('access_token');
      if (token) {
        // Decode JWT manually (split by '.' and decode payload)
        const parts = token.split('.');
        if (parts.length === 3) {
          const payload = JSON.parse(atob(parts[1]));
          setUserRole(payload.role);
        }
      }
    } catch (error) {
      console.error('Failed to extract role from token:', error);
    }
  }, []);

  // Handle scroll to section when hash is present
  useEffect(() => {
    if (router.asPath.includes('#')) {
      const hash = router.asPath.split('#')[1];
      setTimeout(() => {
        const element = document.getElementById(hash);
        if (element) {
          element.scrollIntoView({ behavior: 'smooth' });
        }
      }, 500);
    }
  }, [router.asPath]);

  // Sync view with URL query
  useEffect(() => {
    let nextView: string | null = null;

    if (typeof router.query.view === 'string') {
      nextView = router.query.view;
    } else if (router.asPath.includes('?')) {
      const queryString = router.asPath.split('?')[1].split('#')[0];
      const params = new URLSearchParams(queryString);
      nextView = params.get('view');
    }

    if (nextView && ['daily', 'financial', 'performance'].includes(nextView)) {
      setView(nextView as 'daily' | 'financial' | 'performance');
    } else {
      setView('daily');
    }
  }, [router.asPath, router.query.view]);

  const showMessage = (type: 'success' | 'error' | 'info' | 'warning', text: string) => {
    const msg: StatusMessage = { type, text };
    setMessages([msg]);
  };

  const handleUpload = useCallback(
    async (endpoint: string, files: File[]) => {
      setIsLoading(true);
      try {
        const formData = new FormData();
        // Route-sheets endpoint uses "files" (plural) for List[UploadFile]
        // Other endpoints use "file" (singular) for single UploadFile
        const fieldName = endpoint === '/route-sheets' ? 'files' : 'file';
        files.forEach((file) => formData.append(fieldName, file));

        const token = localStorage.getItem('access_token');
        const response = await fetch(`${API_URL}/upload${endpoint}`, {
          method: 'POST',
          body: formData,
          headers: {
            ...(token && { 'Authorization': `Bearer ${token}` }),
          },
        });

        if (!response.ok) {
          throw new Error(`Upload failed: ${response.status}`);
        }

        const result = await response.json();

        // Fetch updated status
        const statusResponse = await fetch(`${API_URL}/upload/status`);
        const statusData = await statusResponse.json();
        setStatus(statusData);

        showMessage('success', `${endpoint.slice(1)} uploaded: ${result.records_parsed} records`);
      } catch (error) {
        showMessage('error', `Upload failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
      } finally {
        setIsLoading(false);
      }
    },
    []
  );

  const handleAssignVehicles = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
      const response = await fetch(`${API_URL}/upload/assign-vehicles`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        throw new Error(`Assignment failed: ${response.status}`);
      }

      const result = await fetch(`${API_URL}/upload/assign-vehicles`, {
        method: 'POST',
        headers,
      }).then(r => r.json());

      // Check if there are failed routes requiring manual assignment
      if (result.failed && result.failed > 0 && result.failed_routes_detail) {
        showMessage(
          'warning',
          `${result.assigned}/${result.total_routes} routes assigned. ${result.failed} routes require manual vehicle selection.`
        );
        setFailedRoutes(result.failed_routes_detail);
        setShowManualAssignment(true);
      } else if (result.success === false) {
        showMessage('error', result.message || 'Assignment failed');
      } else {
        showMessage(
          'success',
          `Successfully assigned all ${result.assigned}/${result.total_routes} routes (${result.success_rate}%)`
        );

        // Fetch updated status
        const statusResponse = await fetch(`${API_URL}/upload/status`, {
          headers,
        });
        const statusData = await statusResponse.json();
        setStatus(statusData);
        
        // Trigger violations refresh
        setViolationsRefreshTrigger(prev => prev + 1);
        
        // Fetch and store assignment details in sessionStorage for database view
        try {
          const detailsResponse = await fetch(`${API_URL}/upload/assignments`, {
            headers,
          });
          const detailsData = await detailsResponse.json();
          if (detailsData.assignments) {
            sessionStorage.setItem('assignments', JSON.stringify(detailsData.assignments));
          }
        } catch (e) {
          console.error('Failed to fetch assignment details:', e);
        }
      }
    } catch (error) {
      showMessage('error', `Assignment failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleAssignmentComplete = async () => {
    // After manual assignment is complete, try to generate handouts
    setShowManualAssignment(false);
    await handleGenerateHandouts();
  };

  const handleGenerateHandouts = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {};
      const response = await fetch(`${API_URL}/upload/generate-handouts`, {
        method: 'POST',
        headers,
      });

      if (!response.ok) {
        throw new Error(`PDF generation failed: ${response.status}`);
      }

      const result = await response.json();

      if (result.success) {
        showMessage('success', `Driver handouts generated: ${result.cards_generated} cards`);
        setPdfGenerated(true);
        // Trigger automatic download
        setTimeout(() => {
          const downloadLink = document.createElement('a');
          downloadLink.href = `${API_URL}/upload/download-handouts`;
          downloadLink.download = 'NDAY_Driver_Handouts.pdf';
          document.body.appendChild(downloadLink);
          downloadLink.click();
          document.body.removeChild(downloadLink);
        }, 500);
      } else {
        showMessage('error', result.message || 'PDF generation failed');
      }
    } catch (error) {
      showMessage('error', `PDF generation failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-white">
      {/* Manual Vehicle Assignment Dialog */}
      {showManualAssignment && (
        <ManualVehicleAssignment
          failedRoutes={failedRoutes}
          apiUrl={API_URL}
          onAssignmentComplete={handleAssignmentComplete}
          onClose={() => setShowManualAssignment(false)}
        />
      )}

      {/* Header */}
      <PageHeader title={pageTitle} showBack={true} />

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-4 py-8">
        {/* Messages */}
        <StatusDisplay messages={messages} isLoading={isLoading} />

        {showDaily && (
          <>
            {/* Capacity Alerts */}
            <div className="mt-6">
              <CapacityAlert apiUrl={API_URL} />
            </div>

            {/* Electric Van Violations */}
            <div className="mt-6">
              <ElectricVanViolationsAlert refreshTrigger={violationsRefreshTrigger} />
            </div>
          </>
        )}

        {/* Upload Section */}
        <div className="mt-8">
          <h2 className="text-2xl font-bold text-ndl-blue mb-6">{pageTitle}</h2>

          {showDaily && (
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
              {/* DOP Upload */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  DOP (Day of Plan)
                </label>
                <UploadZone
                  label="DOP Excel/CSV"
                  accept=".xlsx,.xls,.csv"
                  onDrop={(files) => handleUpload('/dop', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Route Plan</p>
                    <p className="text-sm text-gray-600">Daily route assignments</p>
                  </div>
                </UploadZone>
                {status.dop_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ {status.dop_record_count} routes</p>
                )}
              </div>

              {/* Fleet Upload */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Fleet
                </label>
                <UploadZone
                  label="Fleet Excel/CSV"
                  accept=".xlsx,.xls,.csv"
                  onDrop={(files) => handleUpload('/fleet', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Vehicle Inventory</p>
                    <p className="text-sm text-gray-600">Available vehicles</p>
                  </div>
                </UploadZone>
                {status.fleet_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ {status.fleet_record_count} vehicles</p>
                )}
              </div>

              {/* Cortex Upload */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Cortex
                </label>
                <UploadZone
                  label="Cortex Excel/CSV"
                  accept=".xlsx,.xls,.csv"
                  onDrop={(files) => handleUpload('/cortex', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Driver Assignments</p>
                    <p className="text-sm text-gray-600">Driver route assignments</p>
                  </div>
                </UploadZone>
                {status.cortex_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ {status.cortex_record_count} assignments</p>
                )}
              </div>

              {/* Route Sheets Upload */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  Route Sheets
                </label>
                <UploadZone
                  label="Route Sheet PDFs"
                  accept=".pdf"
                  multiple
                  onDrop={(files) => handleUpload('/route-sheets', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Load Manifest</p>
                    <p className="text-sm text-gray-600">Package load details</p>
                  </div>
                </UploadZone>
                {status.route_sheets_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ {status.route_sheets_count} schedules</p>
                )}
              </div>
            </div>
          )}

          {/* Financial Data Section - Admin/Manager Only */}
          {showFinancial && (
            <div id="financial-section" className="mt-12 mb-8 scroll-mt-20">
              <h3 className="text-xl font-bold text-gray-800 mb-4 flex items-center gap-2">
                <span className="text-2xl">💰</span> Financial Data
              </h3>
              {(userRole === 'admin' || userRole === 'manager') ? (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
                  {/* Variable Invoice */}
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">
                      Variable Invoice
                    </label>
                    <UploadZone
                      label="Variable Invoice PDF"
                      accept=".pdf"
                      onDrop={(files) => handleUpload('/variable-invoice', files)}
                    >
                      <div className="py-8">
                        <p className="text-lg font-semibold text-ndl-blue">Variable Costs</p>
                        <p className="text-sm text-gray-600">Mileage & expense invoices</p>
                      </div>
                    </UploadZone>
                    {status.variable_invoice_uploaded && (
                      <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                    )}
                  </div>

                  {/* Fleet Invoice */}
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">
                      Fleet Invoice
                    </label>
                    <UploadZone
                      label="Fleet Invoice PDF"
                      accept=".pdf"
                      onDrop={(files) => handleUpload('/fleet-invoice', files)}
                    >
                      <div className="py-8">
                        <p className="text-lg font-semibold text-ndl-blue">Fleet Costs</p>
                        <p className="text-sm text-gray-600">Vehicle maintenance & fuel</p>
                      </div>
                    </UploadZone>
                    {status.fleet_invoice_uploaded && (
                      <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                    )}
                  </div>

                  {/* Weekly Incentive */}
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-2">
                      Weekly Incentive
                    </label>
                    <UploadZone
                      label="Weekly Incentive PDF"
                      accept=".pdf"
                      onDrop={(files) => handleUpload('/weekly-incentive', files)}
                    >
                      <div className="py-8">
                        <p className="text-lg font-semibold text-ndl-blue">Driver Incentives</p>
                        <p className="text-sm text-gray-600">Weekly bonus structure</p>
                      </div>
                    </UploadZone>
                    {status.weekly_incentive_uploaded && (
                      <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                    )}
                  </div>
                </div>
              ) : (
                <div className="bg-yellow-50 border border-yellow-200 text-yellow-800 rounded-lg p-4">
                  Financial data access is restricted to Admin and Manager roles.
                </div>
              )}
            </div>
          )}

          {/* Performance Data Section */}
          {showPerformance && (
          <div id="performance-section" className="mt-12 mb-8 scroll-mt-20">
            <h3 className="text-xl font-bold text-gray-800 mb-4 flex items-center gap-2">
              <span className="text-2xl">📊</span> Performance Data
            </h3>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {/* WST ZIP */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  WST Package
                </label>
                <UploadZone
                  label="WST ZIP File"
                  accept=".zip"
                  onDrop={(files) => handleUpload('/wst-zip', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Weekly Performance Data</p>
                    <p className="text-sm text-gray-600">Contains: Delivered Packages, Service Details, Training, Unplanned Delays, Weekly Report</p>
                  </div>
                </UploadZone>
                {status.wst_zip_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                )}
              </div>

              {/* DSP Scorecard */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  DSP Scorecard
                </label>
                <UploadZone
                  label="DSP Scorecard PDF"
                  accept=".pdf"
                  onDrop={(files) => handleUpload('/dsp-scorecard', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Performance Metrics</p>
                    <p className="text-sm text-gray-600">Delivery service partner scores</p>
                  </div>
                </UploadZone>
                {status.dsp_scorecard_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                )}
              </div>

              {/* POD Report */}
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">
                  POD Report
                </label>
                <UploadZone
                  label="POD Report PDF"
                  accept=".pdf"
                  onDrop={(files) => handleUpload('/pod-report', files)}
                >
                  <div className="py-8">
                    <p className="text-lg font-semibold text-ndl-blue">Proof of Delivery</p>
                    <p className="text-sm text-gray-600">Delivery confirmation records</p>
                  </div>
                </UploadZone>
                {status.pod_report_uploaded && (
                  <p className="mt-2 text-sm text-green-600">✓ Uploaded</p>
                )}
              </div>
            </div>
          </div>
          )}

          {/* Status Display */}
          {showDaily && (
            <StatusDisplay
              dop_uploaded={status.dop_uploaded}
              fleet_uploaded={status.fleet_uploaded}
              cortex_uploaded={status.cortex_uploaded}
              route_sheets_uploaded={status.route_sheets_uploaded}
              dop_record_count={status.dop_record_count}
              fleet_record_count={status.fleet_record_count}
              cortex_record_count={status.cortex_record_count}
              route_sheets_count={status.route_sheets_count}
              assignments_count={status.assignments_count}
              validation_errors={status.validation_errors}
              validation_warnings={status.validation_warnings}
            />
          )}
        </div>

        {/* Actions Section */}
        {showDaily && (
          <div className="mt-10 flex gap-4 flex-wrap">
            <button
              onClick={handleAssignVehicles}
              disabled={!status.dop_uploaded || !status.fleet_uploaded || isLoading}
              className="btn-primary"
            >
              Assign Vehicles
            </button>
            <button
              onClick={handleGenerateHandouts}
              disabled={status.assignments_count === 0 || !status.route_sheets_uploaded || isLoading}
              className="btn-primary"
            >
              Generate Driver Handouts
            </button>
            {pdfGenerated && (
              <a
                href={`${API_URL}/upload/download-handouts`}
                download="NDAY_Driver_Handouts.pdf"
                className="btn-primary bg-green-600 hover:bg-green-700"
                target="_blank"
                rel="noopener noreferrer"
              >
                Download PDF
              </a>
            )}
            <button
              onClick={() => {
                setStatus({
                  dop_uploaded: false,
                  fleet_uploaded: false,
                  cortex_uploaded: false,
                  route_sheets_uploaded: false,
                  dop_record_count: 0,
                  fleet_record_count: 0,
                  cortex_record_count: 0,
                  route_sheets_count: 0,
                  assignments_count: 0,
                  validation_errors: [],
                  validation_warnings: [],
                  variable_invoice_uploaded: false,
                  fleet_invoice_uploaded: false,
                  weekly_incentive_uploaded: false,
                  dsp_scorecard_uploaded: false,
                  pod_report_uploaded: false,
                  wst_zip_uploaded: false,
                });
                setPdfGenerated(false);
                setMessages([]);
                showMessage('info', 'Form reset');
              }}
              className="btn-secondary"
            >
              Reset
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="bg-gray-100 border-t border-gray-300 mt-12 py-4">
        <div className="max-w-6xl mx-auto px-4 text-center text-sm text-gray-600">
          <p>NDAY Route Manager © 2026. All rights reserved.</p>
        </div>
      </footer>
      </div>
    </ProtectedRoute>
  );
}
