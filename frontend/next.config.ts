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
      {
        source: '/rescue/:path*',
        destination: `${backendUrl}/rescue/:path*`,
      },
      {
        source: '/attendance/:path*',
        destination: `${backendUrl}/attendance/:path*`,
      },
      {
        source: '/slack/:path*',
        destination: `${backendUrl}/slack/:path*`,
      },
      {
        source: '/ops-ingest/:path*',
        destination: `${backendUrl}/ops-ingest/:path*`,
      },
      {
        source: '/rostering/:path*',
        destination: `${backendUrl}/rostering/:path*`,
      },
      {
        source: '/adp/:path*',
        destination: `${backendUrl}/adp/:path*`,
      },
      {
        source: '/cortex-tracking/:path*',
        destination: `${backendUrl}/cortex-tracking/:path*`,
      },
      {
        source: '/eod-survey/:path*',
        destination: `${backendUrl}/eod-survey/:path*`,
      },
      {
        source: '/crash-report/:path*',
        destination: `${backendUrl}/crash-report/:path*`,
      },
      {
        source: '/injury-reports/:path*',
        destination: `${backendUrl}/injury-reports/:path*`,
      },
      {
        source: '/sentiment-survey/:path*',
        destination: `${backendUrl}/sentiment-survey/:path*`,
      },
    ];
  },
};

export default nextConfig;
