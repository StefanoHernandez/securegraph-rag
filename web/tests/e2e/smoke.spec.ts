import { test, expect } from "@playwright/test";

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
 * - API on :8000 with auth_enabled=true
 * - Super-admin bootstrapped via `securgraph bootstrap-superadmin`
 * - Next dev server on :3000
 *
 * Set E2E_ADMIN_PASSWORD env var to the bootstrap password.
 */

const ADMIN_PASSWORD = process.env.E2E_ADMIN_PASSWORD ?? "change-me-min-12-chars";

test.describe("Forensic Vault smoke", () => {
  test("login page renders with forensic vault branding", async ({ page }) => {
    await page.goto("/login");
    await expect(page).toHaveTitle(/SecureGraph|Forensic/i);
    // Login form present
    await expect(page.locator('input[name="username"], input[placeholder*="username" i]')).toBeVisible();
    await expect(page.locator('input[type="password"]')).toBeVisible();
  });

  test("login rejects invalid credentials", async ({ page }) => {
    await page.goto("/login");
    await page.locator('input[name="username"], input[placeholder*="username" i]').fill("nonexistent");
    await page.locator('input[type="password"]').fill("wrong-password-x");
    await page.getByRole("button", { name: /login|sign in|enter/i }).click();
    // Should stay on login or show error
    await expect(page).toHaveURL(/login/);
  });

  test("super_admin can login and see dashboard", async ({ browser }) => {
    // Skip if no live API — this test requires a running backend
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live login tests");

    const page = await browser.newPage();
    await page.goto("/login");
    await page.locator('input[name="username"], input[placeholder*="username" i]').fill("admin");
    await page.locator('input[type="password"]').fill(ADMIN_PASSWORD);
    await page.getByRole("button", { name: /login|sign in|enter/i }).click();
    // Should redirect to dashboard
    await expect(page).toHaveURL(/\/(it|en)?\/?$/);
    // Sidebar should be visible
    await expect(page.locator("nav, aside").first()).toBeVisible();
  });

  test("locale routes render (/it and /en)", async ({ page }) => {
    // Italian locale
    await page.goto("/it");
    await expect(page).toHaveURL(/\/it/);
    // English locale
    await page.goto("/en");
    await expect(page).toHaveURL(/\/en/);
  });

  test("dashboard pages render without JS errors", async ({ page }) => {
    const pages = ["/", "/chat", "/ingest", "/health", "/audit", "/graph"];
    for (const path of pages) {
      await page.goto(path);
      // No uncaught JS errors
      const errors: string[] = [];
      page.on("pageerror", (err) => errors.push(err.message));
      await page.waitForLoadState("networkidle");
      // Allow Next.js prerender warnings but not hard errors
      expect(errors.filter((e) => !e.includes("ENVIRONMENT_FALLBACK"))).toEqual([]);
    }
  });
});
