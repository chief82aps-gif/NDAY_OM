'use client';

import { useState, useEffect } from 'react';

interface MetricComparison {
  description: string;
  normalized: string;
  category: string;
  subtype?: string;
  metric_key?: string;
  mapping_source: string;
  invoice_rate: number;
  invoice_quantity: number;
  invoice_amount: number;
  expected_quantity?: number;
  quantity_delta?: number;
}

interface PromptItem {
  description: string;
  normalized: string;
  suggested_metric_key?: string;
  options: string[];
}

interface AuditReport {
  invoice_number: string;
  invoice_date: string;
  period_start: string;
  period_end: string;
  station: string;
  metrics: {
    total_routes?: number;
    routes_from_weekly_report?: number;
    routes_from_service_details?: number;
    training_eligible_total: number;
    delivered_packages_total: number;
    pickup_packages_total: number;
    package_total: number;
  };
  line_item_comparisons: MetricComparison[];
  needs_prompt: PromptItem[];
  metric_options: Record<string, string>;
}

interface StatusMessage {
  type: 'success' | 'error' | 'info';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

interface Props {
  invoiceNumber: string;
  token: string;
  onMessageChanged: (type: 'success' | 'error' | 'info', text: string) => void;
}

export default function VariableInvoiceAudit({
  invoiceNumber,
  token,
  onMessageChanged,
}: Props) {
  const [report, setReport] = useState<AuditReport | null>(null);
  const [loading, setLoading] = useState(true);
  const [savingMappings, setSavingMappings] = useState(false);
  const [mappingResponses, setMappingResponses] = useState<Record<string, string>>({});
  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());

  // Fetch audit report
  useEffect(() => {
    const fetchAudit = async () => {
      try {
        setLoading(true);
        const response = await fetch(
          `${API_URL}/audit/variable-invoice/${invoiceNumber}`,
          {
            method: 'GET',
            headers: {
              'Content-Type': 'application/json',
              'Authorization': `Bearer ${token}`,
            },
          }
        );

        if (!response.ok) {
          throw new Error('Failed to load audit report');
        }

        const data = await response.json();
        setReport(data);

        // Initialize mapping responses from suggested keys
        const initial: Record<string, string> = {};
        if (data.needs_prompt) {
          data.needs_prompt.forEach((item: PromptItem) => {
            if (item.suggested_metric_key) {
              initial[item.normalized] = item.suggested_metric_key;
            }
          });
        }
        setMappingResponses(initial);
      } catch (error) {
        onMessageChanged(
          'error',
          error instanceof Error ? error.message : 'Error loading audit'
        );
      } finally {
        setLoading(false);
      }
    };

    if (token) {
      fetchAudit();
    }
  }, [invoiceNumber, token]);

  const handleMappingChange = (normalized: string, metricKey: string) => {
    setMappingResponses((prev) => ({
      ...prev,
      [normalized]: metricKey,
    }));
  };

  const handleSaveMappings = async () => {
    try {
      setSavingMappings(true);

      if (!report?.needs_prompt || report.needs_prompt.length === 0) {
        onMessageChanged('info', 'No mappings to save');
        return;
      }

      const mappingsToSave = report.needs_prompt
        .filter((item) => item.normalized in mappingResponses)
        .map((item) => ({
          description: item.description,
          metric_key: mappingResponses[item.normalized],
        }));

      if (mappingsToSave.length === 0) {
        onMessageChanged('info', 'No mappings selected');
        return;
      }

      const response = await fetch(`${API_URL}/audit/variable-invoice/mappings`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${token}`,
        },
        body: JSON.stringify(mappingsToSave),
      });

      if (!response.ok) {
        throw new Error('Failed to save mappings');
      }

      const data = await response.json();
      onMessageChanged('success', `Saved ${data.count} mappings`);

      // Refresh audit report
      const auditResponse = await fetch(
        `${API_URL}/audit/variable-invoice/${invoiceNumber}`,
        {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
        }
      );

      if (auditResponse.ok) {
        const updatedReport = await auditResponse.json();
        setReport(updatedReport);
        setMappingResponses({});
      }
    } catch (error) {
      onMessageChanged(
        'error',
        error instanceof Error ? error.message : 'Error saving mappings'
      );
    } finally {
      setSavingMappings(false);
    }
  };

  const toggleRow = (normalized: string) => {
    const next = new Set(expandedRows);
    if (next.has(normalized)) {
      next.delete(normalized);
    } else {
      next.add(normalized);
    }
    setExpandedRows(next);
  };

  if (loading) {
    return (
      <div className="text-center py-12">
        <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-ndl-blue"></div>
        <p className="text-gray-600 mt-2">Loading audit report...</p>
      </div>
    );
  }

  if (!report) {
    return (
      <div className="bg-red-100 text-red-800 p-4 rounded-lg">
        Failed to load audit report
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {/* Header Summary */}
      <div className="bg-white rounded-lg shadow-md p-6">
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div>
            <p className="text-sm text-gray-600">Invoice #</p>
            <p className="text-lg font-mono font-bold text-gray-900">
              {report.invoice_number}
            </p>
          </div>
          <div>
            <p className="text-sm text-gray-600">Period</p>
            <p className="text-lg font-semibold text-gray-900">
              {new Date(report.period_start).toLocaleDateString()} -{' '}
              {new Date(report.period_end).toLocaleDateString()}
            </p>
          </div>
          <div>
            <p className="text-sm text-gray-600">Station</p>
            <p className="text-lg font-semibold text-gray-900">{report.station}</p>
          </div>
          <div>
            <p className="text-sm text-gray-600">Invoice Date</p>
            <p className="text-lg font-semibold text-gray-900">
              {new Date(report.invoice_date).toLocaleDateString()}
            </p>
          </div>
        </div>
      </div>

      {/* WST Metrics */}
      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-xl font-bold text-ndl-blue mb-4">WST Collected Metrics</h2>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <div className="bg-blue-50 p-4 rounded-lg">
            <p className="text-sm text-gray-600">Total Routes</p>
            <p className="text-3xl font-bold text-blue-600">{report.metrics.total_routes}</p>
            <p className="text-xs text-gray-500 mt-1">
              Weekly: {report.metrics.routes_from_weekly_report} | Service: {report.metrics.routes_from_service_details}
            </p>
          </div>
          <div className="bg-green-50 p-4 rounded-lg">
            <p className="text-sm text-gray-600">Training Eligible</p>
            <p className="text-3xl font-bold text-green-600">
              {report.metrics.training_eligible_total}
            </p>
          </div>
          <div className="bg-amber-50 p-4 rounded-lg">
            <p className="text-sm text-gray-600">Delivered Packages</p>
            <p className="text-3xl font-bold text-amber-600">
              {report.metrics.delivered_packages_total}
            </p>
          </div>
          <div className="bg-purple-50 p-4 rounded-lg">
            <p className="text-sm text-gray-600">Pickup Packages</p>
            <p className="text-3xl font-bold text-purple-600">
              {report.metrics.pickup_packages_total}
            </p>
          </div>
        </div>
      </div>

      {/* Line Item Comparisons */}
      <div className="bg-white rounded-lg shadow-md p-6">
        <h2 className="text-xl font-bold text-ndl-blue mb-4">
          Invoice Line Items vs WST Metrics
        </h2>

        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="bg-gray-100 border-b-2 border-gray-300">
                <th className="px-3 py-2 text-left font-semibold text-gray-700">Description</th>
                <th className="px-3 py-2 text-center font-semibold text-gray-700">Metric</th>
                <th className="px-3 py-2 text-right font-semibold text-gray-700">Qty</th>
                <th className="px-3 py-2 text-right font-semibold text-gray-700">Expected</th>
                <th className="px-3 py-2 text-right font-semibold text-gray-700">Delta</th>
                <th className="px-3 py-2 text-right font-semibold text-gray-700">Amount</th>
                <th className="px-3 py-2 text-center font-semibold text-gray-700">Status</th>
              </tr>
            </thead>
            <tbody>
              {report.line_item_comparisons.map((item, idx) => {
                const isDelta = item.quantity_delta && Math.abs(item.quantity_delta) > 0.5;
                const rowColor =
                  item.mapping_source === 'unmapped'
                    ? 'bg-red-50'
                    : isDelta
                    ? 'bg-yellow-50'
                    : 'bg-white';

                return (
                  <tr
                    key={idx}
                    className={`${rowColor} border-b border-gray-200 hover:shadow-sm transition`}
                  >
                    <td className="px-3 py-2 max-w-xs">
                      <button
                        onClick={() => toggleRow(item.normalized)}
                        className="text-blue-600 hover:underline text-left font-medium"
                      >
                        {expandedRows.has(item.normalized) ? '▼' : '▶'}{' '}
                        {item.description}
                      </button>
                    </td>
                    <td className="px-3 py-2 text-center font-mono text-xs">
                      {item.metric_key || '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-semibold">
                      {item.invoice_quantity}
                    </td>
                    <td className="px-3 py-2 text-right text-gray-600">
                      {item.expected_quantity !== undefined ? item.expected_quantity : '—'}
                    </td>
                    <td
                      className={`px-3 py-2 text-right font-bold ${
                        isDelta ? 'text-red-600' : 'text-green-600'
                      }`}
                    >
                      {item.quantity_delta !== undefined
                        ? (item.quantity_delta > 0 ? '+' : '') + item.quantity_delta.toFixed(1)
                        : '—'}
                    </td>
                    <td className="px-3 py-2 text-right font-semibold text-gray-900">
                      ${item.invoice_amount.toFixed(2)}
                    </td>
                    <td className="px-3 py-2 text-center">
                      {item.mapping_source === 'unmapped' && (
                        <span className="inline-block px-2 py-1 bg-red-200 text-red-800 rounded text-xs font-semibold">
                          Unmapped
                        </span>
                      )}
                      {item.mapping_source === 'saved' && (
                        <span className="inline-block px-2 py-1 bg-green-200 text-green-800 rounded text-xs font-semibold">
                          Saved
                        </span>
                      )}
                      {item.mapping_source === 'auto' && (
                        <span className="inline-block px-2 py-1 bg-blue-200 text-blue-800 rounded text-xs font-semibold">
                          Auto
                        </span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>

        {/* Expanded Row Details */}
        {Array.from(expandedRows).map((normalized) => {
          const item = report.line_item_comparisons.find((c) => c.normalized === normalized);
          if (!item) return null;

          return (
            <div key={normalized} className="mt-4 p-4 bg-gray-50 rounded-lg border border-gray-200">
              <p className="font-semibold text-gray-900">Details: {item.description}</p>
              <div className="mt-2 grid grid-cols-2 gap-2 text-sm text-gray-700">
                <div>
                  <p className="text-gray-600">Category:</p>
                  <p>{item.category}</p>
                </div>
                {item.subtype && (
                  <div>
                    <p className="text-gray-600">Subtype:</p>
                    <p>{item.subtype}</p>
                  </div>
                )}
                <div>
                  <p className="text-gray-600">Rate:</p>
                  <p>${item.invoice_rate.toFixed(2)}</p>
                </div>
                <div>
                  <p className="text-gray-600">Source:</p>
                  <p>{item.mapping_source}</p>
                </div>
              </div>
            </div>
          );
        })}
      </div>

      {/* Mapping Prompts */}
      {report.needs_prompt && report.needs_prompt.length > 0 && (
        <div className="bg-amber-50 rounded-lg shadow-md p-6 border-l-4 border-amber-500">
          <h2 className="text-xl font-bold text-amber-900 mb-4">
            🔔 {report.needs_prompt.length} Item(s) Need Mapping
          </h2>

          <div className="space-y-4">
            {report.needs_prompt.map((item, idx) => (
              <div
                key={idx}
                className="bg-white p-4 rounded-lg border border-amber-200"
              >
                <p className="font-semibold text-gray-900 mb-2">{item.description}</p>

                <div className="mb-3">
                  <label className="block text-sm font-medium text-gray-700 mb-2">
                    Assign to WST Metric:
                  </label>
                  <select
                    value={mappingResponses[item.normalized] || ''}
                    onChange={(e) =>
                      handleMappingChange(item.normalized, e.target.value)
                    }
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-amber-500"
                  >
                    <option value="">
                      {item.suggested_metric_key
                        ? `Select metric (suggested: ${item.suggested_metric_key})`
                        : 'Select metric...'}
                    </option>
                    {item.options.map((metricKey) => (
                      <option key={metricKey} value={metricKey}>
                        {metricKey} — {report.metric_options[metricKey]}
                      </option>
                    ))}
                  </select>
                </div>

                {item.suggested_metric_key && (
                  <p className="text-xs text-gray-500">
                    💡 Suggestion: {item.suggested_metric_key}
                  </p>
                )}
              </div>
            ))}
          </div>

          <button
            onClick={handleSaveMappings}
            disabled={savingMappings}
            className="mt-6 w-full px-4 py-3 bg-amber-500 hover:bg-amber-600 disabled:bg-gray-400 text-white font-semibold rounded-lg transition"
          >
            {savingMappings ? 'Saving...' : '💾 Save Mappings'}
          </button>
        </div>
      )}
    </div>
  );
}
