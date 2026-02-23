'use client';

import Image from 'next/image';
import { useRouter } from 'next/router';
import { useAuth } from '../contexts/AuthContext';

interface PageHeaderProps {
  title: string;
  showBack?: boolean;
}

export default function PageHeader({ title, showBack = false }: PageHeaderProps) {
  const router = useRouter();
  const { user, logout } = useAuth();

  const handleHome = () => {
    router.push('/');
  };

  const handleBack = () => {
    router.back();
  };

  const handleLogout = () => {
    logout();
    router.push('/login');
  };

  return (
    <header className="bg-ndl-blue text-white py-4 shadow-md">
      <div className="max-w-7xl mx-auto px-4">
        <div className="flex items-center justify-between">
          {/* Left: Logo and Title */}
          <div className="flex items-center gap-3">
            <Image
              src="/logo.png"
              alt="NDL Logo"
              width={40}
              height={40}
              className="rounded-lg"
              priority
            />
            <div>
              <h1 className="text-2xl font-bold">{title}</h1>
              {user && <p className="text-sm text-blue-100">Welcome, {user.name}</p>}
            </div>
          </div>

          {/* Right: Navigation Buttons */}
          <div className="flex items-center gap-3">
            <button
              onClick={handleHome}
              className="bg-white text-ndl-blue px-4 py-2 rounded font-semibold hover:bg-blue-50 transition text-sm"
            >
              🏠 Home
            </button>

            {showBack && (
              <button
                onClick={handleBack}
                className="bg-white text-ndl-blue px-4 py-2 rounded font-semibold hover:bg-blue-50 transition text-sm"
              >
                ← Back
              </button>
            )}

            <button
              onClick={handleLogout}
              className="bg-red-600 text-white px-4 py-2 rounded font-semibold hover:bg-red-700 transition text-sm"
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    </header>
  );
}
