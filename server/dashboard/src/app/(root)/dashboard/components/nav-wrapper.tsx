// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useTheme } from "next-themes";
import { useDispatch, useSelector } from "react-redux";
import {
  Building2,
  ChartLine,
  Check,
  ChevronDown,
  Folder,
  LogOut,
  Monitor,
  Moon,
  PanelRight,
  Plus,
  Search,
  Settings,
  Sun,
} from "lucide-react";
import { MainNav } from "./main-nav";
import { COLLAPSED_SIDEBAR_WIDTH, SIDEBAR_WIDTH } from "../../clientLayout";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { LanguageToggle } from "@/components/i18n/language-toggle";
import { useAuth } from "@/hooks/use-auth";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { RootState } from "@/store/store";
import { toggleSidebar } from "@/store/reducers/layoutReducer";
import { api, getActiveProjectId, setActiveProjectId } from "@/utils/api";
import { SETTINGS_ENDPOINTS } from "@/utils/api-endpoints";

type OrgOption = { id: string; name: string };
type ProjectOption = {
  id: string;
  name: string;
  organization_id?: string;
  is_default?: boolean;
};

export default function NavWrapper() {
  const router = useRouter();
  const dispatch = useDispatch();
  const { theme, setTheme } = useTheme();
  const { user, logout } = useAuth();
  const { language, t } = useI18n();
  const isSidebarCollapsed = useSelector(
    (state: RootState) => state.layout.isSidebarCollapsed,
  );
  const [isMobile, setIsMobile] = useState(false);
  const [projects, setProjects] = useState<ProjectOption[]>([
    {
      id: "default-project",
      name: "default-project",
      organization_id: "org_default",
      is_default: true,
    },
  ]);
  const [organizations, setOrganizations] = useState<OrgOption[]>([
    { id: "org_default", name: "Default organization" },
  ]);
  const [activeOrg, setActiveOrg] = useState("org_default");
  const [activeProject, setActiveProject] = useState("default-project");
  const [orgSearch, setOrgSearch] = useState("");
  const [projectSearch, setProjectSearch] = useState("");

  const visuallyCollapsed = isMobile || isSidebarCollapsed;
  const sidebarWidth = visuallyCollapsed
    ? COLLAPSED_SIDEBAR_WIDTH
    : SIDEBAR_WIDTH;
  const userDisplayName = user?.name || user?.email || "Account";
  const userInitial = userDisplayName.charAt(0).toUpperCase();

  useEffect(() => {
    const query = window.matchMedia("(max-width: 767px)");
    const sync = () => setIsMobile(query.matches);
    sync();
    query.addEventListener("change", sync);
    return () => query.removeEventListener("change", sync);
  }, []);

  const loadWorkspace = useCallback(() => {
    const storedProject = getActiveProjectId();
    api
      .get(SETTINGS_ENDPOINTS.WORKSPACE)
      .then((response) => {
        const nextProjects: ProjectOption[] = Array.isArray(
          response.data?.projects,
        )
          ? response.data.projects
          : [];
        const nextOrganizations: OrgOption[] = Array.isArray(
          response.data?.organizations,
        )
          ? response.data.organizations
          : [];
        if (!nextProjects.length) return;
        const current =
          nextProjects.find((project) => project.id === storedProject) ||
          nextProjects[0];
        setProjects(nextProjects);
        setOrganizations(
          nextOrganizations.length
            ? nextOrganizations
            : [
                {
                  id: current.organization_id || "org_default",
                  name:
                    response.data?.organization?.name || "Default organization",
                },
              ],
        );
        setActiveProject(current.id);
        setActiveOrg(current.organization_id || "org_default");
        if (current.id !== storedProject) setActiveProjectId(current.id);
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    loadWorkspace();
    window.addEventListener("yiqiao-projects-updated", loadWorkspace);
    return () =>
      window.removeEventListener("yiqiao-projects-updated", loadWorkspace);
  }, [loadWorkspace]);

  const selectedOrganization =
    organizations.find((organization) => organization.id === activeOrg) ||
    organizations[0];
  const selectedProject =
    projects.find((project) => project.id === activeProject) || projects[0];
  const visibleProjects = projects.filter(
    (project) =>
      (project.organization_id || activeOrg) === activeOrg &&
      project.name.toLowerCase().includes(projectSearch.toLowerCase()),
  );
  const visibleOrganizations = organizations.filter((organization) =>
    organization.name.toLowerCase().includes(orgSearch.toLowerCase()),
  );

  const chooseProject = (projectId: string) => {
    setActiveProjectId(projectId);
    setActiveProject(projectId);
    window.location.reload();
  };

  const chooseOrganization = (organizationId: string) => {
    const nextProject =
      projects.find((project) => project.organization_id === organizationId) ||
      projects[0];
    setActiveOrg(organizationId);
    if (nextProject) chooseProject(nextProject.id);
  };

  const themeOptions = useMemo(
    () => [
      { value: "light", label: "Light", icon: Sun },
      { value: "dark", label: "Dark", icon: Moon },
      { value: "system", label: "System", icon: Monitor },
    ],
    [],
  );

  return (
    <>
      <aside
        id="dashboard-sidebar"
        className="fixed left-0 top-0 z-30 flex h-full flex-col overflow-hidden bg-surface-default-secondary transition-[width] duration-300"
        style={{ width: sidebarWidth }}
      >
        <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto overflow-x-hidden px-3 py-3">
          <DropdownMenu onOpenChange={(open) => !open && setOrgSearch("")}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className={cn(
                  "flex h-9 w-full shrink-0 items-center rounded-md border border-transparent text-left text-sm transition-colors hover:bg-surface-default-secondary-hover focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-memBorder-secondary",
                  visuallyCollapsed ? "justify-center px-0" : "gap-2 px-1.5",
                )}
                aria-label={`${t("Organization")}${
                  language === "zh" ? "：" : ": "
                }${t(selectedOrganization?.name || "Select organization")}`}
                title={
                  visuallyCollapsed ? selectedOrganization?.name : undefined
                }
              >
                <span className="grid size-7 shrink-0 place-items-center rounded-md bg-surface-default-tertiary">
                  <Building2 className="size-4" />
                </span>
                {!visuallyCollapsed && (
                  <>
                    <span className="min-w-0 flex-1 truncate text-xs font-medium">
                      {selectedOrganization?.name || "Select organization"}
                    </span>
                    <ChevronDown className="size-3.5 shrink-0 text-onSurface-default-tertiary" />
                  </>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              side={visuallyCollapsed ? "right" : "bottom"}
              align="start"
              sideOffset={6}
              className="w-72 rounded-md border-memBorder-secondary bg-surface-default-primary p-1 font-fustat"
            >
              <div
                className="relative p-1"
                onKeyDown={(event) => event.stopPropagation()}
              >
                <Search className="absolute left-3 top-3.5 size-4 text-onSurface-default-tertiary" />
                <Input
                  value={orgSearch}
                  onChange={(event) => setOrgSearch(event.target.value)}
                  placeholder="Search for organization"
                  className="h-9 pl-8"
                />
              </div>
              <DropdownMenuItem
                onSelect={() =>
                  router.push("/dashboard/settings?tab=org-general")
                }
                className="cursor-pointer gap-2"
              >
                <Plus className="size-4" />
                Create New
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {visibleOrganizations.map((organization) => (
                <DropdownMenuItem
                  key={organization.id}
                  onSelect={() => chooseOrganization(organization.id)}
                  className="cursor-pointer gap-2 py-2"
                >
                  <span className="grid size-7 shrink-0 place-items-center rounded-md bg-surface-default-tertiary">
                    <Building2 className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">
                      {organization.name}
                    </span>
                    {organization.id === "org_default" && (
                      <span className="block text-[10px] uppercase text-onSurface-default-tertiary">
                        Default
                      </span>
                    )}
                  </span>
                  {organization.id === activeOrg && (
                    <Check className="size-4 shrink-0" />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>

          <MainNav className="w-full" collapsed={visuallyCollapsed} />
        </div>
      </aside>

      <header
        className="fixed top-0 z-20 flex h-12 items-center justify-between gap-3 border-b border-memBorder-primary bg-surface-default-secondary px-3 font-fustat transition-[left,width] duration-300 sm:px-4"
        style={{
          left: sidebarWidth,
          width: `calc(100% - ${sidebarWidth}px)`,
        }}
      >
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={() => dispatch(toggleSidebar())}
            className="hidden size-8 shrink-0 items-center justify-center rounded-md text-onSurface-default-tertiary hover:bg-surface-default-secondary-hover hover:text-onSurface-default-primary md:inline-flex"
            aria-label={
              isSidebarCollapsed ? "Expand sidebar" : "Collapse sidebar"
            }
            aria-controls="dashboard-sidebar"
            aria-expanded={!isSidebarCollapsed}
          >
            <PanelRight className="size-4" />
          </button>

          <DropdownMenu onOpenChange={(open) => !open && setProjectSearch("")}>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex h-8 min-w-0 max-w-[210px] items-center gap-2 rounded-md border border-memBorder-primary bg-surface-default-primary px-2 text-left text-xs hover:bg-surface-default-primary-hover sm:w-44"
              >
                <Folder className="size-4 shrink-0 text-onSurface-default-tertiary" />
                <span className="min-w-0 flex-1 truncate">
                  {selectedProject?.name || "Select project"}
                </span>
                <ChevronDown className="size-3.5 shrink-0 text-onSurface-default-tertiary" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="start"
              sideOffset={6}
              className="w-72 rounded-md border-memBorder-secondary bg-surface-default-primary p-1 font-fustat"
            >
              <div
                className="relative p-1"
                onKeyDown={(event) => event.stopPropagation()}
              >
                <Search className="absolute left-3 top-3.5 size-4 text-onSurface-default-tertiary" />
                <Input
                  value={projectSearch}
                  onChange={(event) => setProjectSearch(event.target.value)}
                  placeholder="Search for project"
                  className="h-9 pl-8"
                />
              </div>
              <DropdownMenuItem
                onSelect={() =>
                  router.push("/dashboard/settings?tab=project-general")
                }
                className="cursor-pointer gap-2"
              >
                <Plus className="size-4" />
                Create New
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              {visibleProjects.map((project) => (
                <DropdownMenuItem
                  key={project.id}
                  onSelect={() => chooseProject(project.id)}
                  className="cursor-pointer gap-2 py-2"
                >
                  <span className="grid size-7 shrink-0 place-items-center rounded-md bg-surface-default-tertiary">
                    <Folder className="size-4" />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-sm">
                      {project.name}
                    </span>
                    {(project.is_default ||
                      project.id === "default-project") && (
                      <span className="block text-[10px] uppercase text-onSurface-default-tertiary">
                        Default
                      </span>
                    )}
                  </span>
                  {project.id === activeProject && (
                    <Check className="size-4 shrink-0" />
                  )}
                </DropdownMenuItem>
              ))}
            </DropdownMenuContent>
          </DropdownMenu>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          <div className="hidden sm:block">
            <LanguageToggle compact />
          </div>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                type="button"
                className="flex h-8 items-center gap-1.5 rounded-md px-1.5 hover:bg-surface-default-secondary-hover"
                aria-label={`Open account menu for ${userDisplayName}`}
              >
                <span className="grid size-7 place-items-center rounded-full bg-surface-default-tertiary text-xs font-semibold">
                  {userInitial}
                </span>
                <ChevronDown className="hidden size-3.5 text-onSurface-default-tertiary sm:block" />
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent
              align="end"
              sideOffset={6}
              className="w-64 rounded-md border-memBorder-secondary bg-surface-default-primary p-1 font-fustat"
            >
              <div className="flex items-center gap-2 px-2 py-2">
                <span className="grid size-9 shrink-0 place-items-center rounded-full bg-surface-default-tertiary text-sm font-semibold">
                  {userInitial}
                </span>
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium">
                    {userDisplayName}
                  </p>
                  <p className="truncate text-xs text-onSurface-default-tertiary">
                    {user?.email}
                  </p>
                </div>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem asChild className="cursor-pointer gap-2">
                <Link href="/dashboard/billing">
                  <ChartLine className="size-4" />
                  Usage
                </Link>
              </DropdownMenuItem>
              <DropdownMenuItem asChild className="cursor-pointer gap-2">
                <Link href="/dashboard/settings">
                  <Settings className="size-4" />
                  Settings
                </Link>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <div className="px-2 py-2">
                <p className="mb-2 text-xs text-onSurface-default-tertiary">
                  Theme
                </p>
                <div className="grid grid-cols-3 gap-1 rounded-md bg-surface-default-secondary p-1">
                  {themeOptions.map((option) => {
                    const Icon = option.icon;
                    const active = theme === option.value;
                    return (
                      <Button
                        key={option.value}
                        type="button"
                        variant={active ? "tertiary" : "ghost"}
                        size="icon"
                        className="h-8 w-full"
                        onClick={(event) => {
                          event.preventDefault();
                          setTheme(option.value);
                        }}
                        title={`${option.label} theme`}
                        aria-label={`${option.label} theme`}
                      >
                        <Icon className="size-4" />
                      </Button>
                    );
                  })}
                </div>
              </div>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                onSelect={() => void logout()}
                className="cursor-pointer gap-2"
              >
                <LogOut className="size-4" />
                Log out
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
      </header>
    </>
  );
}
