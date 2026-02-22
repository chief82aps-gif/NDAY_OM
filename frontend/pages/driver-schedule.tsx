'use client';

import { useState, useCallback } from 'react';
import PageHeader from '../components/PageHeader';
import UploadZone from '../components/UploadZone';
import StatusDisplay from '../components/StatusDisplay';
import { ProtectedRoute } from '../components/ProtectedRoute';

interface DriverAssignment {
  driver_name: string;
  date: string;
  wave_time: string;
  service_type: string;
  show_time: string;
}

interface DriverScheduleSummary {
  timestamp: string;
  scheduled_date: string;
  assignments: DriverAssignment[];
  sweepers: string[];
  show_times: Record<string, string>;
  summary: {
    total_assigned: number;
    total_sweepers: number;
    total_drivers: number;
  };
}

interface UploadResponse {
  filename: string;
  status: string;
  timestamp: string;
  scheduled_date: string;
  assignments_count: number;
  sweepers_count: number;
  errors: string[];
}

export default function DriverSchedulePage() {
  const [isLoading, setIsLoading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [schedule, setSchedule] = useState<DriverScheduleSummary | null>(null);
  const [reportDownloading, setReportDownloading] = useState(false);

  const handleDrop = useCallback(async (files: File[]) => {
    if (files.length === 0) return;

    setIsLoading(true);
    setUploadError(null);
    setUploadStatus('Uploading driver schedule...');

    try {
      const formData = new FormData();
      formData.append('file', files[0]);

      const response = await fetch('/upload/driver-schedule', {
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

      // Fetch the schedule summary
      await fetchScheduleSummary();
    } catch (error) {
      setUploadError(
        error instanceof Error ? error.message : 'Failed to upload driver schedule'
      );
      setUploadStatus(null);
    } finally {
      setIsLoading(false);
    }
  }, []);

  const fetchScheduleSummary = async () => {
    try {
      const response = await fetch('/upload/driver-schedule-summary', {
        headers: {
          'Authorization': `Bearer ${localStorage.getItem('token')}`,
        },
      });

      if (!response.ok) {
        throw new Error('Failed to fetch schedule summary');
      }

      const data: DriverScheduleSummary = await response.json();
      setSchedule(data);
    } catch (error) {
      console.error('Error fetching schedule:', error);
    }
  };

  const handleDownloadReport = async () => {
    setReportDownloading(true);
    try {
      const response = await fetch('/upload/download-schedule-report', {
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
              <StatusDisplay status={uploadStatus} type="success" />
            )}

            {uploadError && (
              <StatusDisplay status={uploadError} type="error" />
            )}
          </div>

          {/* Schedule Summary */}
          {schedule && (
            <div className="space-y-6">
              {/* Header Info */}
              <div className="bg-white rounded-lg shadow-md p-6">
                <div className="flex justify-between items-center mb-4">
                  <h2 className="text-2xl font-bold text-gray-800">Schedule Summary</h2>
                  <button
                    onClick={handleDownloadReport}
                    disabled={reportDownloading}
                    className="px-4 py-2 bg-indigo-600 text-white rounded-lg hover:bg-indigo-700 disabled:bg-gray-400 flex items-center gap-2"
                  >
                    {reportDownloading ? (
                      <>
                        <svg className="w-4 h-4 animate-spin" fill="none" viewBox="0 0 24 24">
                          <circle cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" opacity="0.25"></circle>
                          <path fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                        Downloading...
                      </>
                    ) : (
                      <>
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                          <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                        </svg>
                        Download Report
                      </>
                    )}
                  </button>
                </div>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                  <div>
                    <p className="text-sm text-gray-600">File Timestamp</p>
                    <p className="text-lg font-semibold text-gray-800">{schedule.timestamp}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600">Scheduled Date</p>
                    <p className="text-lg font-semibold text-gray-800">{schedule.scheduled_date}</p>
                  </div>
                  <div>
                    <p className="text-sm text-gray-600">Total Drivers</p>
                    <p className="text-lg font-semibold text-indigo-600">{schedule.summary.total_drivers}</p>
                  </div>
                </div>
              </div>

              {/* Show Times Summary */}
              <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="bg-blue-50 rounded-lg shadow-md p-6 border-l-4 border-blue-500">
                  <p className="text-sm text-gray-600 mb-2">Assigned Drivers</p>
                  <p className="text-3xl font-bold text-blue-600">{schedule.summary.total_assigned}</p>
                </div>
                <div className="bg-orange-50 rounded-lg shadow-md p-6 border-l-4 border-orange-500">
                  <p className="text-sm text-gray-600 mb-2">Sweepers</p>
                  <p className="text-3xl font-bold text-orange-600">{schedule.summary.total_sweepers}</p>
                </div>
                <div className="bg-green-50 rounded-lg shadow-md p-6 border-l-4 border-green-500">
                  <p className="text-sm text-gray-600 mb-2">Earliest Show Time</p>
                  <p className="text-3xl font-bold text-green-600">
                    {Object.values(schedule.show_times)[0] || 'N/A'}
                  </p>
                </div>
              </div>

              {/* Assignments Table */}
              <div className="bg-white rounded-lg shadow-md p-6">
                <h3 className="text-xl font-bold text-gray-800 mb-4">Driver Assignments</h3>
                <div className="overflow-x-auto">
                  <table className="w-full">
                    <thead>
                      <tr className="bg-gray-100 border-b">
                        <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700">Driver Name</th>
                        <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700">Wave Time</th>
                        <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700">Show Time</th>
                        <th className="px-4 py-2 text-left text-sm font-semibold text-gray-700">Service Type</th>
                      </tr>
                    </thead>
                    <tbody>
                      {schedule.assignments.map((assignment, idx) => (
                        <tr key={idx} className="border-b hover:bg-gray-50">
                          <td className="px-4 py-3 text-sm text-gray-800">{assignment.driver_name}</td>
                          <td className="px-4 py-3 text-sm text-gray-800">{assignment.wave_time}</td>
                          <td className="px-4 py-3 text-sm font-semibold text-indigo-600">{assignment.show_time}</td>
                          <td className="px-4 py-3 text-sm text-gray-600">{assignment.service_type}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>

              {/* Sweepers List */}
              {schedule.sweepers.length > 0 && (
                <div className="bg-white rounded-lg shadow-md p-6">
                  <h3 className="text-xl font-bold text-gray-800 mb-4">Sweepers (Unassigned but Available)</h3>
                  <div className="bg-orange-50 border border-orange-200 rounded p-4">
                    <div className="flex flex-wrap gap-2">
                      {schedule.sweepers.map((sweeper, idx) => (
                        <span
                          key={idx}
                          className="px-3 py-1 bg-orange-200 text-orange-800 rounded-full text-sm font-medium"
                        >
                          {sweeper}
                        </span>
                      ))}
                    </div>
                    <p className="text-sm text-orange-700 mt-3">
                      💡 All sweepers have show time: <strong>{schedule.show_times[schedule.sweepers[0]] || 'Wave 1 & 2 time'}</strong>
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Info Section */}
          <div className="bg-indigo-50 border-l-4 border-indigo-500 rounded-lg p-6 mt-8">
            <h3 className="font-bold text-indigo-900 mb-2">ℹ️ How It Works</h3>
            <ul className="text-sm text-indigo-800 space-y-2">
              <li>• <strong>Show Time:</strong> Calculated 25 minutes before wave time</li>
              <li>• <strong>Wave Consolidation:</strong> Waves within 5 minutes share the same show time</li>
              <li>• <strong>Sweepers:</strong> Available drivers not assigned to routes, scheduled for Wave 1 & 2 show time</li>
              <li>• <strong>File Format:</strong> Excel with "Rostered Work Blocks" and "Shifts & Availability" tabs</li>
              <li>• <strong>Upload Time:</strong> Expected around 7 PM daily</li>
            </ul>
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
