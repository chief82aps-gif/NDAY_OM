'use client';

import { useState, useCallback } from 'react';
import { useRouter } from 'next/router';
import UploadZone from '../components/UploadZone';
import StatusDisplay from '../components/StatusDisplay';

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
}

interface StatusMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function Upload() {
  const router = useRouter();
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
  });

  const [messages, setMessages] = useState<StatusMessage[]>([]);
  const [isLoading, setIsLoading] = useState(false);
  const [pdfGenerated, setPdfGenerated] = useState(false);

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

        const response = await fetch(`${API_URL}/upload${endpoint}`, {
          method: 'POST',
          body: formData,
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
      const response = await fetch(`${API_URL}/upload/assign-vehicles`, {
        method: 'POST',
      });

      if (!response.ok) {
        throw new Error(`Assignment failed: ${response.status}`);
      }

      const result = await response.json();

      if (result.success) {
        showMessage(
          'success',
          `Assigned ${result.assigned}/${result.total_routes} routes (${result.success_rate}%)`
        );

        // Fetch updated status
        const statusResponse = await fetch(`${API_URL}/upload/status`);
        const statusData = await statusResponse.json();
        setStatus(statusData);
      } else {
        showMessage('error', result.message || 'Assignment failed');
      }
    } catch (error) {
      showMessage('error', `Assignment failed: ${error instanceof Error ? error.message : 'Unknown error'}`);
    } finally {
      setIsLoading(false);
    }
  };

  const handleGenerateHandouts = async () => {
    setIsLoading(true);
    try {
      const response = await fetch(`${API_URL}/upload/generate-handouts`, {
        method: 'POST',
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
    <div className="min-h-screen bg-white">
      {/* Header */}
      <header className="bg-ndl-blue text-white py-6 shadow-md">
        <div className="max-w-6xl mx-auto px-4">
          <div className="flex items-center justify-between mb-2">
            <div className="flex items-center">
              <div className="w-12 h-12 bg-white rounded-lg flex items-center justify-center mr-3">
                <span className="text-ndl-blue font-bold text-xl">N</span>
              </div>
              <div>
                <h1 className="text-3xl font-bold">NDAY Route Manager</h1>
                <p className="text-blue-100">Upload and manage delivery routes</p>
              </div>
            </div>
            <button
              onClick={() => router.push('/')}
              className="bg-white text-ndl-blue px-4 py-2 rounded font-semibold hover:bg-blue-50 transition"
            >
              ← Back to Dashboard
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-4 py-8">
        {/* Messages */}
        <StatusDisplay messages={messages} isLoading={isLoading} />

        {/* Upload Section */}
        <div className="mt-8">
          <h2 className="text-2xl font-bold text-ndl-blue mb-6">Daily Driver Assignment</h2>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-8">
            {/* DOP Upload */}
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                DOP (Day of Plan)
              </label>
              <UploadZone
                label="DOP Excel"
                accept=".xlsx,.xls"
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
                label="Fleet Excel"
                accept=".xlsx,.xls"
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
                label="Cortex Excel"
                accept=".xlsx,.xls"
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

          {/* Status Display */}
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
        </div>

        {/* Actions Section */}
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
      </main>

      {/* Footer */}
      <footer className="bg-gray-100 border-t border-gray-300 mt-12 py-4">
        <div className="max-w-6xl mx-auto px-4 text-center text-sm text-gray-600">
          <p>NDAY Route Manager © 2026. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
