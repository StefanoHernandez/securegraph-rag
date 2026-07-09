import { expect, type Page } from "@playwright/test";

export type DemoUser = {
  username: string;
  password: string;
  label: string;
};

export const DEMO_USERS = {
  superadmin: {
    username: process.env.E2E_SUPERADMIN_USERNAME ?? "superadmin",
    password: process.env.E2E_SUPERADMIN_PASSWORD ?? "superadmin123",
    label: "superadmin",
  },
  tenantAdmin: {
    username: process.env.E2E_ADMIN_USERNAME ?? "admin",
    password: process.env.E2E_ADMIN_PASSWORD ?? "admin123",
    label: "tenant_admin",
  },
  analyst: {
    username: process.env.E2E_ANALYST_USERNAME ?? "analyst",
    password: process.env.E2E_ANALYST_PASSWORD ?? "analyst123",
    label: "analyst",
  },
  viewer: {
    username: process.env.E2E_VIEWER_USERNAME ?? "viewer",
    password: process.env.E2E_VIEWER_PASSWORD ?? "viewer123",
    label: "viewer",
  },
} as const satisfies Record<string, DemoUser>;

const usernameField = /operator id|username|id operatore/i;
const passwordField = /passphrase|password/i;
const submitButton = /authenticate session|autentica sessione|login|sign in|enter/i;

export async function gotoLogin(page: Page) {
  const authCheckPromise = page
    .waitForResponse((response) => response.url().includes("/api/v1/auth/me"), {
      timeout: 10_000,
    })
    .catch(() => null);
  await page.goto("/en/login", { waitUntil: "domcontentloaded" });
  await expect(page).toHaveURL(/\/en\/login/);
  await expect(page.getByRole("textbox", { name: usernameField })).toBeVisible();
  await expect(page.getByRole("textbox", { name: passwordField })).toBeVisible();
  await authCheckPromise;
}

export async function fillLoginForm(page: Page, username: string, password: string) {
  const usernameInput = page.locator("#username");
  const passwordInput = page.locator("#password");
  await expect(usernameInput).toBeEditable();
  await expect(passwordInput).toBeEditable();
  await usernameInput.fill(username);
  await expect(usernameInput).toHaveValue(username);
  await passwordInput.fill(password);
  await expect(passwordInput).toHaveValue(password);
}

async function clearForcedPasswordReset(page: Page, currentPassword: string) {
  await page.evaluate(
    async ({ current, next }) => {
      const csrfEntry = document.cookie
        .split("; ")
        .find((entry) => entry.startsWith("securgraph_csrf="));
      const csrfToken = csrfEntry
        ? decodeURIComponent(csrfEntry.slice("securgraph_csrf=".length))
        : "";
      const response = await fetch("/api/v1/auth/change-password", {
        method: "POST",
        credentials: "include",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "x-csrf-token": csrfToken } : {}),
        },
        body: JSON.stringify({
          current_password: current,
          new_password: next,
        }),
      });
      if (!response.ok) {
        throw new Error(`change-password failed with status ${response.status}`);
      }
    },
    { current: currentPassword, next: currentPassword }
  );
}

export async function loginAsUser(page: Page, user: DemoUser) {
  await gotoLogin(page);
  await fillLoginForm(page, user.username, user.password);
  const loginResponsePromise = page.waitForResponse(
    (response) =>
      response.url().includes("/api/v1/auth/login") &&
      response.request().method() === "POST"
  );
  await page.getByRole("button", { name: submitButton }).click();
  const loginResponse = await loginResponsePromise;
  expect(loginResponse.status()).toBe(200);
  const loginBody = (await loginResponse.json()) as { user?: { must_change_password?: boolean } };
  if (loginBody.user?.must_change_password) {
    await clearForcedPasswordReset(page, user.password);
  }
  await expect(page).toHaveURL(/\/(it|en)(\/)?$/);
}

export async function expectGetApiStatusOnVisit(
  page: Page,
  path: string,
  urlPart: string,
  expectedStatus: number
) {
  const responsePromise = page.waitForResponse(
    (response) =>
      response.url().includes(urlPart) && response.request().method() === "GET"
  );
  await page.goto(path, { waitUntil: "domcontentloaded" });
  const response = await responsePromise;
  expect(response.status()).toBe(expectedStatus);
  return response;
}
