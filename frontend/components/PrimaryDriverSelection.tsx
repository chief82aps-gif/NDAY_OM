'use client';

import React, { useEffect, useMemo, useState } from 'react';

interface MultiDriverRoute {
  route_code: string;
  driver_name: string;
  wave_time?: string;
  vehicle_name?: string;
}

interface PrimaryDriverSelectionProps {
  routes: MultiDriverRoute[];
  apiUrl: string;
  onComplete: () => void;
  onClose: () => void;
}

const splitDriverNames = (driverName: string): string[] => {
  const normalized = driverName
    .replace(/\s+and\s+/gi, '|')
    .replace(/\s*&\s*/g, '|')
    .replace(/\s*\/\s*/g, '|')
    .replace(/\s*,\s*/g, '|')
    .replace(/\s*;\s*/g, '|')
    .replace(/\s*\+\s*/g, '|')
    .replace(/\s*\|\s*/g, '|');

  return normalized
    .split('|')
    .map((name) => name.trim())
    .filter((name) => name.length > 0);
};

export default function PrimaryDriverSelection({
  routes,
  apiUrl,
  onComplete,
  onClose,
}: PrimaryDriverSelectionProps) {
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const defaultSelections = useMemo(() => {
    const selections: Record<string, string> = {};
    routes.forEach((route) => {
      const options = splitDriverNames(route.driver_name);
      selections[route.route_code] = options[0] || route.driver_name || '';
    });
    return selections;
  }, [routes]);

  const [selections, setSelections] = useState<Record<string, string>>(defaultSelections);

  useEffect(() => {
    setSelections(defaultSelections);
  }, [defaultSelections]);

  const handleChange = (routeCode: string, driverName: string) => {
    setSelections((prev) => ({
      ...prev,
      [routeCode]: driverName,
    }));
  };

  const handleSave = async () => {
    setIsSaving(true);
    setError(null);

    try {
      const token = localStorage.getItem('access_token');
      const headers = token ? { 'Authorization': `Bearer ${token}` } : {};

      for (const route of routes) {
        const selected = selections[route.route_code] || defaultSelections[route.route_code];
        const url = `${apiUrl}/upload/primary-driver?route_code=${encodeURIComponent(route.route_code)}&driver_name=${encodeURIComponent(selected)}`;
        const response = await fetch(url, {
          method: 'POST',
          headers,
        });

        if (!response.ok) {
          throw new Error(`Failed to update ${route.route_code}`);
        }
      }

      onComplete();
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save primary drivers');
    } finally {
      setIsSaving(false);
    }
  };

  if (routes.length === 0) {
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-y-auto">
        <div className="sticky top-0 bg-ndl-blue text-white p-6 border-b border-gray-200">
          <h2 className="text-2xl font-bold">Select Primary Drivers</h2>
          <p className="text-gray-100 mt-2">
            Choose the primary driver for routes with multiple assignments.
          </p>
        </div>

        <div className="p-6">
          {error && (
            <div className="mb-6 p-4 rounded-lg bg-red-100 text-red-800 border border-red-300">
              {error}
            </div>
          )}

          <div className="space-y-4">
            {routes.map((route) => {
              const options = splitDriverNames(route.driver_name);
              return (
                <div key={route.route_code} className="border border-gray-300 rounded-lg p-4">
                  <div className="flex flex-col gap-1 mb-3">
                    <span className="text-lg font-semibold text-ndl-blue">{route.route_code}</span>
                    {route.wave_time && (
                      <span className="text-sm text-gray-600">Wave: {route.wave_time}</span>
                    )}
                    {route.vehicle_name && (
                      <span className="text-sm text-gray-600">Vehicle: {route.vehicle_name}</span>
                    )}
                  </div>

                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Primary Driver
                  </label>
                  <select
                    value={selections[route.route_code] || ''}
                    onChange={(e) => handleChange(route.route_code, e.target.value)}
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                  >
                    {options.map((name) => (
                      <option key={name} value={name}>
                        {name}
                      </option>
                    ))}
                  </select>
                  <p className="mt-2 text-xs text-gray-500">Default is the first name in the list.</p>
                </div>
              );
            })}
          </div>
        </div>

        <div className="sticky bottom-0 bg-gray-100 border-t border-gray-300 p-6 flex justify-end gap-3">
          <button
            onClick={onClose}
            className="px-5 py-2 text-gray-800 bg-gray-300 hover:bg-gray-400 rounded-lg font-semibold transition"
          >
            Close
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving}
            className="px-5 py-2 text-white bg-ndl-blue hover:bg-blue-700 rounded-lg font-semibold transition disabled:bg-gray-400"
          >
            {isSaving ? 'Saving...' : 'Save Primary Drivers'}
          </button>
        </div>
      </div>
    </div>
  );
}
