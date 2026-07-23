// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import "@/styles/globals.css";
import "@/styles/yiqiao-theme.css";
import { cookies } from "next/headers";
import { Inter, Fustat, Roboto, DMMono, InterDisplay } from "../(root)/fonts";
import { cn } from "@/lib/utils";
import { ThemeProvider } from "@/components/theme-provider";
import { AuthProvider } from "@/lib/auth";
import { I18nProvider } from "@/lib/i18n";
import {
  LANGUAGE_PREFERENCE_KEY,
  normalizeLanguage,
} from "@/lib/language-preference";

export const metadata = {
  title: "Setup | YiQiao",
  description: "配置你的 YiQiao 实例 | Set up your YiQiao instance",
  icons: {
    icon: "/favicon.svg",
  },
};

export default async function SetupLayout({
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
      data-brand-theme="yiqiao"
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
