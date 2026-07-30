'use client';

import { ReactNode } from 'react';
import { useRouter } from 'next/router';
import { useAuth } from '../contexts/AuthContext';

interface ProtectedRouteProps {
  children: ReactNode;
  allowedRoles?: string[];
}

export function ProtectedRoute({ children, allowedRoles }: ProtectedRouteProps) {
  const router = useRouter();
  const { user, isAuthenticated, isLoading } = useAuth();

  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="inline-block animate-spin rounded-full h-12 w-12 border-b-2 border-ndl-blue"></div>
          <p className="mt-4 text-gray-600">Loading...</p>
        </div>
      </div>
    );
  }

  if (!isAuthenticated) {
    // Preserve where they were headed — added 2026-07-30 so bouncing
    // through /login (e.g. an expired/missing session) lands back on the
    // originally requested page afterward, not always the home page.
    router.push(`/login?redirect=${encodeURIComponent(router.asPath)}`);
    return null;
  }

  if (allowedRoles && allowedRoles.length > 0 && !(user?.role && allowedRoles.includes(user.role))) {
    router.push('/');
    return null;
  }

  return <>{children}</>;
}

export default ProtectedRoute;
