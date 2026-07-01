import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright E2E configuration for the Forensic Vault UI.
 *
 * Tests run against the Next.js dev server on :3000 and the FastAPI
 * backend on :8000. Start both before running:
 *   make api     # terminal 1
 *   make web-dev # terminal 2
 *   npx playwright test
 */
export default defineConfig({
  testDir: "./tests/e2e",
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
    headless: true,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
