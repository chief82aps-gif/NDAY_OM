'use client';

import { useMemo } from 'react';
import { useRouter } from 'next/router';
import PageHeader from './PageHeader';
import { useAuth } from '../contexts/AuthContext';
import { dashboardModules } from '../modules';

export default function Dashboard() {
  const router = useRouter();
  const { user } = useAuth();

  const visibleModules = useMemo(() => {
    const role = user?.role;
    return dashboardModules.filter((moduleItem) => {
      if (!moduleItem.allowedRoles || moduleItem.allowedRoles.length === 0) {
        return true;
      }
      if (!role) {
        return false;
      }
      return moduleItem.allowedRoles.includes(role);
    });
  }, [user?.role]);

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-50 to-blue-50">
      <PageHeader title="NDAY Route Manager" showBack={false} />

      <main className="max-w-6xl mx-auto px-4 py-10">
        <div className="mb-8 rounded-xl border border-blue-100 bg-white p-6 shadow-sm">
          <h2 className="text-3xl font-bold text-slate-900 mb-2">Operations Module Launcher</h2>
          <p className="text-slate-600">
            Each function is isolated as a separate module route so it can be updated independently.
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4 mb-10">
          {visibleModules.map((moduleItem) => (
            <button
              key={moduleItem.id}
              type="button"
              onClick={() => router.push(moduleItem.href)}
              className="w-full rounded-lg border border-slate-200 bg-white px-5 py-4 text-left shadow-sm transition hover:border-blue-300 hover:shadow-md"
            >
              <h3 className="text-lg font-semibold text-slate-900 mb-1">{moduleItem.title}</h3>
              <p className="text-sm text-slate-600">{moduleItem.description}</p>
            </button>
          ))}
        </div>

        <div className="rounded-lg border border-slate-200 bg-white p-6 text-sm text-slate-700">
          <h3 className="text-base font-semibold text-slate-900 mb-2">Module Notes</h3>
          <div className="space-y-1">
            <p>Invoice audit and daily scheduler are now first-class modules in the launcher.</p>
            <p>Role restricted modules stay hidden for users without access.</p>
            <p>To add a new module, add one file under frontend/modules and export it from frontend/modules/index.ts.</p>
          </div>
        </div>
      </main>

      <footer className="bg-slate-800 text-slate-300 py-6 mt-12">
        <div className="max-w-6xl mx-auto px-4 text-center">
          <p>&copy; 2026 NDAY Logistics. All rights reserved.</p>
        </div>
      </footer>
    </div>
  );
}
