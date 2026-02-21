'use client';

import { useState, useEffect } from 'react';
import PageHeader from '../components/PageHeader';
import { ProtectedRoute } from '../components/ProtectedRoute';
import { useAuth } from '../contexts/AuthContext';
import { useRouter } from 'next/router';

interface User {
  username: string;
  name: string;
}

interface StatusMessage {
  type: 'success' | 'error' | 'info';
  text: string;
}

const API_URL = process.env.NEXT_PUBLIC_API_URL || 'http://127.0.0.1:8000';

export default function AdminPage() {
  const { user } = useAuth();
  const router = useRouter();

  const [users, setUsers] = useState<User[]>([]);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<StatusMessage | null>(null);

  // Form states
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [adminPassword, setAdminPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Load users on mount
  useEffect(() => {
    loadUsers();
  }, []);

  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 5000);
  };

  const loadUsers = async () => {
    if (!user) return;

    try {
      setLoading(true);
      const response = await fetch(`${API_URL}/auth/list-users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: user.username,
          password: adminPassword || user.username, // Use admin password if provided
        }),
      });

      if (!response.ok) {
        if (response.status === 401) {
          showMessage('error', 'Invalid admin credentials');
        } else {
          throw new Error('Failed to load users');
        }
        return;
      }

      const data = await response.json();
      setUsers(data.users);
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to load users');
    } finally {
      setLoading(false);
    }
  };

  const handleCreateUser = async (e: React.FormEvent) => {
    e.preventDefault();

    // Validate inputs
    if (!newUsername.trim()) {
      showMessage('error', 'Username is required');
      return;
    }

    if (newUsername.length < 3) {
      showMessage('error', 'Username must be at least 3 characters');
      return;
    }

    if (!newPassword) {
      showMessage('error', 'Password is required');
      return;
    }

    if (newPassword.length < 6) {
      showMessage('error', 'Password must be at least 6 characters');
      return;
    }

    if (newPassword !== confirmPassword) {
      showMessage('error', 'Passwords do not match');
      return;
    }

    if (!adminPassword) {
      showMessage('error', 'Admin password is required');
      return;
    }

    setIsSubmitting(true);

    try {
      const response = await fetch(`${API_URL}/auth/create-user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: newUsername.toLowerCase(),
          password: newPassword,
          admin_username: user?.username,
          admin_password: adminPassword,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to create user');
      }

      showMessage('success', `User '${newUsername}' created successfully`);
      setNewUsername('');
      setNewPassword('');
      setConfirmPassword('');
      setAdminPassword('');

      // Reload users
      await loadUsers();
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to create user');
    } finally {
      setIsSubmitting(false);
    }
  };

  const handleDeleteUser = async (usernameToDelete: string) => {
    if (!confirm(`Are you sure you want to delete user '${usernameToDelete}'?`)) {
      return;
    }

    if (!adminPassword) {
      showMessage('error', 'Admin password is required to delete users');
      return;
    }

    try {
      const response = await fetch(`${API_URL}/auth/delete-user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: usernameToDelete,
          password: '', // Not used for delete
          admin_username: user?.username,
          admin_password: adminPassword,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete user');
      }

      showMessage('success', `User '${usernameToDelete}' deleted successfully`);
      await loadUsers();
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to delete user');
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Admin Panel" showBack={true} />

        <main className="max-w-4xl mx-auto px-4 py-8">
          {/* Message Alert */}
          {message && (
            <div
              className={`mb-6 p-4 rounded-lg ${
                message.type === 'success'
                  ? 'bg-green-100 text-green-800 border border-green-300'
                  : message.type === 'error'
                  ? 'bg-red-100 text-red-800 border border-red-300'
                  : 'bg-blue-100 text-blue-800 border border-blue-300'
              }`}
            >
              {message.text}
            </div>
          )}

          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            {/* Create User Form */}
            <div className="lg:col-span-1">
              <div className="bg-white rounded-lg shadow-md p-6">
                <h2 className="text-xl font-bold text-ndl-blue mb-4">Create New User</h2>

                <form onSubmit={handleCreateUser} className="space-y-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">
                      Username
                    </label>
                    <input
                      type="text"
                      value={newUsername}
                      onChange={(e) => setNewUsername(e.target.value)}
                      placeholder="e.g., supervisor"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                      disabled={isSubmitting}
                    />
                    <p className="text-xs text-gray-500 mt-1">Min 3 characters, lowercase</p>
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">
                      Password
                    </label>
                    <input
                      type="password"
                      value={newPassword}
                      onChange={(e) => setNewPassword(e.target.value)}
                      placeholder="Min 6 characters"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                      disabled={isSubmitting}
                    />
                  </div>

                  <div>
                    <label className="block text-sm font-semibold text-gray-700 mb-1">
                      Confirm Password
                    </label>
                    <input
                      type="password"
                      value={confirmPassword}
                      onChange={(e) => setConfirmPassword(e.target.value)}
                      placeholder="Confirm password"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                      disabled={isSubmitting}
                    />
                  </div>

                  <div className="border-t pt-4">
                    <label className="block text-sm font-semibold text-gray-700 mb-1">
                      Your Admin Password
                    </label>
                    <input
                      type="password"
                      value={adminPassword}
                      onChange={(e) => setAdminPassword(e.target.value)}
                      placeholder="Required to create users"
                      className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                      disabled={isSubmitting}
                    />
                    <p className="text-xs text-gray-500 mt-1">Admin password for verification</p>
                  </div>

                  <button
                    type="submit"
                    disabled={isSubmitting}
                    className="w-full bg-ndl-blue text-white py-2 rounded-lg font-semibold hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isSubmitting ? 'Creating...' : 'Create User'}
                  </button>
                </form>
              </div>
            </div>

            {/* Users List */}
            <div className="lg:col-span-2">
              <div className="bg-white rounded-lg shadow-md p-6">
                <h2 className="text-xl font-bold text-ndl-blue mb-4">System Users</h2>

                {loading ? (
                  <div className="text-center py-8">
                    <div className="inline-block animate-spin rounded-full h-8 w-8 border-b-2 border-ndl-blue"></div>
                    <p className="mt-2 text-gray-600">Loading users...</p>
                  </div>
                ) : users.length === 0 ? (
                  <div className="text-center py-8 text-gray-500">
                    <p>No users found</p>
                  </div>
                ) : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead>
                        <tr className="border-b border-gray-200">
                          <th className="text-left py-2 px-4 font-semibold text-gray-700">
                            Username
                          </th>
                          <th className="text-left py-2 px-4 font-semibold text-gray-700">
                            Display Name
                          </th>
                          <th className="text-right py-2 px-4 font-semibold text-gray-700">
                            Actions
                          </th>
                        </tr>
                      </thead>
                      <tbody>
                        {users.map((u) => (
                          <tr key={u.username} className="border-b border-gray-100 hover:bg-gray-50">
                            <td className="py-3 px-4 font-mono text-gray-800">{u.username}</td>
                            <td className="py-3 px-4 text-gray-700">{u.name}</td>
                            <td className="py-3 px-4 text-right">
                              {u.username === 'admin' ? (
                                <span className="text-xs text-gray-500">Protected</span>
                              ) : (
                                <button
                                  onClick={() => handleDeleteUser(u.username)}
                                  className="text-red-600 hover:text-red-800 font-semibold text-xs transition"
                                >
                                  Delete
                                </button>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Info Box */}
          <div className="mt-8 bg-blue-50 border border-blue-200 rounded-lg p-4">
            <h3 className="font-semibold text-blue-900 mb-2">Admin Notes</h3>
            <ul className="text-sm text-blue-800 space-y-1 list-disc list-inside">
              <li>Default admin account: <code className="bg-white px-1 py-0.5 rounded">admin</code> / <code className="bg-white px-1 py-0.5 rounded">NDAY_2026</code></li>
              <li>You must enter your admin password to create or delete users</li>
              <li>The admin account cannot be deleted</li>
              <li>Usernames are case-insensitive and automatically lowercased</li>
              <li>Passwords must be at least 6 characters</li>
            </ul>
          </div>
        </main>

        {/* Footer */}
        <footer className="bg-gray-100 border-t border-gray-300 mt-12 py-4">
          <div className="max-w-4xl mx-auto px-4 text-center text-sm text-gray-600">
            <p>NDAY Route Manager © 2026. All rights reserved.</p>
          </div>
        </footer>
      </div>
    </ProtectedRoute>
  );
}
