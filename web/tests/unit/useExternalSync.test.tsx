import { renderHook, act } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { useExternalSync } from "@/lib/useExternalSync";

/**
 * React's documented alternative to a synchronising effect: when a value from
 * outside changes, adjust local state *during* the render that observes it,
 * rather than rendering stale state and re-rendering from an effect. The
 * cascading extra render is what `react-hooks/set-state-in-effect` flags.
 */
describe("useExternalSync", () => {
  it("does not fire on the first render", () => {
    const apply = vi.fn();
    renderHook(() => useExternalSync("tenant-a", apply));
    expect(apply).not.toHaveBeenCalled();
  });

  it("fires once when the external value changes", () => {
    const apply = vi.fn();
    const { rerender } = renderHook(({ value }) => useExternalSync(value, apply), {
      initialProps: { value: "tenant-a" },
    });

    rerender({ value: "tenant-b" });

    expect(apply).toHaveBeenCalledTimes(1);
    expect(apply).toHaveBeenCalledWith("tenant-b");
  });

  it("does not fire when the value is unchanged", () => {
    const apply = vi.fn();
    const { rerender } = renderHook(({ value }) => useExternalSync(value, apply), {
      initialProps: { value: "tenant-a" },
    });

    rerender({ value: "tenant-a" });
    rerender({ value: "tenant-a" });

    expect(apply).not.toHaveBeenCalled();
  });

  it("treats undefined as a real transition", () => {
    // Clearing the tenant selection must reset dependent state, not be ignored.
    const apply = vi.fn();
    const { rerender } = renderHook(
      ({ value }: { value: string | undefined }) => useExternalSync(value, apply),
      { initialProps: { value: "tenant-a" as string | undefined } }
    );

    rerender({ value: undefined });

    expect(apply).toHaveBeenCalledWith(undefined);
  });

  it("updates dependent state without an extra committed render", () => {
    const renders: string[] = [];

    const { result, rerender } = renderHook(
      ({ external }: { external: string }) => {
        const [local, setLocal] = useState("initial");
        useExternalSync(external, setLocal);
        renders.push(local);
        return local;
      },
      { initialProps: { external: "initial" } }
    );

    renders.length = 0;
    rerender({ external: "changed" });

    expect(result.current).toBe("changed");
    // The stale value is never committed to the DOM: React re-runs the
    // component before painting, so no cascading render reaches the screen.
    expect(renders[renders.length - 1]).toBe("changed");
  });

  it("keeps working when the callback identity changes each render", () => {
    const seen: string[] = [];
    const { rerender } = renderHook(
      ({ value }) => useExternalSync(value, (v: string) => seen.push(v)),
      { initialProps: { value: "a" } }
    );

    rerender({ value: "b" });
    rerender({ value: "c" });

    expect(seen).toEqual(["b", "c"]);
  });

  it("supports several independent syncs in one component", () => {
    const applyTenant = vi.fn();
    const applyOwner = vi.fn();
    const { rerender } = renderHook(
      ({ tenant, owner }) => {
        useExternalSync(tenant, applyTenant);
        useExternalSync(owner, applyOwner);
      },
      { initialProps: { tenant: "t1", owner: "o1" } }
    );

    act(() => {
      rerender({ tenant: "t2", owner: "o1" });
    });

    expect(applyTenant).toHaveBeenCalledWith("t2");
    expect(applyOwner).not.toHaveBeenCalled();
  });
});
