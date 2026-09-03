import { renderHook } from "@testing-library/react";
import { NextIntlClientProvider } from "next-intl";
import { describe, expect, it } from "vitest";
import { ApiError } from "@/lib/api";
import { ERROR_CODES, useErrorMessage } from "@/lib/errors";
import en from "@/../messages/en.json";
import itMessages from "@/../messages/it.json";

function wrapper(locale: string, messages: Record<string, unknown>) {
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <NextIntlClientProvider locale={locale} messages={messages}>
        {children}
      </NextIntlClientProvider>
    );
  };
}

const FALLBACK = "Impossibile caricare i tenant";

describe("useErrorMessage", () => {
  it("translates a known code", () => {
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("en", en),
    });
    const message = result.current(
      new ApiError(451, "egress_blocked", "Egress blocked by tenant policy", "id"),
      FALLBACK
    );
    expect(message).toBe((en as { errors: Record<string, string> }).errors.egress_blocked);
    expect(message).not.toBe(FALLBACK);
  });

  it("uses the page fallback for an unknown code", () => {
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("en", en),
    });
    expect(
      result.current(new ApiError(500, "some_new_code", "whatever", null), FALLBACK)
    ).toBe(FALLBACK);
  });

  it("uses the page fallback for anything that is not an ApiError", () => {
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("en", en),
    });
    expect(result.current(new Error("Unauthorized"), FALLBACK)).toBe(FALLBACK);
    expect(result.current("not even an error", FALLBACK)).toBe(FALLBACK);
  });

  it("never returns the backend detail verbatim", () => {
    const sentinel = "SELECT secret_column FROM classified_chunks";
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("en", en),
    });
    expect(
      result.current(new ApiError(500, "internal_error", sentinel, "id"), FALLBACK)
    ).not.toContain(sentinel);
  });

  it("appends the request id on a 5xx and omits it on a 4xx", () => {
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("en", en),
    });
    const server = result.current(
      new ApiError(500, "internal_error", "", "0f3c8a1e"),
      FALLBACK
    );
    const client = result.current(
      new ApiError(403, "forbidden", "", "0f3c8a1e"),
      FALLBACK
    );
    expect(server).toContain("0f3c8a1e");
    expect(client).not.toContain("0f3c8a1e");
  });

  it("translates in Italian too", () => {
    const { result } = renderHook(() => useErrorMessage(), {
      wrapper: wrapper("it", itMessages),
    });
    expect(
      result.current(new ApiError(403, "forbidden", "Forbidden", null), FALLBACK)
    ).toBe((itMessages as { errors: Record<string, string> }).errors.forbidden);
  });

  it("resolves every known code in both locales", () => {
    // Adding a code without translating it must fail here, not render blank.
    for (const [locale, messages] of [
      ["en", en],
      ["it", itMessages],
    ] as const) {
      const errors = (messages as { errors?: Record<string, string> }).errors ?? {};
      for (const code of ERROR_CODES) {
        expect(errors[code], `${code} missing in ${locale}`).toBeTruthy();
      }
    }
  });
});
