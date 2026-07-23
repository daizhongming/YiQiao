// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import type { CSSProperties } from "react";
import NavWrapper from "./components/nav-wrapper";
import { SIDEBAR_WIDTH, COLLAPSED_SIDEBAR_WIDTH } from "../clientLayout";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { ScrollArea } from "@/components/ui/scroll-area";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const isSidebarCollapsed = useSelector(
    (state: RootState) => state.layout.isSidebarCollapsed,
  );
  const sidebarWidth = isSidebarCollapsed
    ? COLLAPSED_SIDEBAR_WIDTH
    : SIDEBAR_WIDTH;
  const shellStyle = {
    "--dashboard-sidebar-width": `${sidebarWidth}px`,
  } as CSSProperties;

  return (
    <div className="dashboard-shell" style={shellStyle}>
      <NavWrapper />
      <main className="dashboard-main font-fustat">
        <ScrollArea
          type="scroll"
          className="h-full [&_[data-radix-scroll-area-viewport]]:overflow-x-hidden [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0"
        >
          <div className="mx-auto flex w-full min-w-0 max-w-[1600px] flex-1 flex-col space-y-4 px-3 py-4 sm:px-5 sm:py-5 lg:px-7 lg:py-6">
            {children}
          </div>
        </ScrollArea>
      </main>
    </div>
  );
}
