"use client";

import React from "react";
import { NextIntlClientProvider } from "next-intl";
import DashboardShell from "@/components/layout/DashboardShell";
import { LocaleHtmlAttributes } from "@/components/i18n/LocaleHtmlAttributes";
import itMessages from "../../../messages/it.json";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <NextIntlClientProvider locale="it" messages={itMessages} timeZone="UTC">
      <LocaleHtmlAttributes locale="it" />
      <DashboardShell>{children}</DashboardShell>
    </NextIntlClientProvider>
  );
}
