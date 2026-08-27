import type { NextConfig } from "next";

/**
 * Static export. The whole UI is a client-side app talking to the Python
 * server over websocket and REST, so there is nothing for a Node runtime to
 * do — `kravein serve` mounts the built files directly and no Node process
 * exists in production.
 *
 * `npm run build` writes `out/` and then copies it to `kravein/web-dist/`,
 * which is where `server.py` looks.
 */
const nextConfig: NextConfig = {
  output: "export",
  // The export has no image optimiser behind it.
  images: { unoptimized: true },
  // Emit `path/index.html` so a plain static file server resolves routes
  // without needing rewrite rules.
  trailingSlash: true,
};

export default nextConfig;
