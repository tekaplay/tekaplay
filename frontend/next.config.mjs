/**
 * The API origin has to be reachable from the browser, so it must be named in
 * connect-src. It is only known at build time (NEXT_PUBLIC_* is inlined then),
 * which is also when this config is evaluated — so deriving the CSP from it
 * here is correct rather than a hack.
 */
const apiBaseUrl = process.env.NEXT_PUBLIC_API_BASE_URL ?? 'http://localhost:8000/api/v1';
const apiOrigin = (() => {
  try {
    return new URL(apiBaseUrl).origin;
  } catch {
    return '';
  }
})();

const isProd = process.env.NODE_ENV === 'production';

const csp = [
  "default-src 'self'",
  // Next.js inlines its bootstrap and hydration payload; without
  // 'unsafe-inline' the app does not start. 'unsafe-eval' is dev-only (React
  // Refresh needs it) and is deliberately absent from production.
  `script-src 'self' 'unsafe-inline'${isProd ? '' : " 'unsafe-eval'"}`,
  // styled-jsx and Tailwind's runtime insert <style> elements.
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  `connect-src 'self' ${apiOrigin}`.trim(),
  "frame-ancestors 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "object-src 'none'",
].join('; ');

const securityHeaders = [
  { key: 'Content-Security-Policy', value: csp },
  { key: 'X-Content-Type-Options', value: 'nosniff' },
  { key: 'X-Frame-Options', value: 'DENY' },
  { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
  { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
];

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Container-friendly output. Render, ECS and Container Apps all run this
  // same image; there is no platform-specific build.
  output: 'standalone',
  poweredByHeader: false,
  async headers() {
    return [{ source: '/:path*', headers: securityHeaders }];
  },
};
export default nextConfig;
