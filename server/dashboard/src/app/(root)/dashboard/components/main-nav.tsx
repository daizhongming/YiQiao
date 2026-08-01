// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useId, type ComponentType, type HTMLAttributes } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, useReducedMotion } from "framer-motion";
import {
  Activity,
  BookOpen,
  Box,
  ChartLine,
  ExternalLink,
  FolderInput,
  GalleryVerticalEnd,
  GitBranch,
  KeyRound,
  LayoutDashboard,
  MessageSquare,
  Newspaper,
  PlugZap,
  Settings,
  Users,
  Webhook,
  Wrench,
} from "lucide-react";
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupLabel,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarRail,
} from "@/components/ui/sidebar";
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";

type NavItem = {
  title: string;
  url: string;
  icon: ComponentType<{ className?: string }>;
  external?: boolean;
  active?: (pathname: string) => boolean;
};

const GROUPS: { label: string; items: NavItem[] }[] = [
  {
    label: "SETUP",
    items: [
      { title: "Integrations", url: "/dashboard/install", icon: Box },
      { title: "Playground", url: "/playground", icon: MessageSquare },
      { title: "API Keys", url: "/dashboard/api-keys", icon: KeyRound },
      {
        title: "Configuration",
        url: "/dashboard/configuration",
        icon: Wrench,
      },
    ],
  },
  {
    label: "ACTIVITY",
    items: [
      {
        title: "Dashboard",
        url: "/dashboard",
        icon: LayoutDashboard,
        active: (pathname) => pathname === "/dashboard",
      },
      { title: "Requests", url: "/dashboard/requests", icon: Activity },
      { title: "Entities", url: "/dashboard/entities", icon: Users },
      {
        title: "Memories",
        url: "/dashboard/memories",
        icon: GalleryVerticalEnd,
      },
      { title: "Graph", url: "/dashboard/graph", icon: GitBranch },
      { title: "Webhooks", url: "/dashboard/webhooks", icon: Webhook },
      {
        title: "Memory Exports",
        url: "/dashboard/memory-exports",
        icon: FolderInput,
      },
    ],
  },
  {
    label: "ACCOUNT",
    items: [
      { title: "Settings", url: "/dashboard/settings", icon: Settings },
      { title: "Usage", url: "/dashboard/billing", icon: ChartLine },
    ],
  },
  {
    label: "LEARN",
    items: [
      {
        title: "Docs",
        url: `${process.env.NEXT_PUBLIC_API_URL || ""}/docs`,
        icon: BookOpen,
        external: true,
      },
      {
        title: "Quick Start",
        url: "/dashboard/install",
        icon: PlugZap,
      },
      {
        title: "Playground",
        url: "/playground",
        icon: Newspaper,
      },
    ],
  },
];

const itemIsActive = (pathname: string, item: NavItem) => {
  if (item.external) return false;
  if (item.active) return item.active(pathname);
  return pathname === item.url || pathname.startsWith(`${item.url}/`);
};

export function MainNav({
  className,
  collapsed = false,
  onNavigate,
  ...props
}: HTMLAttributes<HTMLElement> & {
  collapsed?: boolean;
  onNavigate?: () => void;
}) {
  const pathname = usePathname();
  const { t } = useI18n();
  const reduceMotion = useReducedMotion();
  const navigationId = useId().replace(/:/g, "");

  return (
    <Sidebar
      collapsible={collapsed ? "icon" : undefined}
      className={cn("mb-0 w-full border-r-0 bg-transparent", className)}
      {...props}
    >
      <SidebarContent className="gap-0">
        <SidebarGroup className="p-0">
          <SidebarMenu className="gap-0">
            {GROUPS.map((group, groupIndex) => (
              <div
                key={group.label}
                className={cn(
                  "pb-3",
                  groupIndex > 0 && collapsed
                    ? "border-t border-memBorder-primary pt-3"
                    : groupIndex > 0
                      ? "pt-0"
                      : "",
                )}
              >
                {!collapsed && (
                  <SidebarGroupLabel className="mb-0 h-7 px-2 text-[10px] font-medium text-onSurface-default-tertiary">
                    {t(group.label)}
                  </SidebarGroupLabel>
                )}
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = itemIsActive(pathname, item);
                  const content = (
                    <>
                      {active && (
                        <motion.span
                          layoutId={`dashboard-nav-active-${navigationId}`}
                          className="dashboard-nav-active-indicator"
                          transition={{
                            duration: reduceMotion ? 0 : 0.24,
                            ease: [0.22, 1, 0.36, 1],
                          }}
                          aria-hidden="true"
                        />
                      )}
                      <Icon className="dashboard-nav-icon size-4 shrink-0" />
                      {!collapsed && (
                        <span className="min-w-0 flex-1 truncate">
                          {t(item.title)}
                        </span>
                      )}
                      {!collapsed && item.external && (
                        <ExternalLink className="size-3 shrink-0 text-onSurface-default-tertiary" />
                      )}
                    </>
                  );

                  return (
                    <SidebarMenuItem key={item.title}>
                      <SidebarMenuButton
                        asChild
                        collapsed={collapsed}
                        active={active}
                        tooltip={collapsed ? t(item.title) : undefined}
                        className="dashboard-nav-item relative min-h-9 overflow-visible"
                      >
                        {item.external ? (
                          <a
                            href={item.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            aria-current={undefined}
                            aria-label={collapsed ? t(item.title) : undefined}
                            title={collapsed ? t(item.title) : undefined}
                            onClick={onNavigate}
                            className={cn(
                              "flex w-full items-center",
                              collapsed ? "justify-center" : "gap-2",
                            )}
                          >
                            {content}
                          </a>
                        ) : (
                          <Link
                            href={item.url}
                            aria-current={active ? "page" : undefined}
                            aria-label={collapsed ? t(item.title) : undefined}
                            title={collapsed ? t(item.title) : undefined}
                            onClick={onNavigate}
                            className={cn(
                              "flex w-full items-center",
                              collapsed ? "justify-center" : "gap-2",
                            )}
                          >
                            {content}
                          </Link>
                        )}
                      </SidebarMenuButton>
                    </SidebarMenuItem>
                  );
                })}
              </div>
            ))}
          </SidebarMenu>
        </SidebarGroup>
      </SidebarContent>
      <SidebarRail />
    </Sidebar>
  );
}
