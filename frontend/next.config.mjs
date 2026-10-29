/** @type {import('next').NextConfig} */

// In the container build the browser talks to the same origin and Next proxies /api/*
// to the API service on the private network, so the API is never published.
const API_INTERNAL_URL = process.env.API_INTERNAL_URL || '';
const AUTOPILOT_ONLY = process.env.NEXT_PUBLIC_AUTOPILOT_ONLY === 'true';

const nextConfig = {
  output: 'standalone',
  poweredByHeader: false,
  images: {
    remotePatterns: [
      { protocol: 'http', hostname: 'localhost', port: '8000' },
      { protocol: 'http', hostname: 'backend', port: '8000' },
    ],
    unoptimized: true,
  },
  experimental: {},
  async rewrites() {
    return API_INTERNAL_URL
      ? [{ source: '/api/:path*', destination: `${API_INTERNAL_URL}/api/:path*` }]
      : [];
  },
  async redirects() {
    // The hackathon build ships Autopilot only; the orchestrator's pages need services
    // (MLflow, sandbox, LangGraph agents) that this deployment does not run.
    return AUTOPILOT_ONLY
      ? [
          { source: '/', destination: '/repair', permanent: false },
          { source: '/runs/:path*', destination: '/autopilot', permanent: false },
          { source: '/registry/:path*', destination: '/autopilot', permanent: false },
        ]
      : [];
  },
  async headers() {
    return [{
      source: '/:path*',
      headers: [
        { key: 'X-Content-Type-Options', value: 'nosniff' },
        { key: 'X-Frame-Options', value: 'DENY' },
        { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
        { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
      ],
    }];
  },
};

export default nextConfig;
