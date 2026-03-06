'use client';

import { useState, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import UploadZone from '../components/UploadZone';
import StatusDisplay from '../components/StatusDisplay';
import { ProtectedRoute } from '../components/ProtectedRoute';

interface UploadResponse {
  filename: string;
  status: string;
  timestamp: string;
  scheduled_date: string;
  assignments_count: number;
  sweepers_count: number;
  report_generated?: boolean;
  report_compact?: boolean;
  report_path?: string;
  errors: string[];
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function DriverSchedulePage() {
  const [isLoading, setIsLoading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [reportDownloading, setReportDownloading] = useState(false);
  const [reportAvailable, setReportAvailable] = useState(false);
  const [compactReport, setCompactReport] = useState(true);

  const handleDrop = useCallback(async (files: File[]) => {
    if (files.length === 0) return;

    setIsLoading(true);
    setUploadError(null);
    setUploadStatus('Uploading driver schedule...');
    setReportAvailable(false);

    try {
      const formData = new FormData();
      formData.append('file', files[0]);
      formData.append('compact', compactReport ? 'true' : 'false');

      const response = await fetch(`${API_URL}/upload/driver-schedule-report-only`, {
        method: 'POST',
        body: formData,
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
      });

      if (!response.ok) {
        const error = await response.json();
        throw new Error(error.detail || 'Failed to upload driver schedule');
      }

      const data: UploadResponse = await response.json();
      setUploadStatus(
        `✓ Uploaded: ${data.filename} | ${data.assignments_count} assignments, ${data.sweepers_count} sweepers`
      );
      setReportAvailable(Boolean(data.report_generated));
    } catch (error) {
      setUploadError(
        error instanceof Error ? error.message : 'Failed to upload driver schedule'
      );
      setUploadStatus(null);
      setReportAvailable(false);
    } finally {
      setIsLoading(false);
    }
  }, [compactReport]);

  const handleDownloadReport = async () => {
    setReportDownloading(true);
    try {
      const response = await fetch(`${API_URL}/upload/download-schedule-report`, {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
      });

      if (!response.ok) {
        throw new Error('Failed to download report');
      }

      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = 'NDAY_Driver_Schedule_Report.pdf';
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Error downloading report:', error);
      setUploadError('Failed to download report. Please try again.');
    } finally {
      setReportDownloading(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
        <PageHeader title="Driver Schedule Management" showBack={true} />

        <main className="max-w-6xl mx-auto px-4 py-8">
          {/* Upload Section */}
          <div className="bg-white rounded-lg shadow-md p-8 mb-8">
            <h2 className="text-2xl font-bold text-gray-800 mb-6">Upload Driver Schedule</h2>
            <p className="text-gray-600 mb-6">
              Upload the driver schedule Excel file with "Rostered Work Blocks" and "Shifts & Availability" tabs.
              Show times will be calculated automatically (25 minutes before wave time).
            </p>

            <UploadZone
              onDrop={handleDrop}
              accept=".xlsx,.xls"
              multiple={false}
              label="Driver Schedule"
            >
              <svg
                className="mx-auto h-12 w-12 text-indigo-600 mb-2"
                stroke="currentColor"
                fill="none"
                viewBox="0 0 48 48"
              >
                <path
                  d="M28 8H12a4 4 0 00-4 4v20a4 4 0 004 4h24a4 4 0 004-4V20m-8-12v12m0 0l-4-4m4 4l4-4"
                  strokeWidth={2}
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
              <p className="text-lg font-semibold text-gray-700">
                Drag and drop your schedule file here
              </p>
              <p className="text-sm text-gray-500">or click to browse</p>
            </UploadZone>

            <div className="mt-4 flex items-center gap-2">
              <input
                id="compact-report"
                type="checkbox"
                checked={compactReport}
                onChange={(event) => setCompactReport(event.target.checked)}
                className="h-4 w-4"
              />
              <label htmlFor="compact-report" className="text-sm text-gray-700">
                Force compact one-page report
              </label>
            </div>

            {isLoading && (
              <div className="mt-4 text-center">
                <div className="inline-block animate-spin">
                  <svg className="w-6 h-6 text-indigo-600" fill="none" viewBox="0 0 24 24">
                    <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" opacity="0.25"></circle>
                    <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                  </svg>
                </div>
              </div>
            )}

            {uploadStatus && (
              <StatusDisplay messages={[{ type: 'success', text: uploadStatus }]} />
            )}

            {uploadError && (
              <StatusDisplay messages={[{ type: 'error', text: uploadError }]} />
            )}

            {reportAvailable && (
              <div className="mt-6 flex items-center justify-between rounded-lg border border-indigo-200 bg-indigo-50 p-4">
                <div>
                  <p className="font-semibold text-indigo-900">Driver Schedule Report Ready</p>
                  <p className="text-sm text-indigo-700">Report-only mode is active. Schedule matrix is not persisted.</p>
                </div>
                <button
                  onClick={handleDownloadReport}
                  disabled={reportDownloading}
                  className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 flex items-center gap-2"
                >
                  {reportDownloading ? 'Downloading...' : 'Download Report'}
                </button>
              </div>
            )}
          </div>

          {/* Info Section */}
          <div className="bg-indigo-50 border-l-4 border-indigo-500 rounded-lg p-6 mt-8">
            <h3 className="font-bold text-indigo-900 mb-2">How It Works</h3>
            <ul className="text-sm text-indigo-800 space-y-2">
              <li>• <strong>Show Time:</strong> Calculated 25 minutes before wave time</li>
              <li>• <strong>Wave Consolidation:</strong> Waves within 5 minutes share the same show time</li>
              <li>• <strong>Report-only:</strong> The on-screen schedule matrix is intentionally not retained</li>
              <li>• <strong>File Format:</strong> Excel with "Rostered Work Blocks" and "Shifts & Availability" tabs</li>
              <li>• <strong>Upload Time:</strong> Expected around 7 PM daily</li>
            </ul>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
