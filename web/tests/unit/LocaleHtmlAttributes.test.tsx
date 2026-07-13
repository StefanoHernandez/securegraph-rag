import { render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { LocaleHtmlAttributes } from "@/components/i18n/LocaleHtmlAttributes";

describe("LocaleHtmlAttributes", () => {
  afterEach(() => {
    document.documentElement.lang = "en";
  });

  it("updates the document language when the active locale changes", () => {
    const view = render(<LocaleHtmlAttributes locale="it" />);
    expect(document.documentElement.lang).toBe("it");

    view.rerender(<LocaleHtmlAttributes locale="en" />);
    expect(document.documentElement.lang).toBe("en");
  });
});
