import type { NextConfig } from "next";

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

export default nextConfig;
