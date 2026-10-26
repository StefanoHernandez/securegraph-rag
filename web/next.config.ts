import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

// Next injects inline bootstrap scripts and styled-jsx/Tailwind style tags, so
// 'unsafe-inline' is unavoidable for styles and, without per-request nonces,
// for scripts too. Everything else is locked to same-origin: no external
// script, style, font, or frame host is reachable, connect-src is limited to
// the same origin (the BFF proxies the API), and frame-ancestors 'none'
// blocks framing regardless of X-Frame-Options support.
const CONTENT_SECURITY_POLICY = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "worker-src 'self' blob:",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
  "upgrade-insecure-requests",
].join("; ");

const SECURITY_HEADERS = [
  { key: "Content-Security-Policy", value: CONTENT_SECURITY_POLICY },
  { key: "X-Content-Type-Options", value: "nosniff" },
  { key: "X-Frame-Options", value: "DENY" },
  { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
  // The UI needs none of these; denying them limits what an injected script
  // could reach if the CSP above were ever bypassed.
  {
    key: "Permissions-Policy",
    value: "camera=(), microphone=(), geolocation=(), payment=(), usb=()",
  },
  // Ignored over plain HTTP, so it is inert in local development and takes
  // effect only once the app is served over TLS.
  {
    key: "Strict-Transport-Security",
    value: "max-age=31536000; includeSubDomains",
  },
];

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(process.cwd(), "."),
  // `next dev --hostname 0.0.0.0` treats 127.0.0.1 as a cross-origin dev
  // host and refuses the HMR socket, so the login form never hydrates.
  allowedDevOrigins: ["127.0.0.1"],
  async headers() {
    return [{ source: "/:path*", headers: SECURITY_HEADERS }];
  },
};

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
