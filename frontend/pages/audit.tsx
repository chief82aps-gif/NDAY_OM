'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';
import VariableInvoiceAudit from '../components/VariableInvoiceAudit';

interface VariableInvoice {
  id: number;
  invoice_number: string;
  invoice_date: string;
  period_start: string;
  period_end: string;
  station: string;
  subtotal: number;
  total_due: number;
}

interface StatusMessage {
  type: 'success' | 'error' | 'info';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

function AuditPageContent() {
  const router = useRouter();
  const [invoices, setInvoices] = useState<VariableInvoice[]>([]);
  const [selectedInvoice, setSelectedInvoice] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<StatusMessage | null>(null);

  // Get token from localStorage
  const [token, setToken] = useState<string>('');

  useEffect(() => {
    const storedToken = localStorage.getItem('access_token') || '';
    setToken(storedToken);
  }, []);

  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 6000);
  };

  // Fetch list of variable invoices
  useEffect(() => {
    if (!token) return;

    const fetchInvoices = async () => {
      try {
        setLoading(true);
        const response = await fetch(`${API_URL}/audit/variable-invoices`, {
          method: 'GET',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${token}`,
          },
        });

        if (!response.ok) {
          throw new Error('Failed to load invoices');
        }

        const data = await response.json();
        setInvoices(data.invoices || []);
      } catch (error) {
        showMessage('error', error instanceof Error ? error.message : 'Error loading invoices');
      } finally {
        setLoading(false);
      }
    };

    fetchInvoices();
  }, [token]);

  return (
    <div className="min-h-screen bg-gray-50">
      <PageHeader title="Invoice Audit: Variable Invoice vs WST Data" />

      <div className="max-w-7xl mx-auto px-4 py-6">
        {/* Status Messages */}
        {message && (
          <div
            className={`mb-4 p-4 rounded-lg ${
              message.type === 'success'
                ? 'bg-green-100 text-green-800'
                : message.type === 'error'
                ? 'bg-red-100 text-red-800'
                : 'bg-blue-100 text-blue-800'
            }`}
          >
            {message.text}
          </div>
        )}

        {loading ? (
          <div className="text-center py-12">
            <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-ndl-blue"></div>
            <p className="text-gray-600 mt-2">Loading invoices...</p>
          </div>
        ) : !selectedInvoice ? (
          <div className="bg-white rounded-lg shadow-md p-6">
            <h2 className="text-xl font-bold text-ndl-blue mb-4">Select an Invoice to Audit</h2>

            {invoices.length === 0 ? (
              <div className="text-center text-gray-500 py-8">
                <p>No variable invoices found. Please upload invoices first.</p>
              </div>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse">
                  <thead>
                    <tr className="bg-gray-100 border-b-2 border-gray-300">
                      <th className="px-4 py-2 text-left font-semibold text-gray-700">Invoice #</th>
                      <th className="px-4 py-2 text-left font-semibold text-gray-700">Invoice Date</th>
                      <th className="px-4 py-2 text-left font-semibold text-gray-700">Period</th>
                      <th className="px-4 py-2 text-left font-semibold text-gray-700">Station</th>
                      <th className="px-4 py-2 text-right font-semibold text-gray-700">Total Due</th>
                      <th className="px-4 py-2 text-center font-semibold text-gray-700">Action</th>
                    </tr>
                  </thead>
                  <tbody>
                    {invoices.map((invoice, index) => (
                      <tr
                        key={invoice.id}
                        className={`${
                          index % 2 === 0 ? 'bg-white' : 'bg-gray-50'
                        } border-b border-gray-200 hover:bg-blue-50 transition`}
                      >
                        <td className="px-4 py-3 font-mono text-sm text-gray-900">
                          {invoice.invoice_number}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">
                          {new Date(invoice.invoice_date).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">
                          {new Date(invoice.period_start).toLocaleDateString()} -{' '}
                          {new Date(invoice.period_end).toLocaleDateString()}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-700">{invoice.station}</td>
                        <td className="px-4 py-3 text-right text-sm font-semibold text-gray-900">
                          ${invoice.total_due?.toFixed(2) || '0.00'}
                        </td>
                        <td className="px-4 py-3 text-center">
                          <button
                            onClick={() => setSelectedInvoice(invoice.invoice_number)}
                            className="px-3 py-1 bg-blue-500 hover:bg-blue-600 text-white text-sm rounded transition"
                          >
                            Audit
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        ) : (
          <>
            <button
              onClick={() => setSelectedInvoice(null)}
              className="mb-4 px-4 py-2 bg-gray-500 hover:bg-gray-600 text-white rounded-lg transition"
            >
              ← Back to Invoice List
            </button>
            <VariableInvoiceAudit
              invoiceNumber={selectedInvoice}
              token={token}
              onMessageChanged={showMessage}
            />
          </>
        )}
      </div>
    </div>
  );
}

export default function AuditPage() {
  return (
    <ProtectedRoute>
      <AuditPageContent />
    </ProtectedRoute>
  );
}
