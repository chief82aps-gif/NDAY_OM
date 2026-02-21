'use client';

import { useRouter } from 'next/router';
import PageHeader from './PageHeader';

interface FeatureCard {
  id: string;
  title: string;
  description: string;
  icon: string;
  href: string;
  color: string;
}

const FEATURES: FeatureCard[] = [
  {
    id: 'driver-assignment',
    title: 'Daily Driver Assignment',
    description: 'Upload DOP, Fleet, Cortex, and Route Sheets to generate driver handouts with route assignments and vehicle allocations.',
    icon: '📋',
    href: '/upload',
    color: 'from-blue-500 to-blue-600',
  },
  {
    id: 'database',
    title: 'Assignment Database',
    description: 'View, search, and analyze all historical assignment records. Filter by driver, route, or vehicle and export data to CSV.',
    icon: '📊',
    href: '/database',
    color: 'from-purple-500 to-purple-600',
  },
  {
    id: 'reporting',
    title: 'Reports & Analytics',
    description: 'View detailed reports on vehicle utilization, driver assignments, route performance, and delivery metrics.',
    icon: '📈',
    href: '#',
    color: 'from-green-500 to-green-600',
  },
];

export default function Dashboard() {
  const router = useRouter();

  const handleFeatureClick = (href: string) => {
    if (href !== '#') {
      router.push(href);
    }
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-gray-50 to-gray-100">
      {/* Header */}
      <PageHeader title="NDAY Route Manager" showBack={false} />

      {/* Main Content */}
      <main className="max-w-6xl mx-auto px-4 py-12">
        {/* Welcome Section */}
        <div className="mb-12">
          <h2 className="text-3xl font-bold text-gray-800 mb-2">Welcome to NDAY Route Manager</h2>
          <p className="text-gray-600 text-lg">
            Streamline your delivery operations with intelligent route planning and driver assignment.
          </p>
        </div>

        {/* Feature Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6 mb-12">
          {FEATURES.map((feature) => (
            <div
              key={feature.id}
              onClick={() => handleFeatureClick(feature.href)}
              className={`bg-white rounded-lg shadow-md hover:shadow-lg transition-all duration-300 overflow-hidden cursor-pointer transform hover:scale-105 ${
                feature.href === '#' ? 'opacity-75 cursor-not-allowed' : ''
              }`}
            >
              {/* Color Header */}
              <div className={`bg-gradient-to-r ${feature.color} h-2`} />

              {/* Content */}
              <div className="p-6">
                <div className="text-5xl mb-4">{feature.icon}</div>
                <h3 className="text-xl font-bold text-gray-800 mb-2">{feature.title}</h3>
                <p className="text-gray-600 text-sm leading-relaxed mb-4">{feature.description}</p>

                {/* Coming Soon Badge */}
                {feature.href === '#' && (
                  <div className="inline-block bg-yellow-100 text-yellow-800 px-3 py-1 rounded-full text-xs font-semibold">
                    Coming Soon
                  </div>
                )}

                {/* Arrow for Active Features */}
                {feature.href !== '#' && (
                  <div className="text-ndl-blue font-semibold text-sm flex items-center">
                    Access Feature →
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>

        {/* Info Section */}
        <div className="bg-blue-50 border border-blue-200 rounded-lg p-8 mb-8">
          <h3 className="text-xl font-bold text-ndl-blue mb-4">Getting Started</h3>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div>
              <div className="text-3xl font-bold text-ndl-blue mb-2">1</div>
              <h4 className="font-semibold text-gray-800 mb-2">Daily Driver Assignment</h4>
              <p className="text-gray-600 text-sm">
                Upload your operational data files and generate professional PDF handouts with driver assignments.
              </p>
            </div>
            <div>
              <div className="text-3xl font-bold text-ndl-blue mb-2">2</div>
              <h4 className="font-semibold text-gray-800 mb-2">View Your Data</h4>
              <p className="text-gray-600 text-sm">
                Search, filter, and analyze all historical assignments in the database. Export to CSV anytime.
              </p>
            </div>
            <div>
              <div className="text-3xl font-bold text-ndl-blue mb-2">3</div>
              <h4 className="font-semibold text-gray-800 mb-2">Generate Reports</h4>
              <p className="text-gray-600 text-sm">
                Create detailed analytics on vehicle utilization, performance metrics, and delivery insights.
              </p>
            </div>
          </div>
        </div>
      </main>

      {/* Footer */}
      <footer className="bg-gray-800 text-gray-400 py-6 mt-12">
        <div className="max-w-6xl mx-auto px-4 text-center">
          <p>&copy; 2026 NDAY Logistics. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
