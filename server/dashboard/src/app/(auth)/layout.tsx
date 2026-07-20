// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import "@/styles/globals.css";
import React from "react";
import { cookies } from "next/headers";
import { Inter, InterDisplay, Roboto, Fustat, DMMono } from "../(root)/fonts";
import { cn } from "@/lib/utils";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import {
  LANGUAGE_PREFERENCE_KEY,
  normalizeLanguage,
} from "@/lib/language-preference";

export const metadata = {
  title: "YiQiao - Log in",
  description: "Log in to YiQiao",
  icons: {
    icon: "/favicon.svg",
  },
};

export default async function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const cookieStore = await cookies();
  const initialLanguage = normalizeLanguage(
    cookieStore.get(LANGUAGE_PREFERENCE_KEY)?.value,
  );

  return (
    <html
      lang={initialLanguage === "zh" ? "zh-CN" : "en"}
      className={cn(
        Fustat.variable,
        InterDisplay.variable,
        Inter.variable,
        Roboto.variable,
        DMMono.variable,
      )}
      data-scroll-behavior="smooth"
      suppressHydrationWarning
    >
      <body className="font-fustat" suppressHydrationWarning>
        <I18nProvider initialLanguage={initialLanguage}>
          <AuthProvider>
            <ThemeProvider
              attribute="class"
              defaultTheme="system"
              enableSystem
              disableTransitionOnChange
            >
              {children}
            </ThemeProvider>
          </AuthProvider>
        </I18nProvider>
      </body>
    </html>
  );
}
