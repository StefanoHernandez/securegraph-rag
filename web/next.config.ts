import type { NextConfig } from "next";
import createNextIntlPlugin from "next-intl/plugin";

const nextConfig: NextConfig = {
  async rewrites() {
    return [
      {
        source: "/api/:path*",
        // 127.0.0.1, not localhost: uvicorn binds IPv4 (0.0.0.0); "localhost"
        // resolves to ::1 (IPv6) first, which uvicorn does not listen on —
        // the first connect is refused and large POSTs (ingest) reset.
        destination: "http://127.0.0.1:8000/:path*",
      },
    ];
  },
};

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

export default withNextIntl(nextConfig);
