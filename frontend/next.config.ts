import type { NextConfig } from 'next';

const backendUrl = process.env.NEXT_PUBLIC_API_URL || 'https://nday-om.onrender.com';

const nextConfig: NextConfig = {
  reactStrictMode: true,
  async rewrites() {
    return [
      {
        source: '/upload/:path*',
        destination: `${backendUrl}/upload/:path*`,
      },
      {
        source: '/auth/:path*',
        destination: `${backendUrl}/auth/:path*`,
      },
      {
        source: '/audit/:path*',
        destination: `${backendUrl}/audit/:path*`,
      },
      {
        source: '/weekly-audit/:path*',
        destination: `${backendUrl}/weekly-audit/:path*`,
      },
      {
        source: '/weekly-audit-upload/:path*',
        destination: `${backendUrl}/weekly-audit-upload/:path*`,
      },
    ];
  },
};

export default nextConfig;
