import { act, renderHook, waitFor } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { tenantApi } from "@/lib/api";
import { useTenantSelection } from "@/lib/useTenantSelection";
import en from "@/../messages/en.json";

function wrapper({ children }: { children: React.ReactNode }) {
  return (
    <NextIntlClientProvider locale="en" messages={en}>
      {children}
    </NextIntlClientProvider>
  );
}

vi.mock("@/lib/api", () => ({
  tenantApi: {
    listTenants: vi.fn(),
  },
}));

const listTenants = vi.mocked(tenantApi.listTenants);

describe("useTenantSelection", () => {
  beforeEach(() => {
    localStorage.clear();
    listTenants.mockResolvedValue([
      {
        id: "north",
        display_name: "North",
        status: "active",
        tenant_admin_settings_enabled: true,
      },
      {
        id: "south",
        display_name: "South",
        status: "active",
        tenant_admin_settings_enabled: true,
      },
    ]);
  });

  it("selects the session tenant and publishes later tenant changes", async () => {
    localStorage.setItem("securgraph_user", JSON.stringify({ tenant_id: "south" }));

    const { result } = renderHook(() => useTenantSelection(), { wrapper });

    await waitFor(() => expect(result.current.loadingTenants).toBe(false));
    expect(result.current.tenantId).toBe("south");
    expect(result.current.currentTenant?.display_name).toBe("South");

    act(() => result.current.selectTenant("north"));

    expect(result.current.tenantId).toBe("north");
    expect(result.current.currentTenant?.display_name).toBe("North");
    expect(localStorage.getItem("securgraph_tenant")).toBe("north");
  });
});
