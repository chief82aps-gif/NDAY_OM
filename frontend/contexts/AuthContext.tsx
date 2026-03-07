'use client';

import { createContext, useContext, useState, useEffect, ReactNode } from 'react';

interface User {
  username: string;
  name: string;
  role?: string;
}

interface AuthContextType {
  user: User | null;
  isLoading: boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => void;
  isAuthenticated: boolean;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

const resolveApiUrl = (): string => {
  if (process.env.NEXT_PUBLIC_API_URL) {
    return process.env.NEXT_PUBLIC_API_URL;
  }

  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    if (host !== 'localhost' && host !== '127.0.0.1') {
      return 'https://nday-om.onrender.com';
    }
  }

  return 'http://127.0.0.1:8000';
};

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  // Check if user is already logged in on mount
  useEffect(() => {
    const storedUser = localStorage.getItem('user');
    if (storedUser) {
      try {
        setUser(JSON.parse(storedUser));
      } catch (e) {
        localStorage.removeItem('user');
      }
    }
    setIsLoading(false);
  }, []);

  const login = async (username: string, password: string) => {
    // Validate inputs
    if (!username || !password) {
      throw new Error('Username and password are required');
    }

    // Call backend to authenticate
    const API_URL = resolveApiUrl();
    
    try {
      const response = await fetch(`${API_URL}/auth/login`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        let detail = 'Invalid username or password';
        try {
          const errorPayload = await response.json();
          if (errorPayload?.detail) {
            detail = String(errorPayload.detail);
          }
        } catch {
          // Keep default message when response is not JSON.
        }
        throw new Error(detail);
      }

      const data = await response.json();

      const mockUser: User = {
        username,
        name: data.name || username.charAt(0).toUpperCase() + username.slice(1),
        role: data.role,
      };

      setUser(mockUser);
      localStorage.setItem('user', JSON.stringify(mockUser));
      if (data.access_token) {
        localStorage.setItem('access_token', data.access_token);
      }
    } catch (error) {
      throw error instanceof Error ? error : new Error('Authentication failed');
    }
  };

  const logout = () => {
    setUser(null);
    localStorage.removeItem('user');
    localStorage.removeItem('access_token');
  };

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        login,
        logout,
        isAuthenticated: !!user,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
}
