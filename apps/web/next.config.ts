import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The interface is a single client-rendered page with no server features,
  // so it is exported as static files (out/) and served by Workers Static
  // Assets alongside the Python API. See docs/DEPLOYMENT.md.
  output: "export",
};

export default nextConfig;
