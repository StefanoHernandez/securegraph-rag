import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  test: {
    environment: "jsdom",
    environmentOptions: {
      jsdom: {
        url: "http://localhost",
      },
    },
    setupFiles: ["./tests/vitest.setup.ts"],
    include: ["tests/unit/**/*.test.{ts,tsx}"],
    clearMocks: true,
    restoreMocks: true,
  },
});
