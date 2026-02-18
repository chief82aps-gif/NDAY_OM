'use client';

import { useEffect, useState } from 'react';

interface StatusMessage {
  type: 'success' | 'error' | 'info' | 'warning';
  text: string;
}

interface StatusDisplayProps {
  dop_uploaded?: boolean;
  fleet_uploaded?: boolean;
  cortex_uploaded?: boolean;
  route_sheets_uploaded?: boolean;
  dop_record_count?: number;
  fleet_record_count?: number;
  cortex_record_count?: number;
  route_sheets_count?: number;
  assignments_count?: number;
  validation_errors?: string[];
  validation_warnings?: string[];
  messages?: StatusMessage[];
  isLoading?: boolean;
}

export default function StatusDisplay({
  dop_uploaded,
  fleet_uploaded,
  cortex_uploaded,
  route_sheets_uploaded,
  dop_record_count,
  fleet_record_count,
  cortex_record_count,
  route_sheets_count,
  assignments_count,
  validation_errors,
  validation_warnings,
  messages,
  isLoading,
}: StatusDisplayProps) {
  const [displayMessages, setDisplayMessages] = useState<StatusMessage[]>([]);
  const [showErrors, setShowErrors] = useState(false);
  const [showWarnings, setShowWarnings] = useState(true);

  useEffect(() => {
    if (messages) {
      setDisplayMessages(messages);
      const timer = setTimeout(() => {
        setDisplayMessages((prev) => prev.slice(1));
      }, 5000);
      return () => clearTimeout(timer);
    }
  }, [messages]);

  const uploadStatus = [
    { label: 'DOP', uploaded: dop_uploaded, count: dop_record_count },
    { label: 'Fleet', uploaded: fleet_uploaded, count: fleet_record_count },
    { label: 'Cortex', uploaded: cortex_uploaded, count: cortex_record_count },
    {
      label: 'Route Sheets',
      uploaded: route_sheets_uploaded,
      count: route_sheets_count,
    },
  ];

  const anyUploaded = uploadStatus.some((s) => s.uploaded);

  return (
    <div className="space-y-4">
      {/* Messages */}
      {displayMessages.length > 0 && (
        <div className="space-y-2">
          {displayMessages.map((msg, idx) => (
            <div
              key={idx}
              className={`p-4 rounded-lg ${
                msg.type === 'success'
                  ? 'bg-green-100 text-green-700 border-l-4 border-green-500'
                  : msg.type === 'error'
                  ? 'bg-red-100 text-red-700 border-l-4 border-red-500'
                  : msg.type === 'warning'
                  ? 'bg-yellow-100 text-yellow-700 border-l-4 border-yellow-500'
                  : 'bg-blue-100 text-blue-700 border-l-4 border-blue-500'
              }`}
            >
              {msg.text}
            </div>
          ))}
        </div>
      )}

      {/* Loading */}
      {isLoading && (
        <div className="flex items-center justify-center p-4">
          <div className="animate-spin rounded-full h-8 w-8 border-t-2 border-b-2 border-ndl-blue"></div>
          <span className="ml-3 text-gray-600">Processing...</span>
        </div>
      )}

      {/* Upload Status */}
      {anyUploaded && (
        <div className="card">
          <h3 className="text-lg font-bold text-ndl-blue mb-4">Upload Status</h3>
          <div className="grid grid-cols-2 gap-4">
            {uploadStatus.map((status) => (
              <div
                key={status.label}
                className={`p-3 rounded-lg ${
                  status.uploaded
                    ? 'bg-green-50 border border-green-300'
                    : 'bg-gray-50 border border-gray-300'
                }`}
              >
                <div className="flex items-center mb-2">
                  <div
                    className={`w-3 h-3 rounded-full mr-2 ${
                      status.uploaded ? 'bg-green-500' : 'bg-gray-400'
                    }`}
                  ></div>
                  <span className="font-semibold text-sm">{status.label}</span>
                </div>
                {status.uploaded && status.count !== undefined && (
                  <p className="text-sm text-gray-600 ml-5">
                    {status.count} records
                  </p>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Assignments & Generation */}
      {assignments_count !== undefined && assignments_count > 0 && (
        <div className="card bg-ndl-light border-ndl-blue">
          <p className="text-sm font-semibold text-ndl-blue">
            ✓ {assignments_count} routes assigned to vehicles
          </p>
        </div>
      )}

      {/* Errors */}
      {validation_errors && validation_errors.length > 0 && (
        <div className="card border-yellow-300 bg-yellow-50">
          <button
            onClick={() => setShowErrors(!showErrors)}
            className="w-full text-left font-bold text-yellow-700 mb-2 hover:text-yellow-800 cursor-pointer flex items-center justify-between"
          >
            <span>
              {validation_errors.length} Data Issues
            </span>
            <span className="text-sm">{showErrors ? '▼' : '▶'}</span>
          </button>
          {showErrors && (
            <ul className="text-sm text-yellow-600 space-y-1 max-h-32 overflow-y-auto">
              {validation_errors.slice(0, 10).map((error, idx) => (
                <li key={idx} className="text-xs">
                  • {error}
                </li>
              ))}
              {validation_errors.length > 10 && (
                <li className="text-xs font-semibold">
                  ... and {validation_errors.length - 10} more issues
                </li>
              )}
            </ul>
          )}
          <p className="text-xs text-yellow-600 mt-2">
            Note: These are usually minor data quality issues and don't block processing.
          </p>
        </div>
      )}

      {/* Warnings */}
      {validation_warnings && validation_warnings.length > 0 && (
        <div className="card border-orange-300 bg-orange-50">
          <button
            onClick={() => setShowWarnings(!showWarnings)}
            className="w-full text-left font-bold text-orange-700 mb-2 hover:text-orange-800 cursor-pointer flex items-center justify-between"
          >
            <span>
              {validation_warnings.length} Informational Alerts
            </span>
            <span className="text-sm">{showWarnings ? '▼' : '▶'}</span>
          </button>
          {showWarnings && (
            <ul className="text-sm text-orange-600 space-y-1 max-h-32 overflow-y-auto">
              {validation_warnings.slice(0, 5).map((warning, idx) => (
                <li key={idx} className="text-xs">
                  ⚠ {warning}
                </li>
              ))}
              {validation_warnings.length > 5 && (
                <li className="text-xs font-semibold">
                  ... and {validation_warnings.length - 5} more alerts
                </li>
              )}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
