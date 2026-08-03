import { test, expect } from "@playwright/test";
import {
  DEMO_USERS,
  changePasswordViaUI,
  fillLoginForm,
  gotoLogin,
  loginAsUser,
  logoutViaUI,
} from "./helpers/auth";

const submitButton = /authenticate session|autentica sessione|login|sign in|enter/i;

test.describe("Profile session flows", () => {
  test.beforeEach(() => {
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live profile tests");
  });

  test("operator can change password on profile and re-login", async ({ page }) => {
    const user = DEMO_USERS.viewer;
    const newPassword = `${user.password}E2E`;

    await loginAsUser(page, user);
    await changePasswordViaUI(page, user.password, newPassword);
    await logoutViaUI(page);

    await gotoLogin(page);
    await fillLoginForm(page, user.username, newPassword);
    const loginResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/auth/login") &&
        response.request().method() === "POST"
    );
    await page.getByRole("button", { name: submitButton }).click();
    const loginResponse = await loginResponsePromise;
    expect(loginResponse.status()).toBe(200);
    await expect(page).toHaveURL(/\/(it|en)(\/)?$/);

    // Reset the password so subsequent tests can use the seeded viewer account.
    await changePasswordViaUI(page, newPassword, user.password);
  });

  test("logout clears session and blocks protected routes", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.tenantAdmin);
    await logoutViaUI(page);

    const authCheckPromise = page.waitForResponse(
      (response) => response.url().includes("/api/v1/auth/me")
    );
    await page.goto("/en/users", { waitUntil: "domcontentloaded" });
    const authResponse = await authCheckPromise;
    expect([401, 403]).toContain(authResponse.status());
    await expect(page).toHaveURL(/\/(it|en)\/login/);
  });
});
