'use client';

import { useState, useCallback, useEffect } from 'react';
import { useRouter } from 'next/router';
import Link from 'next/link';
import UploadZone from '../components/UploadZone';
import StatusDisplay from '../components/StatusDisplay';
import PageHeader from '../components/PageHeader';
import CapacityAlert from '../components/CapacityAlert';
import ElectricVanViolationsAlert from '../components/ElectricVanViolationsAlert';
import ManualVehicleAssignment from '../components/ManualVehicleAssignment';
import PrimaryDriverSelection from '../components/PrimaryDriverSelection';
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
  dsp_scorecard_uploaded?: boolean;
  pod_report_uploaded?: boolean;
  // Performance Data
  wst_zip_uploaded?: boolean;
}

interface StatusMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  text: string;
}

interface AssignmentDetails {
  route_code: string;
  driver_name: string;
  wave_time?: string;
  vehicle_name?: string;
}

interface DailyComparisonResult {
  comparison_date: string;
  week_number: number;
  station?: string | null;
  counts: {
    cortex_routes: number;
    invoice_routes?: number | null;
    dop_routes: number;
    wst_service_details: number;
    wst_delivered_packages: number;
    wst_training_weekly: number;
    wst_unplanned_delay: number;
    wst_weekly_report: number;
    wst_total: number;
    wst_total_all_tables?: number;
  };
  route_alignment?: {
    invoice_number?: string | null;
    cortex_vs_wst_delta: number;
    cortex_vs_invoice_delta?: number | null;
    aligned_cortex_to_wst: boolean;
    aligned_cortex_to_invoice?: boolean | null;
  };
  route_payment_audit?: {
    comparison_date: string;
    station?: string | null;
    invoice_number?: string | null;
    routes_completed_cortex: number;
    routes_paid_invoice?: number | null;
    routes_seen_wst: number;
    routes_planned_dop: number;
    routes_not_in_dop: number;
    pending_confirmation: number;
    confirmed_valid: number;
    excluded_missort: number;
    effective_cortex_routes: number;
    effective_cortex_vs_invoice_delta?: number | null;
    requires_user_confirmation?: boolean;
    prompt_message?: string | null;
    prompt_items: Array<{
      route_code: string;
      action_status: 'pending' | 'confirm_valid_route' | 'exclude_missort';
      manager_note?: string | null;
      reviewed_role?: string | null;
      reviewed_at?: string | null;
      required: boolean;
      prompt: string;
    }>;
  };
  service_type_comparison?: {
    service_type_filter: string;
    totals: {
      wst_routes: number;
      dop_routes: number;
      route_delta: number;
      wst_packages: number;
      dop_packages: number;
      package_delta: number;
    };
    line_items: Array<{
      route_code: string;
      wst: {
        service_type?: string | null;
        packages?: number | null;
        driver_name?: string | null;
      };
      dop: {
        service_type?: string | null;
        packages?: number | null;
      };
      package_delta?: number | null;
      status: string;
      issues: string[];
    }>;
  };
  aligned: boolean;
  issues: string[];
}

interface WeeklyDisputeItem {
  invoice_number: string;
  station?: string | null;
  period_start: string;
  period_end: string;
  ready_for_dispute: boolean;
  discrepancy_count: number;
  discrepancies?: Array<{
    line_description?: string;
    week_number?: number;
    issues?: string[];
  }>;
}

interface WeeklyDisputeReport {
  week_start: string;
  week_end: string;
  week_number: number;
  station?: string | null;
  only_ready: boolean;
  count: number;
  items: WeeklyDisputeItem[];
}

interface InvoiceOption {
  id: number;
  invoice_number: string;
  invoice_date?: string | null;
  station?: string | null;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8001';

const formatDateOnly = (value?: string | null): string => {
  if (!value) return 'N/A';
  const parts = value.split('-');
  if (parts.length === 3) {
    const [year, month, day] = parts;
    return `${month}/${day}/${year}`;
  }
  return value;
};

const formatApiError = async (response: Response, fallback: string): Promise<string> => {
  const statusText = response.statusText || 'Request failed';
  const prefix = `${response.status} ${statusText}`;

  try {
    const payload = await response.json();
    const detail = payload?.detail;
    const message = payload?.message;

    if (typeof detail === 'string' && detail.trim()) {
      return `${prefix}: ${detail}`;
    }

    if (Array.isArray(detail) && detail.length > 0) {
      const issues = detail
        .map((item: any) => item?.msg || item?.message || JSON.stringify(item))
        .filter(Boolean)
        .join('; ');
      if (issues) {
        return `${prefix}: ${issues}`;
      }
    }

    if (typeof message === 'string' && message.trim()) {
      return `${prefix}: ${message}`;
    }
  } catch {
    try {
      const text = await response.text();
      if (text.trim()) {
        return `${prefix}: ${text.trim()}`;
      }
    } catch {
      return `${prefix}: ${fallback}`;
    }
  }

  return `${prefix}: ${fallback}`;
};

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
  const [primaryDriverRoutes, setPrimaryDriverRoutes] = useState<AssignmentDetails[]>([]);
  const [showPrimaryDriverModal, setShowPrimaryDriverModal] = useState(false);
  const [autoGenerateAfterPrimary, setAutoGenerateAfterPrimary] = useState(false);
  const [comparisonDate, setComparisonDate] = useState<string>(new Date().toISOString().slice(0, 10));
  const [comparisonStation, setComparisonStation] = useState<string>('');
  const [comparisonInvoiceNumber, setComparisonInvoiceNumber] = useState<string>('');
  const [invoiceOptions, setInvoiceOptions] = useState<InvoiceOption[]>([]);
  const [invoiceOptionsLoading, setInvoiceOptionsLoading] = useState(false);
  const [comparisonLoading, setComparisonLoading] = useState(false);
  const [comparisonResult, setComparisonResult] = useState<DailyComparisonResult | null>(null);
  const [routeActionSaving, setRouteActionSaving] = useState<string | null>(null);
  const [weeklyDisputeLoading, setWeeklyDisputeLoading] = useState(false);
  const [weeklyDisputeReport, setWeeklyDisputeReport] = useState<WeeklyDisputeReport | null>(null);

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

  useEffect(() => {
    const loadInvoiceOptions = async () => {
      if (!(userRole === 'admin' || userRole === 'manager')) {
        return;
      }

      try {
        setInvoiceOptionsLoading(true);
        const token = localStorage.getItem('access_token');
        const response = await fetch(`${API_URL}/audit/variable-invoices`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
            ...(token && { 'Authorization': `Bearer ${token}` }),
          },
        });

        if (!response.ok) {
          throw new Error(await formatApiError(response, 'Failed to load invoice options'));
        }

        const data = await response.json();
        const invoices: InvoiceOption[] = data?.invoices || [];
        setInvoiceOptions(invoices);
        if (!comparisonInvoiceNumber && invoices.length > 0) {
          setComparisonInvoiceNumber(invoices[0].invoice_number);
        }
      } catch (error) {
        showMessage('warning', error instanceof Error ? error.message : 'Failed to load invoice list');
      } finally {
        setInvoiceOptionsLoading(false);
      }
    };

    loadInvoiceOptions();
  }, [userRole, comparisonInvoiceNumber]);

  const showMessage = (type: 'success' | 'error' | 'info' | 'warning', text: string) => {
    const msg: StatusMessage = { type, text };
    setMessages([msg]);
  };

  const hasMultipleDrivers = (driverName?: string) => {
    if (!driverName || driverName === 'N/A') return false;
    return /\||\/|&|,|;|\s+and\s+/i.test(driverName);
  };

  const openPrimaryDriverModal = (assignments: AssignmentDetails[], shouldAutoGenerate = false) => {
    const multiDriverRoutes = assignments.filter((assignment) => hasMultipleDrivers(assignment.driver_name));
    if (multiDriverRoutes.length > 0) {
      setPrimaryDriverRoutes(multiDriverRoutes);
      setShowPrimaryDriverModal(true);
      setAutoGenerateAfterPrimary(shouldAutoGenerate);
      return true;
    }
    return false;
  };

  const fetchAssignmentsAndPromptPrimary = async (headers: Record<string, string>, shouldAutoGenerate = false) => {
    try {
      const detailsResponse = await fetch(`${API_URL}/upload/assignments`, {
        headers,
      });
      const detailsData = await detailsResponse.json();
      if (detailsData.assignments) {
        sessionStorage.setItem('assignments', JSON.stringify(detailsData.assignments));
        return openPrimaryDriverModal(detailsData.assignments, shouldAutoGenerate);
      }
    } catch (e) {
      console.error('Failed to fetch assignment details:', e);
    }
    return false;
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
      const headers: Record<string, string> = token ? { 'Authorization': `Bearer ${token}` } : {};
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
        await fetchAssignmentsAndPromptPrimary(headers);
      }
    } catch (error) {
      showMessage('error', `Assignment failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleRunDailyComparison = async () => {
    if (!comparisonDate) {
      showMessage('warning', 'Please select a comparison date');
      return;
    }

    setComparisonLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const params = new URLSearchParams({ comparison_date: comparisonDate });
      if (comparisonStation.trim()) {
        params.append('station', comparisonStation.trim().toUpperCase());
      }
      if (comparisonInvoiceNumber.trim()) {
        params.append('invoice_number', comparisonInvoiceNumber.trim());
      }

      const response = await fetch(`${API_URL}/audit/wst-cortex-dop-comparison?${params.toString()}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          ...(token && { 'Authorization': `Bearer ${token}` }),
        },
      });

      if (!response.ok) {
        throw new Error(await formatApiError(response, 'Comparison failed'));
      }

      const data = await response.json();
      setComparisonResult(data);

      if (data.aligned) {
        showMessage('success', 'Daily WST vs Cortex/DOP comparison is aligned');
      } else {
        showMessage('warning', `Comparison found ${data.issues?.length || 0} issue(s) to resolve`);
      }
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to run comparison');
      setComparisonResult(null);
    } finally {
      setComparisonLoading(false);
    }
  };

  const handleCreateWeeklyDispute = async () => {
    if (!comparisonDate) {
      showMessage('warning', 'Please select a date to determine the dispute week');
      return;
    }

    setWeeklyDisputeLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const params = new URLSearchParams({ week_date: comparisonDate, only_ready: 'true' });
      if (comparisonStation.trim()) {
        params.append('station', comparisonStation.trim().toUpperCase());
      }

      const response = await fetch(`${API_URL}/audit/variable-invoices/disputes/weekly?${params.toString()}`, {
        method: 'GET',
        headers: {
          'Content-Type': 'application/json',
          ...(token && { 'Authorization': `Bearer ${token}` }),
        },
      });

      if (!response.ok) {
        throw new Error(await formatApiError(response, 'Weekly dispute creation failed'));
      }

      const data = await response.json();
      setWeeklyDisputeReport(data);
      showMessage('success', `Weekly dispute report created with ${data.count || 0} dispute item(s)`);
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to create weekly dispute report');
      setWeeklyDisputeReport(null);
    } finally {
      setWeeklyDisputeLoading(false);
    }
  };

  const handleRoutePromptAction = async (
    routeCode: string,
    actionStatus: 'pending' | 'confirm_valid_route' | 'exclude_missort'
  ) => {
    if (!comparisonResult) {
      return;
    }

    setRouteActionSaving(routeCode);
    try {
      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_URL}/audit/route-payment/review`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          ...(token && { 'Authorization': `Bearer ${token}` }),
        },
        body: JSON.stringify({
          comparison_date: comparisonResult.comparison_date,
          station: comparisonResult.station || null,
          invoice_number: comparisonResult.route_alignment?.invoice_number || comparisonInvoiceNumber || null,
          items: [
            {
              route_code: routeCode,
              action_status: actionStatus,
            },
          ],
        }),
      });

      if (!response.ok) {
        throw new Error(await formatApiError(response, 'Failed to save route confirmation action'));
      }

      showMessage('success', `Route ${routeCode} updated to ${actionStatus}`);
      await handleRunDailyComparison();
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to save route action');
    } finally {
      setRouteActionSaving(null);
    }
  };

  const handleExportWeeklyDisputeExcel = () => {
    if (!weeklyDisputeReport) {
      showMessage('warning', 'Create a weekly dispute report before exporting');
      return;
    }

    const escapeCsv = (value: string | number | null | undefined) => {
      const str = value === null || value === undefined ? '' : String(value);
      if (str.includes(',') || str.includes('"') || str.includes('\n')) {
        return `"${str.replace(/"/g, '""')}"`;
      }
      return str;
    };

    const rows: string[] = [];
    rows.push([
      'Week Start',
      'Week End',
      'Week #',
      'Invoice Number',
      'Station',
      'Invoice Period',
      'Discrepancy Count',
      'Line Description',
      'Issue Details',
    ].join(','));

    weeklyDisputeReport.items.forEach((item) => {
      const base = [
        weeklyDisputeReport.week_start,
        weeklyDisputeReport.week_end,
        weeklyDisputeReport.week_number,
        item.invoice_number,
        item.station || '',
        `${item.period_start} - ${item.period_end}`,
        item.discrepancy_count,
      ];

      if (item.discrepancies && item.discrepancies.length > 0) {
        item.discrepancies.forEach((discrepancy) => {
          rows.push([
            ...base.map(escapeCsv),
            escapeCsv(discrepancy.line_description || ''),
            escapeCsv((discrepancy.issues || []).join(' | ')),
          ].join(','));
        });
      } else {
        rows.push([
          ...base.map(escapeCsv),
          '',
          '',
        ].join(','));
      }
    });

    const csv = `\uFEFF${rows.join('\n')}`;
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8;' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `weekly_dispute_report_week_${weeklyDisputeReport.week_number}_${weeklyDisputeReport.week_start}.csv`;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
    showMessage('success', 'Weekly dispute report exported for Excel');
  };

  const handleExportWeeklyDisputePdf = () => {
    if (!weeklyDisputeReport) {
      showMessage('warning', 'Create a weekly dispute report before exporting');
      return;
    }

    const popup = window.open('', '_blank', 'width=1024,height=768');
    if (!popup) {
      showMessage('error', 'Unable to open print window. Please allow popups.');
      return;
    }

    const rowsHtml = weeklyDisputeReport.items
      .map((item) => {
        return `
          <tr>
            <td>${item.invoice_number}</td>
            <td>${item.station || '—'}</td>
            <td>${new Date(item.period_start).toLocaleDateString()} - ${new Date(item.period_end).toLocaleDateString()}</td>
            <td style="text-align:right;">${item.discrepancy_count}</td>
          </tr>
        `;
      })
      .join('');

    popup.document.write(`
      <html>
        <head>
          <title>Weekly Dispute Report</title>
          <style>
            body { font-family: Arial, sans-serif; padding: 24px; color: #1f2937; }
            h1 { margin: 0 0 8px; font-size: 22px; }
            p { margin: 4px 0; }
            table { width: 100%; border-collapse: collapse; margin-top: 16px; }
            th, td { border: 1px solid #d1d5db; padding: 8px; font-size: 12px; }
            th { background: #f3f4f6; text-align: left; }
          </style>
        </head>
        <body>
          <h1>Weekly Dispute Report</h1>
          <p><strong>Week #:</strong> ${weeklyDisputeReport.week_number}</p>
          <p><strong>Week Range:</strong> ${new Date(weeklyDisputeReport.week_start).toLocaleDateString()} - ${new Date(weeklyDisputeReport.week_end).toLocaleDateString()}</p>
          <p><strong>Total Dispute Items:</strong> ${weeklyDisputeReport.count}</p>
          <table>
            <thead>
              <tr>
                <th>Invoice #</th>
                <th>Station</th>
                <th>Period</th>
                <th style="text-align:right;">Discrepancies</th>
              </tr>
            </thead>
            <tbody>
              ${rowsHtml || '<tr><td colspan="4">No dispute items for this week.</td></tr>'}
            </tbody>
          </table>
        </body>
      </html>
    `);
    popup.document.close();
    popup.focus();
    popup.print();
    popup.close();
    showMessage('success', 'Weekly dispute report opened for PDF export');
  };

  const handleAssignmentComplete = async () => {
    // After manual assignment is complete, try to generate handouts
    setShowManualAssignment(false);
    const token = localStorage.getItem('access_token');
    const headers: Record<string, string> = token ? { 'Authorization': `Bearer ${token}` } : {};
    const prompted = await fetchAssignmentsAndPromptPrimary(headers, true);
    if (!prompted) {
      await handleGenerateHandouts();
    }
  };

  const handlePrimaryDriversComplete = async () => {
    setShowPrimaryDriverModal(false);
    setPrimaryDriverRoutes([]);
    if (autoGenerateAfterPrimary) {
      setAutoGenerateAfterPrimary(false);
      await handleGenerateHandouts();
    }
  };

  const handleGenerateHandouts = async () => {
    setIsLoading(true);
    try {
      const token = localStorage.getItem('access_token');
      const headers: Record<string, string> = token ? { 'Authorization': `Bearer ${token}` } : {};
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

      {/* Primary Driver Selection Dialog */}
      {showPrimaryDriverModal && (
        <PrimaryDriverSelection
          routes={primaryDriverRoutes}
          apiUrl={API_URL}
          onComplete={handlePrimaryDriversComplete}
          onClose={() => {
            setShowPrimaryDriverModal(false);
            setPrimaryDriverRoutes([]);
            setAutoGenerateAfterPrimary(false);
          }}
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
                <div className="space-y-6">
                  <div className="bg-white rounded-lg shadow-md p-6 border border-gray-200">
                    <h4 className="text-lg font-bold text-ndl-blue mb-4">WST vs Cortex/DOP Daily Comparison</h4>
                    <div className="grid grid-cols-1 md:grid-cols-5 gap-3 items-end">
                      <div>
                        <label className="block text-sm font-semibold text-gray-700 mb-1">Date</label>
                        <input
                          type="date"
                          value={comparisonDate}
                          onChange={(e) => setComparisonDate(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-semibold text-gray-700 mb-1">Station (optional)</label>
                        <input
                          type="text"
                          placeholder="e.g. DLV3"
                          value={comparisonStation}
                          onChange={(e) => setComparisonStation(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                        />
                      </div>
                      <div>
                        <label className="block text-sm font-semibold text-gray-700 mb-1">Invoice # (optional)</label>
                        <select
                          value={comparisonInvoiceNumber}
                          onChange={(e) => setComparisonInvoiceNumber(e.target.value)}
                          className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                        >
                          <option value="">Select invoice (newest to oldest)</option>
                          {invoiceOptions.map((invoice) => (
                            <option key={invoice.id} value={invoice.invoice_number}>
                              {invoice.invoice_number}
                              {invoice.invoice_date ? ` • ${invoice.invoice_date}` : ''}
                              {invoice.station ? ` • ${invoice.station}` : ''}
                            </option>
                          ))}
                        </select>
                        {invoiceOptionsLoading && (
                          <p className="text-xs text-gray-500 mt-1">Loading invoices...</p>
                        )}
                      </div>
                      <div className="md:col-span-2">
                        <div className="flex flex-wrap gap-2">
                          <button
                            onClick={handleRunDailyComparison}
                            disabled={comparisonLoading}
                            className="w-full md:w-auto px-4 py-2 bg-ndl-blue hover:bg-blue-700 text-white rounded-lg font-semibold transition disabled:opacity-60"
                          >
                            {comparisonLoading ? 'Running Comparison...' : 'Run Daily Comparison'}
                          </button>
                          <button
                            onClick={handleCreateWeeklyDispute}
                            disabled={weeklyDisputeLoading}
                            className="w-full md:w-auto px-4 py-2 bg-orange-600 hover:bg-orange-700 text-white rounded-lg font-semibold transition disabled:opacity-60"
                          >
                            {weeklyDisputeLoading ? 'Creating Dispute...' : 'Create Weekly Dispute Report'}
                          </button>
                        </div>
                      </div>
                    </div>

                    {comparisonResult && (
                      <div className="mt-4 border border-gray-200 rounded-lg p-4 bg-gray-50">
                        <div className="flex items-center justify-between mb-3">
                          <p className="text-sm text-gray-700">
                            Date: <span className="font-semibold">{formatDateOnly(comparisonResult.comparison_date)}</span> | Week #{comparisonResult.week_number}
                          </p>
                          <span className={`text-xs px-2 py-1 rounded-full font-semibold ${comparisonResult.aligned ? 'bg-green-100 text-green-800' : 'bg-yellow-100 text-yellow-800'}`}>
                            {comparisonResult.aligned ? 'Aligned' : 'Needs Review'}
                          </span>
                        </div>

                        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-sm">
                          <div className="bg-white rounded p-2 border border-gray-200">
                            <p className="text-gray-600">Cortex</p>
                            <p className="text-lg font-bold text-gray-900">{comparisonResult.counts.cortex_routes}</p>
                          </div>
                          <div className="bg-white rounded p-2 border border-gray-200">
                            <p className="text-gray-600">DOP</p>
                            <p className="text-lg font-bold text-gray-900">{comparisonResult.counts.dop_routes}</p>
                          </div>
                          <div className="bg-white rounded p-2 border border-gray-200">
                            <p className="text-gray-600">WST Service Routes</p>
                            <p className="text-lg font-bold text-gray-900">{comparisonResult.counts.wst_total}</p>
                          </div>
                          <div className="bg-white rounded p-2 border border-gray-200">
                            <p className="text-gray-600">WST Service Details</p>
                            <p className="text-lg font-bold text-gray-900">{comparisonResult.counts.wst_service_details}</p>
                          </div>
                          <div className="bg-white rounded p-2 border border-gray-200">
                            <p className="text-gray-600">Invoice Routes</p>
                            <p className="text-lg font-bold text-gray-900">{comparisonResult.counts.invoice_routes ?? '—'}</p>
                          </div>
                        </div>

                        {comparisonResult.route_alignment && (
                          <div className="mt-3 bg-white border border-gray-200 rounded p-3 text-sm text-gray-700">
                            <p>
                              Cortex vs WST Route Delta: <span className="font-semibold">{comparisonResult.route_alignment.cortex_vs_wst_delta}</span>
                            </p>
                            <p>
                              Cortex vs Invoice Route Delta: <span className="font-semibold">{comparisonResult.route_alignment.cortex_vs_invoice_delta ?? 'N/A'}</span>
                            </p>
                            {comparisonResult.route_alignment.invoice_number && (
                              <p>
                                Invoice Compared: <span className="font-semibold">{comparisonResult.route_alignment.invoice_number}</span>
                              </p>
                            )}
                          </div>
                        )}

                        {comparisonResult.route_payment_audit && (
                          <div className="mt-3 bg-white border border-gray-200 rounded p-3">
                            <p className="text-sm font-semibold text-gray-800 mb-2">
                              Core Audit: Routes Completed (Cortex) vs Routes Paid (Invoice)
                            </p>

                            {comparisonResult.route_payment_audit.requires_user_confirmation && (
                              <div className="mb-3 rounded border border-yellow-300 bg-yellow-50 p-3">
                                <p className="text-sm font-semibold text-yellow-900">User Confirmation Required</p>
                                <p className="text-sm text-yellow-800">
                                  {comparisonResult.route_payment_audit.prompt_message || 'Review required routes before finalizing this audit.'}
                                </p>
                              </div>
                            )}

                            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm text-gray-700 mb-3">
                              <p>Cortex Completed: <span className="font-semibold">{comparisonResult.route_payment_audit.routes_completed_cortex}</span></p>
                              <p>Invoice Paid: <span className="font-semibold">{comparisonResult.route_payment_audit.routes_paid_invoice ?? '—'}</span></p>
                              <p>WST Seen: <span className="font-semibold">{comparisonResult.route_payment_audit.routes_seen_wst}</span></p>
                              <p>DOP Planned: <span className="font-semibold">{comparisonResult.route_payment_audit.routes_planned_dop}</span></p>
                              <p>Not In DOP: <span className="font-semibold">{comparisonResult.route_payment_audit.routes_not_in_dop}</span></p>
                              <p>Pending Confirm: <span className="font-semibold">{comparisonResult.route_payment_audit.pending_confirmation}</span></p>
                              <p>Excluded Missort: <span className="font-semibold">{comparisonResult.route_payment_audit.excluded_missort}</span></p>
                              <p>Effective Cortex: <span className="font-semibold">{comparisonResult.route_payment_audit.effective_cortex_routes}</span></p>
                            </div>

                            {comparisonResult.route_payment_audit.prompt_items?.length > 0 && (
                              <div className="overflow-x-auto border border-yellow-200 rounded bg-yellow-50">
                                <table className="w-full text-xs md:text-sm">
                                  <thead>
                                    <tr className="bg-yellow-100 border-b border-yellow-200">
                                      <th className="px-2 py-2 text-left">Route</th>
                                      <th className="px-2 py-2 text-left">Prompt</th>
                                      <th className="px-2 py-2 text-left">Status</th>
                                      <th className="px-2 py-2 text-left">Action</th>
                                    </tr>
                                  </thead>
                                  <tbody>
                                    {comparisonResult.route_payment_audit.prompt_items.map((item) => (
                                      <tr key={item.route_code} className="border-b border-yellow-100 last:border-b-0">
                                        <td className="px-2 py-2 font-mono">{item.route_code}</td>
                                        <td className="px-2 py-2">{item.prompt}</td>
                                        <td className="px-2 py-2 font-semibold">{item.action_status}</td>
                                        <td className="px-2 py-2">
                                          <div className="flex gap-2">
                                            <button
                                              onClick={() => handleRoutePromptAction(item.route_code, 'confirm_valid_route')}
                                              disabled={routeActionSaving === item.route_code}
                                              className="px-2 py-1 rounded bg-green-600 text-white hover:bg-green-700 disabled:opacity-60"
                                            >
                                              Confirm Valid
                                            </button>
                                            <button
                                              onClick={() => handleRoutePromptAction(item.route_code, 'exclude_missort')}
                                              disabled={routeActionSaving === item.route_code}
                                              className="px-2 py-1 rounded bg-red-600 text-white hover:bg-red-700 disabled:opacity-60"
                                            >
                                              Remove Missort
                                            </button>
                                          </div>
                                        </td>
                                      </tr>
                                    ))}
                                  </tbody>
                                </table>
                              </div>
                            )}
                          </div>
                        )}

                        {comparisonResult.service_type_comparison && (
                          <div className="mt-3 bg-white border border-gray-200 rounded p-3">
                            <p className="text-sm font-semibold text-gray-800 mb-2">
                              Service Type Comparison: {comparisonResult.service_type_comparison.service_type_filter}
                            </p>

                            <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-sm text-gray-700 mb-3">
                              <p>WST Routes: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.wst_routes}</span></p>
                              <p>DOP Routes: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.dop_routes}</span></p>
                              <p>Route Delta: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.route_delta}</span></p>
                              <p>WST Packages: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.wst_packages}</span></p>
                              <p>DOP Packages: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.dop_packages}</span></p>
                              <p>Package Delta: <span className="font-semibold">{comparisonResult.service_type_comparison.totals.package_delta}</span></p>
                            </div>

                            <div className="overflow-x-auto border border-gray-200 rounded">
                              <table className="w-full text-xs md:text-sm">
                                <thead>
                                  <tr className="bg-gray-100 border-b border-gray-200">
                                    <th className="px-2 py-2 text-left">Route</th>
                                    <th className="px-2 py-2 text-left">WST Driver</th>
                                    <th className="px-2 py-2 text-right">WST Pkg</th>
                                    <th className="px-2 py-2 text-right">DOP Pkg</th>
                                    <th className="px-2 py-2 text-right">Delta</th>
                                    <th className="px-2 py-2 text-left">Issues</th>
                                  </tr>
                                </thead>
                                <tbody>
                                  {comparisonResult.service_type_comparison.line_items.map((item, idx) => (
                                    <tr key={`${item.route_code}-${idx}`} className="border-b border-gray-100 last:border-b-0">
                                      <td className="px-2 py-2 font-mono">{item.route_code}</td>
                                      <td className="px-2 py-2">{item.wst?.driver_name || '—'}</td>
                                      <td className="px-2 py-2 text-right">{item.wst?.packages ?? '—'}</td>
                                      <td className="px-2 py-2 text-right">{item.dop?.packages ?? '—'}</td>
                                      <td className="px-2 py-2 text-right font-semibold">{item.package_delta ?? '—'}</td>
                                      <td className="px-2 py-2">{item.issues?.length ? item.issues.join(', ') : 'matched'}</td>
                                    </tr>
                                  ))}
                                </tbody>
                              </table>
                            </div>
                          </div>
                        )}

                        <div className="mt-3 bg-white border border-gray-200 rounded p-3">
                          <p className="text-sm font-semibold text-gray-800 mb-2">Comparison Diagnostics</p>
                          <div className="grid grid-cols-1 md:grid-cols-2 gap-2 text-sm text-gray-700">
                            <p>
                              Requested Date: <span className="font-semibold">{formatDateOnly(comparisonDate)}</span>
                            </p>
                            <p>
                              Compared Date: <span className="font-semibold">{formatDateOnly(comparisonResult.comparison_date)}</span>
                            </p>
                            <p>
                              Station Filter: <span className="font-semibold">{comparisonResult.station || 'All stations'}</span>
                            </p>
                            <p>
                              Cortex Rows: <span className="font-semibold">{comparisonResult.counts.cortex_routes}</span>
                            </p>
                            <p>
                              DOP Rows: <span className="font-semibold">{comparisonResult.counts.dop_routes}</span>
                            </p>
                            <p>
                              WST Service Rows: <span className="font-semibold">{comparisonResult.counts.wst_total}</span>
                            </p>
                          </div>

                          <div className="mt-2 text-xs text-gray-600">
                            WST all-table record breakdown → Delivered: {comparisonResult.counts.wst_delivered_packages}, Service Details: {comparisonResult.counts.wst_service_details}, Training: {comparisonResult.counts.wst_training_weekly}, Unplanned Delay: {comparisonResult.counts.wst_unplanned_delay}, Weekly Report: {comparisonResult.counts.wst_weekly_report}, Combined: {comparisonResult.counts.wst_total_all_tables ?? 0}
                          </div>
                        </div>

                        {!comparisonResult.aligned && comparisonResult.issues?.length > 0 && (
                          <div className="mt-3 bg-yellow-50 border border-yellow-200 rounded p-3">
                            <p className="text-sm font-semibold text-yellow-800 mb-1">Issues</p>
                            <ul className="list-disc list-inside text-sm text-yellow-800">
                              {comparisonResult.issues.map((issue, idx) => (
                                <li key={idx}>{issue}</li>
                              ))}
                            </ul>
                          </div>
                        )}
                      </div>
                    )}

                    {weeklyDisputeReport && (
                      <div className="mt-4 border border-orange-200 rounded-lg p-4 bg-orange-50">
                        <div className="flex items-center justify-between mb-3">
                          <p className="text-sm text-orange-900">
                            Weekly Dispute Report: Week #{weeklyDisputeReport.week_number} ({new Date(weeklyDisputeReport.week_start).toLocaleDateString()} - {new Date(weeklyDisputeReport.week_end).toLocaleDateString()})
                          </p>
                          <div className="flex items-center gap-2">
                            <button
                              onClick={handleExportWeeklyDisputeExcel}
                              className="text-xs px-2 py-1 rounded-md font-semibold bg-emerald-100 text-emerald-800 hover:bg-emerald-200 transition"
                            >
                              Export Excel
                            </button>
                            <button
                              onClick={handleExportWeeklyDisputePdf}
                              className="text-xs px-2 py-1 rounded-md font-semibold bg-sky-100 text-sky-800 hover:bg-sky-200 transition"
                            >
                              Export PDF
                            </button>
                            <span className="text-xs px-2 py-1 rounded-full font-semibold bg-orange-100 text-orange-800">
                              {weeklyDisputeReport.count} item(s)
                            </span>
                          </div>
                        </div>

                        {weeklyDisputeReport.count === 0 ? (
                          <p className="text-sm text-orange-900">No dispute items for the selected week.</p>
                        ) : (
                          <div className="overflow-x-auto">
                            <table className="w-full text-sm bg-white rounded border border-orange-200">
                              <thead>
                                <tr className="bg-orange-100 border-b border-orange-200">
                                  <th className="px-3 py-2 text-left">Invoice #</th>
                                  <th className="px-3 py-2 text-left">Station</th>
                                  <th className="px-3 py-2 text-left">Period</th>
                                  <th className="px-3 py-2 text-right">Discrepancies</th>
                                </tr>
                              </thead>
                              <tbody>
                                {weeklyDisputeReport.items.map((item) => (
                                  <tr key={item.invoice_number} className="border-b border-orange-100 last:border-b-0">
                                    <td className="px-3 py-2 font-mono">{item.invoice_number}</td>
                                    <td className="px-3 py-2">{item.station || '—'}</td>
                                    <td className="px-3 py-2">
                                      {new Date(item.period_start).toLocaleDateString()} - {new Date(item.period_end).toLocaleDateString()}
                                    </td>
                                    <td className="px-3 py-2 text-right font-semibold">{item.discrepancy_count}</td>
                                  </tr>
                                ))}
                              </tbody>
                            </table>
                          </div>
                        )}
                      </div>
                    )}
                  </div>

                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                  <p className="text-sm text-blue-900 mb-3">
                    Invoice ingest tools are now in a separate location for cleaner workflow.
                  </p>
                  <Link
                    href="/invoice-ingest"
                    className="inline-flex px-4 py-2 bg-ndl-blue hover:bg-blue-700 text-white rounded-lg font-semibold transition"
                  >
                    Open Invoice Ingest Tools
                  </Link>
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
