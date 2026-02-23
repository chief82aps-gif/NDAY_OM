import React, { useState, useEffect } from 'react';

interface Violation {
  route_code: string;
  van_name: string;
  van_type: string;
  route_type: string;
  message: string;
}

interface ViolationsResponse {
  violations: Violation[];
  pending_violations: Violation[];
  authorized_violations: Violation[];
  authorized_routes: string[];
  total_violations: number;
  pending_count: number;
  has_pending: boolean;
}

interface ElectricVanViolationsAlertProps {
  refreshTrigger?: number;
}

const ElectricVanViolationsAlert: React.FC<ElectricVanViolationsAlertProps> = ({ refreshTrigger = 0 }) => {
  const [violations, setViolations] = useState<ViolationsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [approvingRoute, setApprovingRoute] = useState<string | null>(null);
  const [expandedRoutes, setExpandedRoutes] = useState<Set<string>>(new Set());

  useEffect(() => {
    fetchViolations();
  }, [refreshTrigger]);

  const fetchViolations = async () => {
    try {
      setLoading(true);
      const response = await fetch('/upload/electric-van-violations');
      if (response.ok) {
        const data = await response.json();
        setViolations(data);
      }
    } catch (error) {
      console.error('Failed to fetch electric van violations:', error);
    } finally {
      setLoading(false);
    }
  };

  const handleApproveViolation = async (violation: Violation) => {
    try {
      setApprovingRoute(violation.route_code);
      const response = await fetch(`/upload/authorize-electric-van`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          route_code: violation.route_code,
          van_vin: violation.van_name,
          reason: 'User approved via upload workflow',
        }),
      });

      if (response.ok) {
        await fetchViolations();
      } else {
        console.error('Failed to approve violation');
      }
    } catch (error) {
      console.error('Error approving violation:', error);
    } finally {
      setApprovingRoute(null);
    }
  };

  const toggleExpanded = (routeCode: string) => {
    const newExpanded = new Set(expandedRoutes);
    if (newExpanded.has(routeCode)) {
      newExpanded.delete(routeCode);
    } else {
      newExpanded.add(routeCode);
    }
    setExpandedRoutes(newExpanded);
  };

  if (!violations || violations.total_violations === 0) {
    return null;
  }

  const { pending_violations, authorized_violations } = violations;

  return (
    <div className="mt-6 p-4 border-l-4 border-amber-500 bg-amber-50 rounded-md">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h3 className="text-lg font-semibold text-amber-900 flex items-center gap-2">
            ⚡ Electric Van Constraints
            <span className="text-sm font-normal text-amber-700 bg-amber-200 px-2 py-1 rounded">
              {violations.total_violations} violation{violations.total_violations !== 1 ? 's' : ''}
            </span>
          </h3>
          <p className="text-sm text-amber-800 mt-1">
            Electric vans cannot be used on non-electric routes without approval.
          </p>
        </div>
      </div>

      {pending_violations && pending_violations.length > 0 && (
        <div className="mb-4">
          <h4 className="text-sm font-semibold text-amber-900 mb-2">
            Pending Approval ({pending_violations.length})
          </h4>
          <div className="space-y-2">
            {pending_violations.map((violation) => (
              <div
                key={violation.route_code}
                className="bg-white p-3 rounded border border-amber-200 hover:border-amber-400 transition-colors"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2">
                      <button
                        onClick={() => toggleExpanded(violation.route_code)}
                        className="text-amber-600 hover:text-amber-700 font-semibold text-sm"
                      >
                        {expandedRoutes.has(violation.route_code) ? '▼' : '▶'} {violation.route_code}
                      </button>
                      <span className="text-xs bg-amber-100 text-amber-800 px-2 py-1 rounded">
                        {violation.van_type}
                      </span>
                    </div>
                    {expandedRoutes.has(violation.route_code) && (
                      <div className="ml-4 text-sm text-gray-600 space-y-1 mb-2">
                        <div>
                          <span className="font-semibold">Van:</span> {violation.van_name}
                        </div>
                        <div>
                          <span className="font-semibold">Route Type:</span> {violation.route_type}
                        </div>
                        <div>
                          <span className="font-semibold">Issue:</span> {violation.message}
                        </div>
                      </div>
                    )}
                  </div>
                  <button
                    onClick={() => handleApproveViolation(violation)}
                    disabled={approvingRoute === violation.route_code}
                    className="px-3 py-2 bg-amber-600 hover:bg-amber-700 disabled:bg-amber-400 text-white text-sm font-medium rounded transition-colors whitespace-nowrap"
                  >
                    {approvingRoute === violation.route_code ? 'Approving...' : 'Approve'}
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {authorized_violations && authorized_violations.length > 0 && (
        <div className="mt-4">
          <h4 className="text-sm font-semibold text-green-900 mb-2">
            Approved ({authorized_violations.length})
          </h4>
          <div className="space-y-2">
            {authorized_violations.map((violation) => (
              <div
                key={violation.route_code}
                className="bg-green-50 p-3 rounded border border-green-200"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2">
                      <span className="text-green-700 font-semibold text-sm">✓ {violation.route_code}</span>
                      <span className="text-xs bg-green-100 text-green-800 px-2 py-1 rounded">
                        {violation.van_type}
                      </span>
                    </div>
                    <p className="text-xs text-green-700 mt-1">{violation.van_name}</p>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="mt-4 p-3 bg-amber-100 rounded text-sm text-amber-800">
        <p className="font-semibold mb-1">Note:</p>
        <p>
          Approving an electric van assignment allows the exception for that specific route only. 
          {violations.pending_count > 0 && (
            <span className="block mt-1 text-amber-900">
              You must approve all {violations.pending_count} violation{violations.pending_count !== 1 ? 's' : ''} before generating handouts.
            </span>
          )}
        </p>
      </div>
    </div>
  );
};

export default ElectricVanViolationsAlert;
