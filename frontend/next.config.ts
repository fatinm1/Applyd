import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  async rewrites() {
    // In production (e.g. Railway), the browser calls the same origin (`https://your-app/...`).
    // We proxy `/api/*` requests to the FastAPI server running inside the same app container.
    return [
      {
        source: "/api/:path*",
        destination: "http://localhost:8000/api/:path*",
      },
    ];
  },
};

export default nextConfig;
