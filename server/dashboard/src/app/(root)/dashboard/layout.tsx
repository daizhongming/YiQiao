// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import NavWrapper from "./components/nav-wrapper";
import { SIDEBAR_WIDTH, COLLAPSED_SIDEBAR_WIDTH } from "../clientLayout";
import { useSelector } from "react-redux";
import { RootState } from "@/store/store";
import { ScrollArea } from "@/components/ui/scroll-area";
import { useEffect, useState } from "react";

export default function DashboardLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  const isSidebarCollapsed = useSelector(
    (state: RootState) => state.layout.isSidebarCollapsed,
  );
  const [isMobile, setIsMobile] = useState(false);
  const sidebarWidth =
    isMobile || isSidebarCollapsed ? COLLAPSED_SIDEBAR_WIDTH : SIDEBAR_WIDTH;

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsMobile(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  return (
    <>
      <NavWrapper />
      <div
        className="mt-[48px] rounded-tl-lg relative h-[calc(100vh-56px)] bg-surface-default-primary border border-memBorder-primary overflow-hidden transition-all duration-300 ease-in-out font-fustat"
        style={{
          left: `${sidebarWidth}px`,
          width: `calc(100vw - ${sidebarWidth + 8}px)`,
        }}
      >
        <ScrollArea
          type="scroll"
          className="h-[calc(100vh-70px)] [&_[data-radix-scroll-area-viewport]]:overflow-x-hidden [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0"
        >
          <div className="mx-auto flex w-full min-w-0 max-w-full flex-1 flex-col space-y-4 px-3 py-4 sm:px-6 sm:py-6">
            {children}
          </div>
        </ScrollArea>
      </div>
    </>
  );
}
