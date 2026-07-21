// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { Metadata } from "next";
import { cookies } from "next/headers";
import {
  LANGUAGE_PREFERENCE_KEY,
  normalizeLanguage,
} from "@/lib/language-preference";
import { DashboardClientLayout } from "./dashboard-client-layout";

export const metadata: Metadata = {
  title: "Dashboard | YiQiao",
  description:
    "YiQiao 自托管记忆管理仪表盘 | YiQiao self-hosted memory dashboard",
  icons: {
    icon: "/favicon.svg",
  },
};

export default async function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const cookieStore = await cookies();
  const initialLanguage = normalizeLanguage(
    cookieStore.get(LANGUAGE_PREFERENCE_KEY)?.value,
  );

  return (
    <DashboardClientLayout initialLanguage={initialLanguage}>
      {children}
    </DashboardClientLayout>
  );
}
