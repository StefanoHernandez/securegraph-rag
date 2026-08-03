import { test, expect } from "@playwright/test";
import { DEMO_USERS, expectGetApiStatusOnVisit, loginAsUser } from "./helpers/auth";

const DEFAULT_TENANT = process.env.E2E_TENANT_ID ?? "default";

test.describe("Group membership", () => {
  test.beforeEach(() => {
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live group membership tests");
  });

  test("tenant admin can add and remove analyst in Direction group", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.tenantAdmin);

    await expectGetApiStatusOnVisit(
      page,
      "/en/groups",
      `/api/v1/tenants/${DEFAULT_TENANT}/groups`,
      200
    );

    await page.getByRole("button", { name: "Direction" }).click();
    await expect(page.getByText("direction")).toBeVisible();

    const analystLabel = new RegExp(DEMO_USERS.analyst.username, "i");
    const memberRow = page
      .locator(".border.border-hairline")
      .filter({ hasText: analystLabel });

    if (await memberRow.count()) {
      await memberRow.getByRole("button").click();
      const removeResponsePromise = page.waitForResponse(
        (response) =>
          response.url().includes("/groups/") &&
          response.url().includes("/members/") &&
          response.request().method() === "DELETE"
      );
      await removeResponsePromise;
      await expect(memberRow).toHaveCount(0);
    }

    const addMemberResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/groups/") &&
        response.url().includes("/members/") &&
        response.request().method() === "PUT"
    );

    await page.locator("select").first().selectOption({ label: /analyst/i });
    await page.getByRole("button", { name: /^Add$/i }).click();
    const addResponse = await addMemberResponsePromise;
    expect(addResponse.status()).toBe(204);

    await expect(
      page.locator(".border.border-hairline").filter({ hasText: analystLabel })
    ).toHaveCount(1);

    await page
      .locator(".border.border-hairline")
      .filter({ hasText: analystLabel })
      .getByRole("button")
      .click();
    const removeResponsePromise = page.waitForResponse(
      (response) =>
        response.url().includes("/groups/") &&
        response.url().includes("/members/") &&
        response.request().method() === "DELETE"
    );
    const removeResponse = await removeResponsePromise;
    expect(removeResponse.status()).toBe(204);
  });
});
