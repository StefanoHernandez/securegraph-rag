"use client";

import { useCallback } from "react";
import { useTranslations } from "next-intl";
import { ApiError } from "@/lib/api";

/**
 * Codes this UI knows how to translate.
 *
 * An explicit allow-list rather than probing the message tree with an
 * arbitrary string: next-intl types `t.has` over the namespace's keys, and a
 * code the server introduced but this build has never heard of should fall to
 * the page's own message rather than render blank.
 */
export const ERROR_CODES: ReadonlySet<string> = new Set([
  "egress_blocked",
  "query_scope_invalid",
  "egress_url_invalid",
  "upload_too_large",
  "corpus_manifest_invalid",
  "ingestion_failed",
  "secret_unavailable",
  "csrf_invalid",
  "refresh_rejected",
  "forbidden",
  "not_found",
  "rate_limited",
  "internal_error",
  "service_unavailable",
  "gateway_timeout",
]);

type ErrorTranslator = ReturnType<typeof useTranslations<"errors">>;
type ErrorKey = Parameters<ErrorTranslator>[0];

/**
 * Turn an unknown thrown value into something worth showing a person.
 *
 * Order: a translated message for a code we know, then the caller's own
 * localized fallback, and never the backend's text. The fallbacks have existed
 * on every page since the UI was written — they were unreachable because the
 * thrown Error always had a message.
 *
 * A 5xx also gets its request id appended, so the person looking at the screen
 * can quote the same id that is on every server log line for that request.
 */
export function useErrorMessage() {
  const t = useTranslations("errors");
  return useCallback(
    (error: unknown, fallback: string): string => {
      if (!(error instanceof ApiError)) {
        return fallback;
      }
      let message = fallback;
      if (ERROR_CODES.has(error.code)) {
        const key = error.code as ErrorKey;
        if (t.has(key)) {
          message = t(key);
        }
      }
      if (error.status >= 500 && error.requestId) {
        return `${message} ${t("reference" as ErrorKey, { id: error.requestId })}`;
      }
      return message;
    },
    [t]
  );
}
