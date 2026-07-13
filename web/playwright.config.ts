import { defineConfig, devices } from "@playwright/test";

const e2ePort = process.env.E2E_PORT ?? "3001";
const baseURL = process.env.E2E_BASE_URL ?? `http://127.0.0.1:${e2ePort}`;
const webServerUrl = process.env.E2E_WEB_SERVER_URL ?? baseURL;
const webServerCommand =
  process.env.E2E_WEB_SERVER_COMMAND ??
  `npm run dev -- --webpack --hostname 127.0.0.1 --port ${e2ePort}`;
const skipWebServer = process.env.E2E_SKIP_WEB_SERVER === "1";

/**
 * Playwright E2E configuration for the Forensic Vault UI.
 *
 * Tests run against an isolated Next.js dev server on :3001 and the FastAPI
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
    baseURL,
    trace: "on-first-retry",
    headless: true,
  },
  projects: [
    {
      name: "desktop-chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 5"] },
    },
  ],
  webServer: skipWebServer
    ? undefined
    : {
        command: webServerCommand,
        url: webServerUrl,
        reuseExistingServer: true,
        timeout: 60_000,
      },
});
