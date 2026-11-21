import type { NextConfig } from "next";

// Static export: FastAPI serves the files from web-next/out, so the app and the API share one origin.
const nextConfig: NextConfig = {
  output: "export",
  trailingSlash: true,
  images: { unoptimized: true },
  // Local dev: forward API calls to the FastAPI server on :8000.
  ...(process.env.NODE_ENV === "development"
    ? { rewrites: async () => [{ source: "/api/:path*", destination: "http://127.0.0.1:8000/api/:path*" }] }
    : {}),
};

export default nextConfig;
