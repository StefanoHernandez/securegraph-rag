export const locales = ["it", "en"] as const;
export const defaultLocale = "it";

export type Locale = (typeof locales)[number];

export function getPathLocale(pathname: string | null): Locale {
  const segment = pathname?.split("/").filter(Boolean)[0];
  return locales.includes(segment as Locale) ? (segment as Locale) : defaultLocale;
}

export function stripLocale(pathname: string | null): string {
  if (!pathname) {
    return "/";
  }
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length > 0 && locales.includes(parts[0] as Locale)) {
    return `/${parts.slice(1).join("/")}` || "/";
  }
  return pathname;
}

export function localizePath(href: string, pathname: string | null): string {
  if (!href.startsWith("/") || href.startsWith("/api")) {
    return href;
  }
  const locale = getPathLocale(pathname);
  const cleanHref = stripLocale(href);
  return cleanHref === "/" ? `/${locale}` : `/${locale}${cleanHref}`;
}
