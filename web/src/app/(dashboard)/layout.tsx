"use client";

import React from "react";
import { NextIntlClientProvider } from "next-intl";
import DashboardShell from "@/components/layout/DashboardShell";
import itMessages from "../../../messages/it.json";

export default function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <NextIntlClientProvider locale="it" messages={itMessages}>
      <DashboardShell>{children}</DashboardShell>
    </NextIntlClientProvider>
  );
}
