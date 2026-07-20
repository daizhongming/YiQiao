// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import React from "react";
import { ThemeProvider } from "@/components/theme-provider";
import "@/styles/globals.css";
import { ClientLayout } from "./clientLayout";
import { cn } from "@/lib/utils";
import { Inter, InterDisplay, Roboto, Fustat, DMMono } from "./fonts";
import { Provider } from "react-redux";
import store from "@/store/store";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import type { Language } from "@/lib/language-preference";
import dynamic from "next/dynamic";

const Toaster = dynamic(
  () =>
    import("@/components/ui/sonner").then((mod) => ({ default: mod.Toaster })),
  {
    ssr: false,
  },
);

export function DashboardClientLayout({
  children,
  initialLanguage,
}: Readonly<{
  children: React.ReactNode;
  initialLanguage: Language;
}>) {
  return (
    <html
      lang={initialLanguage === "zh" ? "zh-CN" : "en"}
      data-scroll-behavior="smooth"
      suppressHydrationWarning
    >
      <body
        className={cn(
          Inter.className,
          InterDisplay.variable,
          Roboto.variable,
          Fustat.variable,
          DMMono.variable,
        )}
        suppressHydrationWarning
      >
        <Provider store={store}>
          <I18nProvider initialLanguage={initialLanguage}>
            <AuthProvider>
              <ThemeProvider
                attribute="class"
                defaultTheme="light"
                enableSystem
                disableTransitionOnChange
              >
                <ClientLayout>{children}</ClientLayout>
                <Toaster />
              </ThemeProvider>
            </AuthProvider>
          </I18nProvider>
        </Provider>
      </body>
    </html>
  );
}
