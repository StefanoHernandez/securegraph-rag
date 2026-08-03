import { test, expect } from "@playwright/test";
import {
  DEMO_USERS,
  expectGetApiStatusOnVisit,
  expectPostApiStatus,
  loginAsUser,
} from "./helpers/auth";

const DEFAULT_TENANT = process.env.E2E_TENANT_ID ?? "default";

test.describe("Role and group authorization", () => {
  test.beforeEach(() => {
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live role tests");
  });

  test("superadmin can list tenants, users, and business groups", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.superadmin);

    await expectGetApiStatusOnVisit(page, "/en/tenants", "/api/v1/tenants", 200);
    await expect(page.getByRole("heading", { name: /^Tenants$/i })).toBeVisible();

    await expectGetApiStatusOnVisit(
      page,
      "/en/users",
      `/api/v1/tenants/${DEFAULT_TENANT}/users`,
      200
    );
    await expect(page.getByRole("heading", { name: /^Users$/i })).toBeVisible();

    await expectGetApiStatusOnVisit(
      page,
      "/en/groups",
      `/api/v1/tenants/${DEFAULT_TENANT}/groups`,
      200
    );
    await expect(page.getByRole("heading", { name: /^Groups$/i })).toBeVisible();
    await expect(page.getByRole("button", { name: "Direction" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Project Managers" })).toBeVisible();
  });

  test("tenant admin can manage users and sees seeded business groups", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.tenantAdmin);

    await expectGetApiStatusOnVisit(
      page,
      "/en/users",
      `/api/v1/tenants/${DEFAULT_TENANT}/users`,
      200
    );
    await expect(
      page.getByRole("table").getByText(DEMO_USERS.tenantAdmin.username, { exact: true })
    ).toBeVisible();

    await expectGetApiStatusOnVisit(
      page,
      "/en/groups",
      `/api/v1/tenants/${DEFAULT_TENANT}/groups`,
      200
    );
    await expect(page.getByRole("button", { name: "Direction" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Senior Management" })).toBeVisible();
  });

  test("analyst can browse tenant scope but not admin users or groups", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.analyst);

    await expectGetApiStatusOnVisit(page, "/en/tenants", "/api/v1/tenants", 200);

    await expectGetApiStatusOnVisit(
      page,
      "/en/users",
      `/api/v1/tenants/${DEFAULT_TENANT}/users`,
      403
    );
    await expect(
      page.getByText(/Unable to load users|HTTP Error 403|status code 403|403|Forbidden/i)
    ).toBeVisible();

    await expectGetApiStatusOnVisit(
      page,
      "/en/groups",
      `/api/v1/tenants/${DEFAULT_TENANT}/groups`,
      403
    );
    await expect(
      page.getByText(/Unable to load groups|HTTP Error 403|status code 403|403|Forbidden/i)
    ).toBeVisible();

    await page.goto("/en/chat", { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toBeVisible();
  });

  test("tenant admin cannot create tenants via API", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.tenantAdmin);
    await expectPostApiStatus(
      page,
      "/tenants",
      { id: "e2e-forbidden-tenant", display_name: "E2E Forbidden Tenant" },
      403
    );
  });

  test("viewer is denied tenant admin APIs for users and groups", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.viewer);

    await expectGetApiStatusOnVisit(
      page,
      "/en/users",
      `/api/v1/tenants/${DEFAULT_TENANT}/users`,
      403
    );
    await expect(
      page.getByText(/Unable to load users|HTTP Error 403|status code 403|403|Forbidden/i)
    ).toBeVisible();

    await expectGetApiStatusOnVisit(
      page,
      "/en/groups",
      `/api/v1/tenants/${DEFAULT_TENANT}/groups`,
      403
    );
    await expect(
      page.getByText(/Unable to load groups|HTTP Error 403|status code 403|403|Forbidden/i)
    ).toBeVisible();
  });
});
