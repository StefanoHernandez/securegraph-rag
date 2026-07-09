import { test, expect, type Page } from "@playwright/test";
import {
  DEMO_USERS,
  fillLoginForm,
  gotoLogin,
  loginAsUser,
} from "./helpers/auth";

/**
 * Forensic Vault E2E smoke tests.
 *
 * These tests verify the primary user journeys:
 * - Login page renders and accepts credentials
 * - Dashboard loads after login
 * - Key admin pages render for super_admin
 * - Locale switching works (/it and /en)
 *
 * Prerequisites:
 * - API with auth_enabled=true
 * - Demo users seeded via `securgraph seed-demo`
 * - Next server reachable at E2E_BASE_URL
 *
 * Set E2E_ADMIN_USERNAME / E2E_ADMIN_PASSWORD to override the demo admin.
 */

const submitButton = /authenticate session|autentica sessione|login|sign in|enter/i;
const usernameField = /operator id|username|id operatore/i;
const passwordField = /passphrase|password/i;

async function loginAsDemoAdmin(page: Page) {
  await loginAsUser(page, DEMO_USERS.tenantAdmin);
}

test.describe("Forensic Vault smoke", () => {
  test("login page renders with forensic vault branding", async ({ page }) => {
    await gotoLogin(page);
    await expect(page).toHaveTitle(/SecureGraph|Forensic/i);
    await expect(page.getByText(/securegraph/i)).toBeVisible();
    await expect(page.getByRole("textbox", { name: usernameField })).toBeVisible();
    await expect(page.getByRole("textbox", { name: passwordField })).toBeVisible();
  });

  test("login rejects invalid credentials", async ({ page }) => {
    // Requires a live backend: the assertion waits for a real 401 from
    // /api/v1/auth/login. Without E2E_LIVE the proxied request never
    // resolves to 401 and waitForResponse would hang the CI run.
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live login tests");

    await gotoLogin(page);
    await fillLoginForm(page, "nonexistent", "wrong-password-x");
    const loginResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/auth/login") &&
        response.request().method() === "POST"
    );
    await page.getByRole("button", { name: submitButton }).click();
    const loginResponse = await loginResponsePromise;
    expect(loginResponse.status()).toBe(401);
    await expect(page).toHaveURL(/\/(it|en)\/login/);
    await expect(page.getByRole("button", { name: submitButton })).toBeEnabled();
  });

  test("demo admin can login and see dashboard", async ({ page }) => {
    // Skip if no live API — this test requires a running backend
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live login tests");

    await loginAsDemoAdmin(page);
    await expect(page.locator("nav, aside").first()).toBeVisible();
    await expect(page.getByText(/documents under custody|documenti/i)).toBeVisible();
  });

  test("locale routes render (/it and /en)", async ({ page }) => {
    await page.goto("/it/login", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/it\/login/);
    await expect(page.getByRole("button", { name: submitButton })).toBeVisible();

    await page.goto("/en/login", { waitUntil: "domcontentloaded" });
    await expect(page).toHaveURL(/\/en\/login/);
    await expect(page.getByRole("button", { name: submitButton })).toBeVisible();
  });

  test("dashboard pages render without JS errors", async ({ page }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => errors.push(err.message));
    if (process.env.E2E_LIVE) {
      await loginAsDemoAdmin(page);
    }
    const pages = ["/it", "/it/chat", "/it/ingest", "/it/health", "/it/audit", "/it/graph"];
    for (const path of pages) {
      await page.goto(path, { waitUntil: "domcontentloaded" });
      await expect(page.locator("body")).toBeVisible();
      expect(errors.filter((e) => !e.includes("ENVIRONMENT_FALLBACK"))).toEqual([]);
    }
  });
});
