import { defineConfig } from "vitest/config";

export default defineConfig({
  esbuild: { jsx: "automatic" },
  test: {
    environment: "jsdom",
    include: ["src/**/*.spec.tsx"],
    setupFiles: ["src/test/setup.ts"],
    restoreMocks: true,
  },
});
