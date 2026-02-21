'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/router';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';

interface Assignment {
  id: string;
  route_code: string;
  driver_name: string;
  vehicle_name: string;
  wave_time: string;
  service_type: string;
  dsp: string;
  assignment_date: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function Database() {
  const router = useRouter();
  const [assignments, setAssignments] = useState<Assignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedDate, setSelectedDate] = useState<string>(new Date().toISOString().split('T')[0]);
  const [searchText, setSearchText] = useState('');
  const [filterType, setFilterType] = useState<'all' | 'driver' | 'route' | 'van'>('all');

  useEffect(() => {
    // In a real implementation, fetch from database
    // For now, load from local storage or session
    const storedAssignments = sessionStorage.getItem('assignments');
    if (storedAssignments) {
      try {
        setAssignments(JSON.parse(storedAssignments));
      } catch (e) {
        console.error('Failed to load assignments:', e);
      }
    }
    setLoading(false);
  }, [selectedDate]);

  const filteredAssignments = assignments.filter((a) => {
    if (!searchText) return true;
    const searchLower = searchText.toLowerCase();
    
    switch (filterType) {
      case 'driver':
        return a.driver_name.toLowerCase().includes(searchLower);
      case 'route':
        return a.route_code.toLowerCase().includes(searchLower);
      case 'van':
        return a.vehicle_name.toLowerCase().includes(searchLower);
      default:
        return (
          a.driver_name.toLowerCase().includes(searchLower) ||
          a.route_code.toLowerCase().includes(searchLower) ||
          a.vehicle_name.toLowerCase().includes(searchLower)
        );
    }
  });

  const stats = {
    totalRoutes: filteredAssignments.length,
    totalDrivers: new Set(filteredAssignments.map(a => a.driver_name)).size,
    totalVehicles: new Set(filteredAssignments.map(a => a.vehicle_name)).size,
    serviceTypes: new Set(filteredAssignments.map(a => a.service_type)).size,
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
      {/* Header */}
      <PageHeader title="Assignment Database" showBack={true} />

      {/* Main Content */}
      <main className="max-w-7xl mx-auto px-4 py-8">
        {/* Stats Section */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-3xl font-bold text-ndl-blue">{stats.totalRoutes}</div>
            <div className="text-gray-600 text-sm mt-1">Total Routes</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-3xl font-bold text-purple-600">{stats.totalDrivers}</div>
            <div className="text-gray-600 text-sm mt-1">Unique Drivers</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-3xl font-bold text-green-600">{stats.totalVehicles}</div>
            <div className="text-gray-600 text-sm mt-1">Unique Vehicles</div>
          </div>
          <div className="bg-white rounded-lg shadow p-6">
            <div className="text-3xl font-bold text-orange-600">{stats.serviceTypes}</div>
            <div className="text-gray-600 text-sm mt-1">Service Types</div>
          </div>
        </div>

        {/* Filters Section */}
        <div className="bg-white rounded-lg shadow p-6 mb-8">
          <h2 className="text-xl font-bold text-gray-800 mb-4">Search & Filter</h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div>
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Filter By
              </label>
              <select
                value={filterType}
                onChange={(e) => setFilterType(e.target.value as typeof filterType)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
              >
                <option value="all">All Fields</option>
                <option value="driver">Driver Name</option>
                <option value="route">Route Code</option>
                <option value="van">Vehicle Name</option>
              </select>
            </div>
            <div className="md:col-span-2">
              <label className="block text-sm font-semibold text-gray-700 mb-2">
                Search
              </label>
              <input
                type="text"
                placeholder="Search assignments..."
                value={searchText}
                onChange={(e) => setSearchText(e.target.value)}
                className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
              />
            </div>
          </div>
        </div>

        {/* Results Section */}
        <div className="bg-white rounded-lg shadow overflow-hidden">
          <div className="px-6 py-4 border-b border-gray-200">
            <h2 className="text-xl font-bold text-gray-800">
              Assignments ({filteredAssignments.length})
            </h2>
          </div>

          {loading ? (
            <div className="p-8 text-center text-gray-500">Loading assignments...</div>
          ) : filteredAssignments.length === 0 ? (
            <div className="p-8 text-center text-gray-500">
              No assignments found. Upload data and generate assignments to populate the database.
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead className="bg-gray-50 border-b border-gray-200">
                  <tr>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Route</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Driver</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Vehicle</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Wave</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">Service Type</th>
                    <th className="px-6 py-3 text-left text-sm font-semibold text-gray-700">DSP</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredAssignments.map((a, idx) => (
                    <tr
                      key={idx}
                      className={idx % 2 === 0 ? 'bg-white' : 'bg-gray-50'}
                    >
                      <td className="px-6 py-3 text-sm font-medium text-ndl-blue">{a.route_code}</td>
                      <td className="px-6 py-3 text-sm text-gray-700">{a.driver_name}</td>
                      <td className="px-6 py-3 text-sm text-gray-700">{a.vehicle_name}</td>
                      <td className="px-6 py-3 text-sm text-gray-700">{a.wave_time}</td>
                      <td className="px-6 py-3 text-sm text-gray-700">{a.service_type}</td>
                      <td className="px-6 py-3 text-sm text-gray-700">{a.dsp}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Export Section */}
        {filteredAssignments.length > 0 && (
          <div className="mt-8 flex gap-4">
            <button
              onClick={() => {
                const csv = [
                  ['Route Code', 'Driver Name', 'Vehicle Name', 'Wave Time', 'Service Type', 'DSP'],
                  ...filteredAssignments.map(a => [
                    a.route_code,
                    a.driver_name,
                    a.vehicle_name,
                    a.wave_time,
                    a.service_type,
                    a.dsp,
                  ]),
                ]
                  .map(row => row.join(','))
                  .join('\n');

                const blob = new Blob([csv], { type: 'text/csv' });
                const url = window.URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.href = url;
                a.download = `assignments-${selectedDate}.csv`;
                document.body.appendChild(a);
                a.click();
                window.URL.revokeObjectURL(url);
                document.body.removeChild(a);
              }}
              className="btn-primary"
            >
              Export to CSV
            </button>
          </div>
        )}
      </main>

      {/* Footer */}
      <footer className="bg-gray-800 text-gray-400 py-6 mt-12">
        <div className="max-w-7xl mx-auto px-4 text-center">
          <p>&copy; 2026 NDAY Logistics. All rights reserved.</p>
        </div>
      </footer>
      </div>
    </ProtectedRoute>
  );
}
