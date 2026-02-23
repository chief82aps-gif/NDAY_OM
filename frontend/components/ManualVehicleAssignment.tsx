'use client';

import React, { useState, useEffect } from 'react';

interface FailedRoute {
  route_code: string;
  service_type: string;
  driver_name?: string;
  wave_time?: string;
  available_vehicles: Array<{
    vehicle_name: string;
    vin: string;
    service_type: string;
  }>;
}

interface ManualVehicleAssignmentProps {
  failedRoutes: FailedRoute[];
  apiUrl: string;
  onAssignmentComplete: () => void;
  onClose: () => void;
}

export default function ManualVehicleAssignment({
  failedRoutes,
  apiUrl,
  onAssignmentComplete,
  onClose,
}: ManualVehicleAssignmentProps) {
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [isLoading, setIsLoading] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

  const handleSelectVehicle = (routeCode: string, vehicleVin: string) => {
    setAssignments((prev) => ({
      ...prev,
      [routeCode]: vehicleVin,
    }));
  };

  const handleAssignAll = async () => {
    const unassigned = failedRoutes.filter((route) => !assignments[route.route_code]);
    if (unassigned.length > 0) {
      setMessage({
        type: 'error',
        text: `Please select vehicles for all ${unassigned.length} failed routes`,
      });
      return;
    }

    setIsLoading(true);
    let successCount = 0;
    let failureCount = 0;

    for (const [routeCode, vehicleVin] of Object.entries(assignments)) {
      try {
        const response = await fetch(
          `${apiUrl}/upload/manual-assign-vehicle?route_code=${routeCode}&vehicle_vin=${vehicleVin}`,
          {
            method: 'POST',
          }
        );

        if (response.ok) {
          successCount++;
        } else {
          failureCount++;
        }
      } catch (error) {
        failureCount++;
      }
    }

    setIsLoading(false);

    if (failureCount === 0) {
      setMessage({
        type: 'success',
        text: `Successfully assigned ${successCount} routes. Click "Complete Assignment" to proceed with PDF generation.`,
      });
    } else {
      setMessage({
        type: 'error',
        text: `Assigned ${successCount} routes, but ${failureCount} failed. Please try again.`,
      });
    }
  };

  if (failedRoutes.length === 0) {
    return null;
  }

  return (
    <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
      <div className="bg-white rounded-lg shadow-2xl max-w-4xl w-full max-h-[90vh] overflow-y-auto">
        {/* Header */}
        <div className="sticky top-0 bg-ndl-blue text-white p-6 border-b border-gray-200">
          <h2 className="text-2xl font-bold">Manual Vehicle Assignment Required</h2>
          <p className="text-gray-100 mt-2">
            {failedRoutes.length} route(s) require manual vehicle selection before PDF generation
          </p>
        </div>

        {/* Content */}
        <div className="p-6">
          {message && (
            <div
              className={`mb-6 p-4 rounded-lg ${
                message.type === 'success'
                  ? 'bg-green-100 text-green-800 border border-green-300'
                  : 'bg-red-100 text-red-800 border border-red-300'
              }`}
            >
              {message.text}
            </div>
          )}

          {/* Routes List */}
          <div className="space-y-6">
            {failedRoutes.map((route) => (
              <div key={route.route_code} className="border border-gray-300 rounded-lg p-4 bg-gray-50">
                {/* Route Info */}
                <div className="flex justify-between items-start mb-4">
                  <div>
                    <h3 className="text-xl font-bold text-ndl-blue">{route.route_code}</h3>
                    {route.driver_name && (
                      <p className="text-gray-700">
                        <span className="font-semibold">Driver:</span> {route.driver_name}
                      </p>
                    )}
                    <p className="text-gray-700">
                      <span className="font-semibold">Service Type:</span> {route.service_type}
                    </p>
                    {route.wave_time && (
                      <p className="text-gray-700">
                        <span className="font-semibold">Wave:</span> {route.wave_time}
                      </p>
                    )}
                  </div>
                  <div
                    className={`px-3 py-1 rounded-full text-sm font-semibold ${
                      assignments[route.route_code]
                        ? 'bg-green-100 text-green-800'
                        : 'bg-yellow-100 text-yellow-800'
                    }`}
                  >
                    {assignments[route.route_code] ? '✓ Selected' : 'Pending'}
                  </div>
                </div>

                {/* Vehicle Selection */}
                <div className="mt-4">
                  <label className="block text-sm font-semibold text-gray-800 mb-2">
                    Select Vehicle:
                  </label>
                  <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                    {route.available_vehicles.length > 0 ? (
                      route.available_vehicles.map((vehicle) => (
                        <button
                          key={vehicle.vin}
                          onClick={() => handleSelectVehicle(route.route_code, vehicle.vin)}
                          className={`p-3 text-left rounded-lg border-2 transition ${
                            assignments[route.route_code] === vehicle.vin
                              ? 'border-ndl-blue bg-blue-50'
                              : 'border-gray-300 hover:border-ndl-blue'
                          }`}
                        >
                          <div className="font-semibold text-gray-900">{vehicle.vehicle_name}</div>
                          <div className="text-sm text-gray-600">VIN: {vehicle.vin}</div>
                          <div className="text-xs text-gray-500">{vehicle.service_type}</div>
                        </button>
                      ))
                    ) : (
                      <div className="col-span-2 p-3 bg-red-50 border border-red-300 rounded-lg text-red-800">
                        No available vehicles for this service type. This route cannot be assigned.
                      </div>
                    )}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>

        {/* Footer */}
        <div className="sticky bottom-0 bg-gray-100 border-t border-gray-300 p-6 flex justify-end gap-4">
          <button
            onClick={onClose}
            className="px-6 py-2 text-gray-800 bg-gray-300 hover:bg-gray-400 rounded-lg font-semibold transition"
          >
            Cancel
          </button>
          <button
            onClick={handleAssignAll}
            disabled={isLoading || failedRoutes.every((r) => !assignments[r.route_code])}
            className="px-6 py-2 text-white bg-ndl-blue hover:bg-blue-700 disabled:bg-gray-400 rounded-lg font-semibold transition"
          >
            {isLoading ? 'Assigning...' : 'Assign All Vehicles'}
          </button>
          {Object.keys(assignments).length === failedRoutes.length && (
            <button
              onClick={onAssignmentComplete}
              className="px-6 py-2 text-white bg-green-600 hover:bg-green-700 rounded-lg font-semibold transition"
            >
              Complete Assignment & Generate PDF
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
