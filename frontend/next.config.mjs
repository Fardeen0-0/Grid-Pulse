/** @type {import('next').NextConfig} */
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.dirname(fileURLToPath(import.meta.url));

const nextConfig = {
  outputFileTracingRoot: root,
  experimental: {
    // Bill analysis can take longer than the default 30s rewrite proxy.
    proxyTimeout: 120_000,
  },
  async rewrites() {
    const target = process.env.API_PROXY_URL || "http://127.0.0.1:8000";
    return [{ source: "/api/:path*", destination: `${target}/api/:path*` }];
  },
};

export default nextConfig;
