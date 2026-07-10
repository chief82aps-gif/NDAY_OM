'use client';

import { useState } from 'react';
import UploadZone from '../components/UploadZone';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';

interface StatusMessage {
  type: 'success' | 'error' | 'info';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8001';

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

export default function InvoiceIngestPage() {
  const [isLoading, setIsLoading] = useState(false);
  const [messages, setMessages] = useState<StatusMessage[]>([]);

  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    setMessages([{ type, text }]);
  };

  const handleUpload = async (endpoint: string, files: File[]) => {
    setIsLoading(true);
    try {
      const formData = new FormData();
      files.forEach((file) => formData.append('file', file));

      const token = localStorage.getItem('access_token');
      const response = await fetch(`${API_URL}/upload${endpoint}`, {
        method: 'POST',
        body: formData,
        headers: {
          ...(token && { Authorization: `Bearer ${token}` }),
        },
      });

      if (!response.ok) {
        throw new Error(await formatApiError(response, `Upload failed for ${endpoint}`));
      }

      const result = await response.json();
      showMessage('success', `${endpoint.slice(1)} uploaded successfully (${result.filename || 'file'})`);
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Upload failed');
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-white">
        <PageHeader title="Invoice Ingest Tools" showBack={true} />

        <main className="max-w-6xl mx-auto px-4 py-8">
          {messages.length > 0 && (
            <div className="mb-6 space-y-2">
              {messages.map((message, idx) => (
                <div
                  key={`${message.type}-${idx}`}
                  className={`rounded-lg p-3 text-sm ${
                    message.type === 'success'
                      ? 'bg-green-50 border border-green-200 text-green-800'
                      : message.type === 'error'
                        ? 'bg-red-50 border border-red-200 text-red-800'
                        : 'bg-blue-50 border border-blue-200 text-blue-800'
                  }`}
                >
                  {message.text}
                </div>
              ))}
            </div>
          )}

          <div className="bg-white rounded-lg shadow-md p-6 border border-gray-200">
            <h2 className="text-xl font-bold text-ndl-blue mb-4">Invoice Uploads</h2>
            <p className="text-sm text-gray-600 mb-6">
              Upload variable, fleet, and weekly incentive invoice documents in one dedicated location.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">Variable Invoice</label>
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
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">Fleet Invoice</label>
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
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-700 mb-2">Weekly Incentive</label>
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
              </div>
            </div>

            {isLoading && <p className="text-sm text-gray-600 mt-4">Uploading...</p>}
          </div>
        </main>
      </div>
    </ProtectedRoute>
  );
}
