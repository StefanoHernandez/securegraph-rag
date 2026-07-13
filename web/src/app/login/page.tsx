"use client";

import React from "react";
import { NextIntlClientProvider } from "next-intl";
import { LocaleHtmlAttributes } from "@/components/i18n/LocaleHtmlAttributes";
import LoginForm from "./LoginForm";
import itMessages from "../../../messages/it.json";

export default function LoginPage() {
  return (
    <NextIntlClientProvider locale="it" messages={itMessages} timeZone="UTC">
      <LocaleHtmlAttributes locale="it" />
      <LoginForm />
    </NextIntlClientProvider>
  );
}
