import path from "node:path";
import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const backendOrigin =
  process.env.SECURGRAPH_API_ORIGIN ||
  process.env.NEXT_PUBLIC_API_DIRECT ||
  "http://127.0.0.1:8000";

const nextConfig: NextConfig = {
  outputFileTracingRoot: path.join(process.cwd(), "."),
  experimental: {
    // The rewrite proxy defaults to a 30s upstream timeout and resets the
    // socket (ECONNRESET) when it fires. Cold /query and /ingest calls
    // (embedding-model load + LLM round trips) routinely exceed 30s.
    proxyTimeout: 180_000,
  },
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // 127.0.0.1, not localhost: uvicorn binds IPv4 (0.0.0.0); "localhost"
        // resolves to ::1 (IPv6) first, which uvicorn does not listen on —
        // the first connect is refused and large POSTs (ingest) reset.
        destination: `${backendOrigin}/api/:path*`,
      },
    ];
  },
};

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
