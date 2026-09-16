import { defineConfig } from "vite";

const host = process.env.TAURI_DEV_HOST;

export default defineConfig({
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    host: host || "127.0.0.1",
    hmr: host
      ? {
          protocol: "ws",
          host,
          port: 1421,
        }
      : undefined,
    // Browser-only development: forwards to `python -m dataforge_application.server --http 8765`.
    proxy: { "/rpc": "http://127.0.0.1:8765" },
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
  envPrefix: ["VITE_", "TAURI_"],
  // Lazily imported desktop modules are pre-bundled so the dev server never reloads the page
  // mid-session when Scrape Studio or Settings first loads them.
  optimizeDeps: {
    include: ["@tauri-apps/api/core", "@tauri-apps/api/event", "@tauri-apps/api/window", "@tauri-apps/plugin-dialog", "@tauri-apps/plugin-opener"],
  },
  build: {
    target: "es2022",
    outDir: "dist",
    emptyOutDir: true,
  },
});
