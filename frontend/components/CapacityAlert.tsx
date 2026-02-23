import { useEffect, useState } from 'react';

interface CapacityAlert {
  service_type: string;
  total_bags: number;
  max_bags: number;
  percentage: number;
  message: string;
}

interface CapacityInfo {
  total_bags: number;
  max_bags: number;
  percentage: number;
  is_at_threshold: boolean;
  routes_assigned: number;
  bags_remaining: number;
}

interface CapacityStatus {
  by_service_type: { [key: string]: CapacityInfo };
  alerts: CapacityAlert[];
  has_alerts: boolean;
  alert_count: number;
}

interface CapacityAlertProps {
  apiUrl?: string;
}

export default function CapacityAlert({ apiUrl = 'http://127.0.0.1:8000' }: CapacityAlertProps) {
  const [capacityStatus, setCapacityStatus] = useState<CapacityStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [hasError, setHasError] = useState(false);

  const fetchCapacityStatus = async () => {
    try {
      setLoading(true);
      setHasError(false);
      const response = await fetch(`${apiUrl}/upload/capacity-status`);
      
      if (!response.ok) {
        throw new Error('Failed to fetch capacity status');
      }
      
      const data = await response.json();
      setCapacityStatus(data);
    } catch (error) {
      console.error('Error fetching capacity status:', error);
      setHasError(true);
    } finally {
      setLoading(false);
    }
  };

  // Fetch on mount and set up refresh interval
  useEffect(() => {
    fetchCapacityStatus();
    const interval = setInterval(fetchCapacityStatus, 10000); // Refresh every 10 seconds
    return () => clearInterval(interval);
  }, [apiUrl]);

  if (loading) {
    return (
      <div className="p-4 border border-gray-300 rounded-lg bg-gray-50">
        <div className="flex items-center justify-center">
          <div className="animate-spin rounded-full h-5 w-5 border-b-2 border-ndl-blue"></div>
          <span className="ml-2 text-gray-600">Loading capacity status...</span>
        </div>
      </div>
    );
  }

  if (hasError || !capacityStatus) {
    return null;
  }

  // If no alerts, don't show anything
  if (!capacityStatus.has_alerts) {
    return null;
  }

  return (
    <div className="space-y-4">
      {/* Alert Header */}
      <div className="bg-red-50 border-l-4 border-red-500 p-4 rounded-md">
        <div className="flex items-start">
          <div className="flex-shrink-0">
            <span className="text-2xl">⚠️</span>
          </div>
          <div className="ml-3 flex-1">
            <h3 className="text-lg font-semibold text-red-800">
              Van Capacity Alert
            </h3>
            <p className="text-sm text-red-700 mt-1">
              {capacityStatus.alert_count} service type{capacityStatus.alert_count > 1 ? 's' : ''} {capacityStatus.alert_count > 1 ? 'are' : 'is'} at 80% or more capacity
            </p>
          </div>
        </div>
      </div>

      {/* Individual Alerts */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        {capacityStatus.alerts.map((alert) => (
          <div key={alert.service_type} className="bg-orange-50 border border-orange-200 rounded-lg p-4">
            <h4 className="font-semibold text-orange-900 mb-2">{alert.service_type}</h4>
            
            {/* Progress Bar */}
            <div className="mb-3">
              <div className="flex justify-between text-sm text-orange-800 mb-1">
                <span>{alert.total_bags} bags</span>
                <span>{alert.percentage.toFixed(1)}%</span>
              </div>
              <div className="w-full bg-orange-100 rounded-full h-3 overflow-hidden">
                <div
                  className={`h-full transition-all duration-300 ${
                    alert.percentage >= 95
                      ? 'bg-red-600'
                      : alert.percentage >= 90
                      ? 'bg-red-500'
                      : 'bg-orange-500'
                  }`}
                  style={{ width: `${Math.min(alert.percentage, 100)}%` }}
                />
              </div>
            </div>

            {/* Details */}
            <div className="space-y-1 text-xs text-orange-700">
              <div className="flex justify-between">
                <span>Capacity:</span>
                <span className="font-mono">{alert.max_bags} bags</span>
              </div>
              <div className="flex justify-between">
                <span>Remaining:</span>
                <span className="font-mono text-red-600 font-semibold">
                  {Math.max(0, alert.max_bags - alert.total_bags)} bags
                </span>
              </div>
            </div>

            {/* Status Badge */}
            {alert.percentage >= 95 ? (
              <div className="mt-3 px-2 py-1 bg-red-600 text-white text-xs rounded font-semibold text-center">
                CRITICAL: At Max Capacity
              </div>
            ) : alert.percentage >= 90 ? (
              <div className="mt-3 px-2 py-1 bg-red-500 text-white text-xs rounded font-semibold text-center">
                Nearly Full
              </div>
            ) : (
              <div className="mt-3 px-2 py-1 bg-orange-500 text-white text-xs rounded font-semibold text-center">
                At Threshold (80%)
              </div>
            )}
          </div>
        ))}
      </div>

      {/* Capacity Summary Table */}
      {Object.keys(capacityStatus.by_service_type).length > 0 && (
        <div className="bg-white border border-gray-200 rounded-lg p-4 mt-4">
          <h4 className="font-semibold text-gray-800 mb-3">All Service Types Capacity</h4>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-gray-200 bg-gray-50">
                  <th className="text-left px-3 py-2 font-semibold text-gray-700">Service Type</th>
                  <th className="text-right px-3 py-2 font-semibold text-gray-700">Bags</th>
                  <th className="text-right px-3 py-2 font-semibold text-gray-700">Max</th>
                  <th className="text-right px-3 py-2 font-semibold text-gray-700">Usage</th>
                  <th className="text-right px-3 py-2 font-semibold text-gray-700">Routes</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(capacityStatus.by_service_type)
                  .sort(([, a], [, b]) => b.percentage - a.percentage)
                  .map(([serviceType, info]) => (
                    <tr
                      key={serviceType}
                      className={`border-b border-gray-100 ${
                        info.is_at_threshold ? 'bg-red-50' : ''
                      }`}
                    >
                      <td className="px-3 py-2 text-gray-800">{serviceType}</td>
                      <td className="text-right px-3 py-2 font-mono text-gray-700">
                        {info.total_bags}
                      </td>
                      <td className="text-right px-3 py-2 font-mono text-gray-700">
                        {info.max_bags}
                      </td>
                      <td className="text-right px-3 py-2">
                        <span
                          className={`font-semibold ${
                            info.is_at_threshold ? 'text-red-600' : 'text-gray-700'
                          }`}
                        >
                          {info.percentage.toFixed(1)}%
                        </span>
                      </td>
                      <td className="text-right px-3 py-2 text-gray-700">
                        {info.routes_assigned}
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
