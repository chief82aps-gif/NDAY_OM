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
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<StatusMessage | null>(null);
  const [authenticated, setAuthenticated] = useState(false);

  // Form states
  const [newUsername, setNewUsername] = useState('');
  const [newPassword, setNewPassword] = useState('');
  const [confirmPassword, setConfirmPassword] = useState('');
  const [adminPassword, setAdminPassword] = useState('');
  const [isSubmitting, setIsSubmitting] = useState(false);

  // Password change modal
  const [showPasswordModal, setShowPasswordModal] = useState(false);
  const [passwordChangeUser, setPasswordChangeUser] = useState('');
  const [newPasswordForUser, setNewPasswordForUser] = useState('');
  const [confirmNewPasswordForUser, setConfirmNewPasswordForUser] = useState('');

  const showMessage = (type: 'success' | 'error' | 'info', text: string) => {
    setMessage({ type, text });
    setTimeout(() => setMessage(null), 5000);
  };

  const handleAuthenticate = async () => {
    if (!adminPassword || !user) {
      showMessage('error', 'Please enter your admin password');
      return;
    }

    setLoading(true);
    try {
      const response = await fetch(`${API_URL}/auth/list-users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: user.username,
          password: adminPassword,
        }),
      });

      if (!response.ok) {
        throw new Error('Invalid admin credentials');
      }

      const data = await response.json();
      setUsers(data.users);
      setAuthenticated(true);
      showMessage('success', 'Admin panel unlocked');
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Authentication failed');
      setAuthenticated(false);
    } finally {
      setLoading(false);
    }
  };

  const loadUsers = async () => {
    if (!user || !authenticated) return;

    try {
      setLoading(true);
      const response = await fetch(`${API_URL}/auth/list-users`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: user.username,
          password: adminPassword,
        }),
      });

      if (!response.ok) {
        throw new Error('Failed to load users');
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

    if (!authenticated) {
      showMessage('error', 'Please authenticate first');
      return;
    }

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

    if (!authenticated) {
      showMessage('error', 'Please authenticate first');
      return;
    }

    try {
      const response = await fetch(`${API_URL}/auth/delete-user`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: usernameToDelete,
          password: '',
          admin_username: user?.username,
          admin_password: adminPassword,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to delete user');
      }

      showMessage('success', `User '$" + "usernameToDelete}' deleted successfully`);
      await loadUsers();
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to delete user');
    }
  };

  const openPasswordModal = (username: string) => {
    setPasswordChangeUser(username);
    setNewPasswordForUser('');
    setConfirmNewPasswordForUser('');
    setShowPasswordModal(true);
  };

  const handleChangePassword = async () => {
    if (!authenticated) {
      showMessage('error', 'Please authenticate first');
      return;
    }

    if (!newPasswordForUser || newPasswordForUser.length < 6) {
      showMessage('error', 'New password must be at least 6 characters');
      return;
    }

    if (newPasswordForUser !== confirmNewPasswordForUser) {
      showMessage('error', 'Passwords do not match');
      return;
    }

    try {
      const response = await fetch(`${API_URL}/auth/change-password`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          username: passwordChangeUser,
          old_password: '',
          new_password: newPasswordForUser,
          admin_username: user?.username,
          admin_password: adminPassword,
        }),
      });

      if (!response.ok) {
        const errorData = await response.json();
        throw new Error(errorData.detail || 'Failed to change password');
      }

      showMessage('success', `Password for '${passwordChangeUser}' changed successfully`);
      setShowPasswordModal(false);
    } catch (error) {
      showMessage('error', error instanceof Error ? error.message : 'Failed to change password');
    }
  };

  return (
    <ProtectedRoute>
      <div className="min-h-screen bg-gray-50">
        <PageHeader title="Admin Panel" showBack={true} />

        <main className="max-w-5xl mx-auto px-4 py-8">
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

          {/* Authentication Section */}
          {!authenticated ? (
            <div className="max-w-md mx-auto bg-white rounded-lg shadow-md p-8">
              <div className="text-center mb-6">
                <div className="text-5xl mb-4">🔐</div>
                <h2 className="text-2xl font-bold text-ndl-blue mb-2">Admin Authentication</h2>
                <p className="text-gray-600">Enter your admin password to access user management</p>
              </div>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Logged in as: <span className="text-ndl-blue">{user?.username}</span>
                  </label>
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Admin Password
                  </label>
                  <input
                    type="password"
                    value={adminPassword}
                    onChange={(e) => setAdminPassword(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && handleAuthenticate()}
                    placeholder="Enter your password"
                    className="w-full px-4 py-3 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                    disabled={loading}
                  />
                </div>

                <button
                  onClick={handleAuthenticate}
                  disabled={loading || !adminPassword}
                  className="w-full bg-ndl-blue text-white py-3 rounded-lg font-semibold hover:bg-blue-700 transition disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? 'Authenticating...' : 'Unlock Admin Panel'}
                </button>
              </div>

              <div className="mt-6 text-center text-sm text-gray-600">
                <p>Default admin password: <code className="bg-gray-100 px-2 py-1 rounded">NDAY_2026</code></p>
              </div>
            </div>
          ) : (
            <>
              <div className="grid grid-cols-1 lg:grid-cols-5 gap-6">
                {/* Create User Form */}
                <div className="lg:col-span-2">
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
                <div className="lg:col-span-3">
                  <div className="bg-white rounded-lg shadow-md p-6">
                    <div className="flex justify-between items-center mb-4">
                      <h2 className="text-xl font-bold text-ndl-blue">System Users</h2>
                      <button
                        onClick={loadUsers}
                        disabled={loading}
                        className="text-sm px-4 py-2 bg-gray-200 hover:bg-gray-300 rounded-lg transition disabled:opacity-50"
                      >
                        {loading ? 'Refreshing...' : '🔄 Refresh'}
                      </button>
                    </div>

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
                                <td className="py-3 px-4 text-right space-x-3">
                                  {u.username === 'admin' ? (
                                    <span className="text-xs text-gray-500 italic">Protected</span>
                                  ) : (
                                    <>
                                      <button
                                        onClick={() => openPasswordModal(u.username)}
                                        className="text-blue-600 hover:text-blue-800 font-semibold text-xs transition"
                                      >
                                        Change Password
                                      </button>
                                      <button
                                        onClick={() => handleDeleteUser(u.username)}
                                        className="text-red-600 hover:text-red-800 font-semibold text-xs transition"
                                      >
                                        Delete
                                      </button>
                                    </>
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
            </>
          )}
        </main>

        {/* Password Change Modal */}
        {showPasswordModal && (
          <div className="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50 p-4">
            <div className="bg-white rounded-lg shadow-2xl max-w-md w-full p-6">
              <h2 className="text-2xl font-bold text-ndl-blue mb-4">Change Password</h2>
              <p className="text-gray-600 mb-4">
                Changing password for: <span className="font-semibold text-gray-900">{passwordChangeUser}</span>
              </p>

              <div className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    New Password
                  </label>
                  <input
                    type="password"
                    value={newPasswordForUser}
                    onChange={(e) => setNewPasswordForUser(e.target.value)}
                    placeholder="Min 6 characters"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                  />
                </div>

                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-1">
                    Confirm New Password
                  </label>
                  <input
                    type="password"
                    value={confirmNewPasswordForUser}
                    onChange={(e) => setConfirmNewPasswordForUser(e.target.value)}
                    placeholder="Confirm password"
                    className="w-full px-3 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue"
                  />
                </div>
              </div>

              <div className="flex gap-3 mt-6">
                <button
                  onClick={() => setShowPasswordModal(false)}
                  className="flex-1 px-4 py-2 bg-gray-300 hover:bg-gray-400 rounded-lg font-semibold transition"
                >
                  Cancel
                </button>
                <button
                  onClick={handleChangePassword}
                  className="flex-1 px-4 py-2 bg-ndl-blue hover:bg-blue-700 text-white rounded-lg font-semibold transition"
                >
                  Change Password
                </button>
              </div>
            </div>
          </div>
        )}

        {/* Footer */}
        <footer className="bg-gray-100 border-t border-gray-300 mt-12 py-4">
          <div className="max-w-5xl mx-auto px-4 text-center text-sm text-gray-600">
            <p>NDAY Route Manager © 2026. All rights reserved.</p>
          </div>
        </footer>
      </div>
    </ProtectedRoute>
  );
}
