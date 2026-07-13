import { test, expect } from "@playwright/test";
import { DEMO_USERS, loginAsUser } from "./helpers/auth";

const submitQuery = /send query|invia query/i;
const submitIngest = /submit to intake|invia a intake/i;

test.describe("Secure query and ingest workflows", () => {
  test.beforeEach(() => {
    test.skip(!process.env.E2E_LIVE, "Set E2E_LIVE=1 to run live workflow tests");
  });

  test("tenant switch, ingest, query, and explain preserve server authority", async ({ page }) => {
    await loginAsUser(page, DEMO_USERS.superadmin);
    const alternateTenant = process.env.E2E_SECOND_TENANT_ID;

    await page.goto("/en/chat", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#chat-query")).toHaveAccessibleName(/query input/i);
    await expect(page.getByRole("button", { name: submitQuery })).toBeVisible();

    if (alternateTenant) {
      await page.getByRole("button", { name: /tenant/i }).click();
      await page.getByText(alternateTenant, { exact: true }).click();
      await expect(page.getByText(new RegExp(`tenant ${alternateTenant}`, "i"))).toBeVisible();
    }

    const queryResponse = page.waitForResponse(
      (response) =>
        response.url().includes("/api/v1/query") && response.request().method() === "POST"
    );
    await page.locator("#chat-query").fill("What evidence is available to me?");
    await page.getByRole("button", { name: submitQuery }).click();
    const response = await queryResponse;
    expect(response.status()).toBe(200);
    const payload = (await response.json()) as { trace_id: string };

    await page.goto(`/en/explain?trace_id=${encodeURIComponent(payload.trace_id)}`, {
      waitUntil: "domcontentloaded",
    });
    await expect(page.locator("body")).toContainText(payload.trace_id);

    await page.goto("/en/ingest", { waitUntil: "domcontentloaded" });
    await expect(page.locator("#ingest-file")).toHaveAccessibleName(/choose an evidence file/i);
    await page.locator("#ingest-file").setInputFiles({
      name: "e2e-evidence.md",
      mimeType: "text/markdown",
      buffer: Buffer.from("# E2E evidence\nThis document is safe test content."),
    });
    const ingestResponse = page.waitForResponse(
      (candidate) =>
        candidate.url().includes("/api/v1/ingest") && candidate.request().method() === "POST"
    );
    await page.getByRole("button", { name: submitIngest }).click();
    expect([200, 201, 202]).toContain((await ingestResponse).status());
  });

  test("mobile drawer traps navigation controls and restores its trigger", async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== "mobile-chromium", "Mobile-only navigation check");
    await loginAsUser(page, DEMO_USERS.tenantAdmin);
    await page.goto("/en/chat", { waitUntil: "domcontentloaded" });

    const toggle = page.getByRole("button", { name: /toggle navigation/i });
    await toggle.click();
    await expect(toggle).toHaveAttribute("aria-expanded", "true");
    await expect(page.getByRole("dialog", { name: /primary navigation/i })).toBeVisible();

    await page.keyboard.press("Escape");
    await expect(toggle).toHaveAttribute("aria-expanded", "false");
    await expect(toggle).toBeFocused();
  });
});
