"use client";

import React from "react";
import { NextIntlClientProvider } from "next-intl";
import LoginForm from "./LoginForm";
import itMessages from "../../../messages/it.json";

export default function LoginPage() {
  return (
    <NextIntlClientProvider locale="it" messages={itMessages}>
      <LoginForm />
    </NextIntlClientProvider>
  );
}
