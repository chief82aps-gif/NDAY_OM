'use client';

import { useRouter } from 'next/router';
import { useAuth } from '../contexts/AuthContext';
import { useEffect, useState } from 'react';

function resolveApi(): string {
  if (process.env.NEXT_PUBLIC_API_URL) return process.env.NEXT_PUBLIC_API_URL;
  if (typeof window !== 'undefined' && window.location.hostname !== 'localhost') {
    return 'https://nday-om.onrender.com';
  }
  return 'http://127.0.0.1:8001';
}

const SLACK_ERROR_MESSAGES: Record<string, string> = {
  denied: 'Slack sign-in was cancelled.',
  invalid_state: 'That Slack sign-in link expired — please try again.',
  not_configured: 'Slack sign-in is not set up yet.',
  slack_unreachable: "Couldn't reach Slack — please try again.",
  token_exchange_failed: 'Slack sign-in failed — please try again.',
  userinfo_failed: 'Slack sign-in failed — please try again.',
  not_linked: "This Slack account isn't linked to a dashboard login yet. Contact your administrator.",
  inactive: 'This account is not active. Contact your administrator.',
};

export default function Login() {
  const router = useRouter();
  const { login, loginWithSlackToken } = useAuth();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [showPasswordForm, setShowPasswordForm] = useState(false);

  useEffect(() => {
    if (!router.isReady) return;
    const { slack_token: slackToken, username: slackUsername, name: slackName, role: slackRole, slack_error: slackError } = router.query;

    if (typeof slackToken === 'string') {
      loginWithSlackToken(
        slackToken,
        typeof slackUsername === 'string' ? slackUsername : '',
        typeof slackName === 'string' ? slackName : '',
        typeof slackRole === 'string' ? slackRole : 'driver'
      );
      router.replace('/');
      return;
    }

    if (typeof slackError === 'string') {
      setError(SLACK_ERROR_MESSAGES[slackError] || 'Slack sign-in failed — please try again.');
      setShowPasswordForm(true);   // surface the fallback immediately — Slack just failed
      router.replace('/login', undefined, { shallow: true });
    }
  }, [router.isReady, router.query]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    setLoading(true);

    try {
      await login(username, password);
      router.push('/');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Login failed');
    } finally {
      setLoading(false);
    }
  };

  const handleSlackLogin = () => {
    window.location.href = `${resolveApi()}/auth/slack/login`;
  };

  return (
    <div className="min-h-screen bg-gradient-to-br from-ndl-blue to-blue-700 flex items-center justify-center px-4">
      <div className="w-full max-w-md">
        {/* Logo Section */}
        <div className="text-center mb-8">
          <div className="inline-block w-16 h-16 bg-white rounded-lg flex items-center justify-center mb-4">
            <span className="text-ndl-blue font-bold text-4xl">N</span>
          </div>
          <h1 className="text-4xl font-bold text-white mb-2">NDAY Route Manager</h1>
          <p className="text-blue-100">Sign in to your account</p>
        </div>

        {/* Login Card */}
        <div className="bg-white rounded-lg shadow-2xl p-8">
          {/* Error Message */}
          {error && (
            <div className="mb-6 bg-red-50 border border-red-200 text-red-700 px-4 py-3 rounded-lg text-sm">
              {error}
            </div>
          )}

          {/* Slack Sign-In — the primary, front-and-center option */}
          <button
            type="button"
            onClick={handleSlackLogin}
            className="w-full flex items-center justify-center gap-2 bg-ndl-blue hover:bg-blue-700 text-white font-semibold py-3 px-4 rounded-lg transition duration-200"
          >
            <svg width="20" height="20" viewBox="0 0 122.8 122.8" xmlns="http://www.w3.org/2000/svg">
              <path d="M25.8 77.6c0 7.1-5.8 12.9-12.9 12.9S0 84.7 0 77.6s5.8-12.9 12.9-12.9h12.9v12.9z" fill="#e01e5a"/>
              <path d="M32.3 77.6c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9v32.3c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V77.6z" fill="#e01e5a"/>
              <path d="M45.2 25.8c-7.1 0-12.9-5.8-12.9-12.9S38.1 0 45.2 0s12.9 5.8 12.9 12.9v12.9H45.2z" fill="#36c5f0"/>
              <path d="M45.2 32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H12.9C5.8 58.1 0 52.3 0 45.2s5.8-12.9 12.9-12.9h32.3z" fill="#36c5f0"/>
              <path d="M97 45.2c0-7.1 5.8-12.9 12.9-12.9s12.9 5.8 12.9 12.9-5.8 12.9-12.9 12.9H97V45.2z" fill="#2eb67d"/>
              <path d="M90.5 45.2c0 7.1-5.8 12.9-12.9 12.9s-12.9-5.8-12.9-12.9V12.9C64.7 5.8 70.5 0 77.6 0s12.9 5.8 12.9 12.9v32.3z" fill="#2eb67d"/>
              <path d="M77.6 97c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9-12.9-5.8-12.9-12.9V97h12.9z" fill="#ecb22e"/>
              <path d="M77.6 90.5c-7.1 0-12.9-5.8-12.9-12.9s5.8-12.9 12.9-12.9h32.3c7.1 0 12.9 5.8 12.9 12.9s-5.8 12.9-12.9 12.9H77.6z" fill="#ecb22e"/>
            </svg>
            Sign in with Slack
          </button>

          {/* Password fallback — tucked away, not deleted. Keeping this
              reachable matters: Slack sign-in only works for an account
              already linked (see /auth/link-slack), and if Slack itself
              has an outage this is the only other way in. */}
          <div className="mt-6 pt-6 border-t border-gray-200">
            {!showPasswordForm ? (
              <button
                type="button"
                onClick={() => setShowPasswordForm(true)}
                className="w-full text-sm text-gray-500 hover:text-gray-700 text-center"
              >
                Use username &amp; password instead
              </button>
            ) : (
              <form onSubmit={handleSubmit} className="space-y-4">
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Username
                  </label>
                  <input
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="Enter your username"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue focus:border-transparent"
                    disabled={loading}
                    autoComplete="username"
                  />
                </div>
                <div>
                  <label className="block text-sm font-semibold text-gray-700 mb-2">
                    Password
                  </label>
                  <input
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="Enter your password"
                    className="w-full px-4 py-2 border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-ndl-blue focus:border-transparent"
                    disabled={loading}
                    autoComplete="current-password"
                  />
                </div>
                <button
                  type="submit"
                  disabled={loading || !username || !password}
                  className="w-full bg-gray-700 hover:bg-gray-800 text-white font-semibold py-2 px-4 rounded-lg transition duration-200 disabled:opacity-50 disabled:cursor-not-allowed"
                >
                  {loading ? 'Signing in...' : 'Sign In'}
                </button>
              </form>
            )}
          </div>
        </div>

        {/* Footer */}
        <p className="text-center text-blue-100 text-sm mt-6">
          &copy; 2026 NDAY Logistics. All rights reserved.
        </p>
      </div>
    </div>
  );
}
