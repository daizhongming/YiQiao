"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";
import { format } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import {
  Building2,
  CalendarDays,
  Check,
  ChevronDown,
  ChevronUp,
  Clock3,
  Copy,
  Folder,
  Languages,
  LockKeyhole,
  LogOut,
  Monitor,
  Moon,
  Pencil,
  Plus,
  RotateCcw,
  Save,
  SlidersHorizontal,
  Sun,
  Tags,
  Trash2,
  UserPlus,
  UserRound,
  Users,
  Wand2,
  X,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { Calendar } from "@/components/ui/calendar";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/use-toast";
import { useAuth } from "@/hooks/use-auth";
import { useApiQuery } from "@/hooks/use-api-query";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { getCategoryValidationError, isValidEmail } from "@/lib/validators";
import { api, getActiveProjectId, setActiveProjectId } from "@/utils/api";
import {
  AUTH_ENDPOINTS,
  MEMORY_ENDPOINTS,
  SETTINGS_ENDPOINTS,
} from "@/utils/api-endpoints";

type MemberRole = "READER" | "EDITOR" | "OWNER";

export type CloudSettingsSection =
  | "project-general"
  | "project-extraction"
  | "project-categories"
  | "project-retention"
  | "project-playground"
  | "org-general"
  | "org-members"
  | "profile";

type ExtractionSettings = {
  multilingual: boolean;
  use_case: string;
  memory_depth: string;
  include: string;
  exclude: string;
  custom_instructions: string;
};

type Category = { name: string; description: string };

type RetentionSettings = {
  memory_decay: boolean;
  expiration_date: string | null;
};

type PlaygroundSettings = {
  custom_instructions: string;
  categories: Category[];
  includes_prompt: string;
  excludes_prompt: string;
  force_add_only: boolean;
  reranking: boolean;
  temperature: number;
  threshold: number;
  max_tokens: number;
  top_k: number;
  top_p: number;
};

type WorkspaceSettings = {
  organization: { id: string; name: string };
  organizations: { id: string; name: string }[];
  active_organization_id: string;
  active_project_id: string;
  projects: {
    id: string;
    name: string;
    description: string;
    organization_id?: string;
    is_default?: boolean;
    extraction?: ExtractionSettings;
    categories?: Category[];
    retention?: RetentionSettings;
    playground?: PlaygroundSettings;
  }[];
  members: {
    email: string;
    role: MemberRole;
    status: "active" | "invited";
    project_id?: string;
    organization_id?: string;
  }[];
  extraction: ExtractionSettings;
  categories: Category[];
  retention: RetentionSettings;
  playground: PlaygroundSettings;
};

const DEFAULT_EXTRACTION: ExtractionSettings = {
  multilingual: true,
  use_case: "",
  memory_depth: "Essential Insights",
  include: "",
  exclude: "",
  custom_instructions: "",
};

const DEFAULT_RETENTION: RetentionSettings = {
  memory_decay: true,
  expiration_date: null,
};

const DEFAULT_PLAYGROUND: PlaygroundSettings = {
  custom_instructions: "",
  categories: [],
  includes_prompt: "",
  excludes_prompt: "",
  force_add_only: false,
  reranking: false,
  temperature: 0.1,
  threshold: 0.2,
  max_tokens: 2048,
  top_k: 10,
  top_p: 1,
};

const DEFAULT_WORKSPACE: WorkspaceSettings = {
  organization: { id: "org_default", name: "Default organization" },
  organizations: [{ id: "org_default", name: "Default organization" }],
  active_organization_id: "org_default",
  active_project_id: "default-project",
  projects: [
    {
      id: "default-project",
      name: "default-project",
      description: "",
      organization_id: "org_default",
      is_default: true,
    },
  ],
  members: [],
  extraction: DEFAULT_EXTRACTION,
  categories: [],
  retention: DEFAULT_RETENTION,
  playground: DEFAULT_PLAYGROUND,
};

type PlaygroundNumberField = {
  key: "temperature" | "threshold" | "top_p" | "top_k" | "max_tokens";
  label: string;
  step: number;
  min: number;
  max: number;
};

const PLAYGROUND_NUMBER_FIELDS: PlaygroundNumberField[] = [
  {
    key: "temperature",
    label: "Reply temperature",
    step: 0.01,
    min: 0,
    max: 2,
  },
  {
    key: "threshold",
    label: "Similarity threshold",
    step: 0.01,
    min: 0,
    max: 1,
  },
  { key: "top_p", label: "Reply Top P", step: 0.01, min: 0, max: 1 },
  { key: "top_k", label: "Retrieved memories", step: 1, min: 1, max: 100 },
  {
    key: "max_tokens",
    label: "Reply max tokens",
    step: 1,
    min: 1,
    max: 131072,
  },
];

const USE_CASES = [
  "Healthcare",
  "AI Companion",
  "Customer Support",
  "E-commerce",
  "Education",
  "Research",
  "Personal",
  "Others",
  "CODING_AGENT",
  "VOICE_AGENT",
  "OPENCLAW",
  "ENTERPRISE_SAAS",
  "Personalized Learning",
  "Assistant",
];

const MEMORY_DEPTHS = [
  "Essential Insights",
  "Balanced Context",
  "Comprehensive Knowledge",
];

const ROLE_DETAILS: Record<MemberRole, { label: string; description: string }> =
  {
    READER: {
      label: "Can Read",
      description: "Can make standard API requests and read basic data.",
    },
    EDITOR: {
      label: "Can Edit",
      description: "Can add, update, delete memories and manage entities.",
    },
    OWNER: {
      label: "Admin",
      description: "Full access including members and project settings.",
    },
  };

const SETTINGS_GROUPS: {
  label: string;
  items: {
    section: CloudSettingsSection;
    label: string;
    icon: typeof Folder;
  }[];
}[] = [
  {
    label: "Project",
    items: [
      { section: "project-general", label: "General", icon: Folder },
      {
        section: "project-extraction",
        label: "Extraction",
        icon: Languages,
      },
      { section: "project-categories", label: "Categories", icon: Tags },
      { section: "project-retention", label: "Retention", icon: Clock3 },
      {
        section: "project-playground",
        label: "Playground",
        icon: SlidersHorizontal,
      },
    ],
  },
  {
    label: "Organization",
    items: [
      { section: "org-general", label: "General", icon: Building2 },
      { section: "org-members", label: "Members", icon: Users },
    ],
  },
  {
    label: "Personal",
    items: [{ section: "profile", label: "Profile", icon: UserRound }],
  },
];

const sectionTitle = (section: CloudSettingsSection) => {
  if (section === "project-extraction") return "Extraction";
  if (section === "project-categories") return "Categories";
  if (section === "project-retention") return "Retention";
  if (section === "project-playground") return "Playground Settings";
  if (section === "org-members") return "Members";
  if (section === "profile") return "Profile";
  return "General";
};

function effectiveProjectSettings(
  workspace: WorkspaceSettings,
  projectId: string,
) {
  const project = workspace.projects.find((item) => item.id === projectId);
  return {
    extraction: {
      ...DEFAULT_EXTRACTION,
      ...(workspace.extraction || {}),
      ...(project?.extraction || {}),
    },
    categories: project?.categories ?? workspace.categories ?? [],
    retention: {
      ...DEFAULT_RETENTION,
      ...(workspace.retention || {}),
      ...(project?.retention || {}),
    },
    playground: {
      ...DEFAULT_PLAYGROUND,
      ...(workspace.playground || {}),
      ...(project?.playground || {}),
    },
  };
}

function hydrateProject(workspace: WorkspaceSettings, projectId: string) {
  return {
    ...workspace,
    active_project_id: projectId,
    ...effectiveProjectSettings(workspace, projectId),
  };
}

function SettingsPanel({
  children,
  className,
}: {
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <section
      className={cn(
        "overflow-hidden rounded-md border border-memBorder-primary bg-surface-default-primary",
        className,
      )}
    >
      {children}
    </section>
  );
}

function Field({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("space-y-1.5", className)}>
      <Label className="text-xs font-medium text-onSurface-default-secondary">
        {label}
      </Label>
      {children}
    </div>
  );
}

export default function SettingsCloudClient({
  section,
}: {
  section: CloudSettingsSection;
}) {
  const { user, isAdmin, logout, refreshUser } = useAuth();
  const { theme, setTheme } = useTheme();
  const { language } = useI18n();
  const [workspace, setWorkspace] =
    useState<WorkspaceSettings>(DEFAULT_WORKSPACE);
  const [activeProjectId, setActiveProject] = useState("default-project");
  const [busy, setBusy] = useState<string | null>(null);
  const [inviteOpen, setInviteOpen] = useState(false);
  const [inviteScope, setInviteScope] = useState<"organization" | "project">(
    "organization",
  );
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] = useState<MemberRole>("READER");
  const [createKind, setCreateKind] = useState<
    "organization" | "project" | null
  >(null);
  const [createName, setCreateName] = useState("");
  const [categoryName, setCategoryName] = useState("");
  const [categoryDescription, setCategoryDescription] = useState("");
  const [extractionMode, setExtractionMode] = useState<
    "configure" | "edit" | "view"
  >("configure");
  const [detailsOpen, setDetailsOpen] = useState(true);
  const [firstName, setFirstName] = useState("");
  const [lastName, setLastName] = useState("");
  const extractionInitialized = useRef(false);

  const { data, isLoading, refetch } = useApiQuery<WorkspaceSettings>(
    async () => (await api.get(SETTINGS_ENDPOINTS.WORKSPACE)).data,
    { errorToast: "Failed to load workspace settings" },
  );

  useEffect(() => {
    if (!data) return;
    const stored = getActiveProjectId();
    const projectId = data.projects.some((project) => project.id === stored)
      ? stored
      : data.projects.some((project) => project.id === data.active_project_id)
        ? data.active_project_id
        : data.projects[0]?.id || "default-project";
    const hydrated = hydrateProject(data, projectId);
    setWorkspace(hydrated);
    setActiveProject(projectId);
    setActiveProjectId(projectId);
    if (!extractionInitialized.current) {
      setExtractionMode(
        hydrated.extraction.custom_instructions ? "view" : "configure",
      );
      extractionInitialized.current = true;
    }
  }, [data]);

  useEffect(() => {
    const parts = (user?.name || "").trim().split(/\s+/).filter(Boolean);
    setFirstName(parts[0] || "");
    setLastName(parts.slice(1).join(" "));
  }, [user]);

  const organizations = workspace.organizations.length
    ? workspace.organizations
    : [workspace.organization];
  const selectedProject =
    workspace.projects.find((project) => project.id === activeProjectId) ||
    workspace.projects[0] ||
    DEFAULT_WORKSPACE.projects[0];
  const selectedOrganization =
    organizations.find(
      (org) =>
        org.id ===
        (selectedProject.organization_id || workspace.active_organization_id),
    ) ||
    organizations[0] ||
    DEFAULT_WORKSPACE.organization;

  const currentEmail = user?.email.toLowerCase();
  const canManageOrganization =
    isAdmin ||
    workspace.members.some(
      (member) =>
        member.email.toLowerCase() === currentEmail &&
        member.status === "active" &&
        member.role === "OWNER" &&
        !member.project_id &&
        member.organization_id === selectedOrganization.id,
    );
  const canManageProject =
    canManageOrganization ||
    workspace.members.some(
      (member) =>
        member.email.toLowerCase() === currentEmail &&
        member.status === "active" &&
        member.role === "OWNER" &&
        member.project_id === selectedProject.id,
    );

  const projectMembers = useMemo(
    () =>
      workspace.members.filter(
        (member) => member.project_id === selectedProject.id,
      ),
    [selectedProject.id, workspace.members],
  );
  const organizationMembers = useMemo(
    () =>
      workspace.members.filter(
        (member) =>
          !member.project_id &&
          member.organization_id === selectedOrganization.id,
      ),
    [selectedOrganization.id, workspace.members],
  );

  const membersFor = (scope: "organization" | "project") => {
    const scoped =
      scope === "organization" ? organizationMembers : projectMembers;
    if (
      !user ||
      scoped.some(
        (member) => member.email.toLowerCase() === user.email.toLowerCase(),
      )
    ) {
      return scoped;
    }
    return [
      {
        email: user.email,
        role: "OWNER" as MemberRole,
        status: "active" as const,
        project_id: scope === "project" ? selectedProject.id : undefined,
        organization_id: selectedOrganization.id,
      },
      ...scoped,
    ];
  };

  const notifyError = (title: string, error: unknown) =>
    toast({
      title,
      description: getErrorMessage(error),
      variant: "destructive",
    });

  const reloadWorkspace = async () => {
    await refetch();
    window.dispatchEvent(new Event("yiqiao-projects-updated"));
  };

  const updateProjectDraft = (
    patch: Partial<WorkspaceSettings["projects"][number]>,
  ) => {
    setWorkspace((current) => ({
      ...current,
      projects: current.projects.map((project) =>
        project.id === selectedProject.id ? { ...project, ...patch } : project,
      ),
    }));
  };

  const updateOrganizationDraft = (name: string) => {
    setWorkspace((current) => ({
      ...current,
      organizations: current.organizations.map((org) =>
        org.id === selectedOrganization.id ? { ...org, name } : org,
      ),
      organization:
        current.organization.id === selectedOrganization.id
          ? { ...current.organization, name }
          : current.organization,
    }));
  };

  const updateExtraction = (patch: Partial<ExtractionSettings>) =>
    setWorkspace((current) => ({
      ...current,
      extraction: { ...current.extraction, ...patch },
    }));

  const updateRetention = (patch: Partial<RetentionSettings>) =>
    setWorkspace((current) => ({
      ...current,
      retention: { ...current.retention, ...patch },
    }));

  const updatePlayground = (patch: Partial<PlaygroundSettings>) =>
    setWorkspace((current) => ({
      ...current,
      playground: { ...current.playground, ...patch },
    }));

  const saveProjectDetails = async () => {
    if (!canManageProject || !selectedProject.name.trim()) return;
    setBusy("project-details");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
        {
          name: selectedProject.name.trim(),
          description: selectedProject.description || "",
        },
      );
      await reloadWorkspace();
      toast({ title: "Project updated", variant: "success" });
    } catch (error) {
      notifyError("Failed to update project", error);
    } finally {
      setBusy(null);
    }
  };

  const saveOrganization = async () => {
    if (!canManageOrganization || !selectedOrganization.name.trim()) return;
    setBusy("organization");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORGANIZATION(selectedOrganization.id),
        { name: selectedOrganization.name.trim() },
      );
      await reloadWorkspace();
      toast({ title: "Organization updated", variant: "success" });
    } catch (error) {
      notifyError("Failed to update organization", error);
    } finally {
      setBusy(null);
    }
  };

  const createScope = async () => {
    const name = createName.trim();
    if (!createKind || !name) return;
    setBusy("create");
    try {
      if (createKind === "project") {
        const response = await api.post<WorkspaceSettings["projects"][number]>(
          SETTINGS_ENDPOINTS.ORG_PROJECTS(selectedOrganization.id),
          { name },
        );
        setActiveProjectId(response.data.id);
        setActiveProject(response.data.id);
      } else {
        const org = await api.post<{ id: string; name: string }>(
          SETTINGS_ENDPOINTS.ORGANIZATIONS,
          { name },
        );
        const project = await api.post<WorkspaceSettings["projects"][number]>(
          SETTINGS_ENDPOINTS.ORG_PROJECTS(org.data.id),
          {
            name: "default-project",
          },
        );
        setActiveProjectId(project.data.id);
        setActiveProject(project.data.id);
      }
      setCreateKind(null);
      setCreateName("");
      await reloadWorkspace();
      toast({
        title: `${createKind === "project" ? "Project" : "Organization"} created`,
        variant: "success",
      });
    } catch (error) {
      notifyError("Failed to create workspace", error);
    } finally {
      setBusy(null);
    }
  };

  const deleteProject = async () => {
    if (
      !canManageProject ||
      selectedProject.is_default ||
      selectedProject.id === "default-project" ||
      !window.confirm(
        `Delete project "${selectedProject.name}" and all of its data?`,
      )
    ) {
      return;
    }
    setBusy("delete-project");
    try {
      await api.delete(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
      );
      const fallback = workspace.projects.find(
        (project) => project.id !== selectedProject.id,
      );
      if (fallback) {
        setActiveProjectId(fallback.id);
        setActiveProject(fallback.id);
      }
      await reloadWorkspace();
      toast({ title: "Project deleted", variant: "success" });
    } catch (error) {
      notifyError("Failed to delete project", error);
    } finally {
      setBusy(null);
    }
  };

  const deleteOrganization = async () => {
    if (
      !canManageOrganization ||
      selectedOrganization.id === "org_default" ||
      !window.confirm(
        `Delete organization "${selectedOrganization.name}" and all projects in it?`,
      )
    ) {
      return;
    }
    setBusy("delete-organization");
    try {
      await api.delete(
        SETTINGS_ENDPOINTS.ORGANIZATION(selectedOrganization.id),
      );
      await reloadWorkspace();
      toast({ title: "Organization deleted", variant: "success" });
    } catch (error) {
      notifyError("Failed to delete organization", error);
    } finally {
      setBusy(null);
    }
  };

  const deleteAllMemories = async () => {
    if (
      !canManageProject ||
      !window.confirm(
        `Delete every memory in "${selectedProject.name}"? This cannot be undone.`,
      )
    ) {
      return;
    }
    setBusy("delete-memories");
    try {
      await api.delete(MEMORY_ENDPOINTS.BASE);
      toast({ title: "All project memories deleted", variant: "success" });
    } catch (error) {
      notifyError("Failed to delete memories", error);
    } finally {
      setBusy(null);
    }
  };

  const inviteMember = async () => {
    const email = inviteEmail.trim().toLowerCase();
    if (!email) return;
    if (!isValidEmail(email)) {
      toast({
        title: "Invite Member",
        description: "Enter a valid email address.",
        variant: "destructive",
      });
      return;
    }
    setBusy("invite");
    try {
      const endpoint =
        inviteScope === "organization"
          ? SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id)
          : SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
              selectedOrganization.id,
              selectedProject.id,
            );
      await api.post(endpoint, { email, role: inviteRole });
      setInviteOpen(false);
      setInviteEmail("");
      await reloadWorkspace();
      toast({ title: "Member invited", variant: "success" });
    } catch (error) {
      notifyError("Failed to invite member", error);
    } finally {
      setBusy(null);
    }
  };

  const updateMemberRole = async (
    scope: "organization" | "project",
    email: string,
    role: MemberRole,
  ) => {
    setBusy(`role-${email}`);
    try {
      const endpoint =
        scope === "organization"
          ? SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id)
          : SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
              selectedOrganization.id,
              selectedProject.id,
            );
      await api.put(endpoint, { email, role });
      await reloadWorkspace();
      toast({ title: "Member role updated", variant: "success" });
    } catch (error) {
      notifyError("Failed to update member role", error);
    } finally {
      setBusy(null);
    }
  };

  const removeMember = async (
    scope: "organization" | "project",
    email: string,
  ) => {
    if (!window.confirm(`Remove ${email} from this ${scope}?`)) return;
    setBusy(`remove-${email}`);
    try {
      const endpoint =
        scope === "organization"
          ? SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id)
          : SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
              selectedOrganization.id,
              selectedProject.id,
            );
      await api.delete(endpoint, {
        ...(scope === "organization"
          ? { data: { email } }
          : { params: { email } }),
      });
      await reloadWorkspace();
      toast({ title: "Member removed", variant: "success" });
    } catch (error) {
      notifyError("Failed to remove member", error);
    } finally {
      setBusy(null);
    }
  };

  const saveExtraction = async () => {
    setBusy("extraction");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
        { extraction: workspace.extraction },
      );
      setExtractionMode("view");
      await reloadWorkspace();
      toast({ title: "Extraction settings saved", variant: "success" });
    } catch (error) {
      notifyError("Failed to save extraction settings", error);
    } finally {
      setBusy(null);
    }
  };

  const generateInstructions = async () => {
    setBusy("generate");
    try {
      const response = await api.post(
        MEMORY_ENDPOINTS.GENERATE_INSTRUCTIONS,
        {
          use_case: workspace.extraction.use_case || "General assistant memory",
          memory_depth: workspace.extraction.memory_depth,
          include: workspace.extraction.include,
          exclude: workspace.extraction.exclude,
          multilingual: workspace.extraction.multilingual,
        },
        { timeout: 75_000 },
      );
      updateExtraction({
        custom_instructions: response.data.custom_instructions || "",
      });
      setExtractionMode("edit");
      toast({ title: "Instructions generated", variant: "success" });
    } catch (error) {
      notifyError("Failed to generate instructions", error);
    } finally {
      setBusy(null);
    }
  };

  const saveCategories = async (categories: Category[]) => {
    const invalidCategory = getCategoryValidationError(categories);
    if (invalidCategory) {
      toast({
        title: "Categories",
        description: invalidCategory,
        variant: "destructive",
      });
      return false;
    }
    setBusy("categories");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
        { categories },
      );
      setWorkspace((current) => ({ ...current, categories }));
      await reloadWorkspace();
      toast({ title: "Categories saved", variant: "success" });
      return true;
    } catch (error) {
      notifyError("Failed to save categories", error);
      return false;
    } finally {
      setBusy(null);
    }
  };

  const addCategory = async () => {
    const name = categoryName.trim();
    if (!name) return;
    const next = [
      ...workspace.categories.filter(
        (category) => category.name.toLowerCase() !== name.toLowerCase(),
      ),
      { name, description: categoryDescription.trim() },
    ];
    const saved = await saveCategories(next);
    if (!saved) return;
    setCategoryName("");
    setCategoryDescription("");
  };

  const saveRetention = async () => {
    setBusy("retention");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
        { retention: workspace.retention },
      );
      await reloadWorkspace();
      toast({ title: "Retention settings saved", variant: "success" });
    } catch (error) {
      notifyError("Failed to save retention settings", error);
    } finally {
      setBusy(null);
    }
  };

  const validatePlayground = () => {
    const playground = workspace.playground;
    return [
      !Number.isFinite(playground.temperature) ||
      playground.temperature < 0 ||
      playground.temperature > 2
        ? "Temperature must be between 0 and 2."
        : "",
      !Number.isFinite(playground.threshold) ||
      playground.threshold < 0 ||
      playground.threshold > 1
        ? "Threshold must be between 0 and 1."
        : "",
      !Number.isFinite(playground.top_p) ||
      playground.top_p < 0 ||
      playground.top_p > 1
        ? "Top P must be between 0 and 1."
        : "",
      !Number.isInteger(playground.top_k) || playground.top_k < 1
        ? "Top K must be a positive integer."
        : "",
      playground.top_k > 100 ? "Top K must not exceed 100." : "",
      !Number.isInteger(playground.max_tokens) || playground.max_tokens < 1
        ? "Max tokens must be a positive integer."
        : "",
      playground.max_tokens > 131072
        ? "Max tokens must not exceed 131072."
        : "",
    ].find(Boolean);
  };

  const savePlayground = async () => {
    const invalidValue = validatePlayground();
    if (invalidValue) {
      toast({
        title: "Invalid playground settings",
        description: invalidValue,
        variant: "destructive",
      });
      return;
    }
    if (!canManageProject) return;

    setBusy("playground");
    try {
      await api.patch(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
        { playground: workspace.playground },
      );
      await reloadWorkspace();
      toast({ title: "Settings saved", variant: "success" });
    } catch (error) {
      notifyError("Failed to save settings", error);
    } finally {
      setBusy(null);
    }
  };

  const saveProfile = async () => {
    const name = `${firstName.trim()} ${lastName.trim()}`.trim();
    if (!name) return;
    setBusy("profile");
    try {
      await api.patch(AUTH_ENDPOINTS.ME, { name });
      await refreshUser();
      toast({ title: "Profile updated", variant: "success" });
    } catch (error) {
      notifyError("Failed to update profile", error);
    } finally {
      setBusy(null);
    }
  };

  const deleteAccount = async () => {
    if (
      !window.confirm(
        "Delete your YiQiao account and revoke all of its credentials?",
      )
    ) {
      return;
    }
    setBusy("delete-account");
    try {
      await api.delete(AUTH_ENDPOINTS.DELETE_ACCOUNT);
      await logout();
    } catch (error) {
      notifyError("Failed to delete account", error);
      setBusy(null);
    }
  };

  const openInvite = (scope: "organization" | "project") => {
    setInviteScope(scope);
    setInviteOpen(true);
  };

  const renderMembers = (scope: "organization" | "project") => {
    const members = membersFor(scope);
    const canManage =
      scope === "organization" ? canManageOrganization : canManageProject;
    return (
      <div className="overflow-x-auto">
        <table className="w-full min-w-[560px] text-left text-sm">
          <thead className="border-b border-memBorder-primary bg-surface-default-secondary text-xs text-onSurface-default-tertiary">
            <tr>
              <th className="px-4 py-3 font-medium">Member Name</th>
              <th className="w-48 px-4 py-3 font-medium">Seat Type</th>
              <th className="w-14 px-4 py-3" />
            </tr>
          </thead>
          <tbody className="divide-y divide-memBorder-primary">
            {members.map((member) => {
              const isSelf =
                member.email.toLowerCase() === user?.email.toLowerCase();
              return (
                <tr key={`${scope}-${member.email}`}>
                  <td className="px-4 py-3">
                    <div className="min-w-0">
                      <p className="truncate text-onSurface-default-primary">
                        {member.email}
                      </p>
                      {member.status === "invited" && (
                        <p className="text-xs text-onSurface-default-tertiary">
                          Invitation pending
                        </p>
                      )}
                    </div>
                  </td>
                  <td className="px-4 py-3">
                    <select
                      value={member.role}
                      disabled={
                        isSelf || !canManage || busy === `role-${member.email}`
                      }
                      onChange={(event) =>
                        void updateMemberRole(
                          scope,
                          member.email,
                          event.target.value as MemberRole,
                        )
                      }
                      className="h-8 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-2 text-xs"
                    >
                      {(Object.keys(ROLE_DETAILS) as MemberRole[]).map(
                        (role) => (
                          <option key={role} value={role}>
                            {ROLE_DETAILS[role].label}
                          </option>
                        ),
                      )}
                    </select>
                  </td>
                  <td className="px-4 py-3 text-right">
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon"
                      className="size-8"
                      disabled={
                        isSelf ||
                        !canManage ||
                        busy === `remove-${member.email}`
                      }
                      onClick={() => void removeMember(scope, member.email)}
                      title={`Remove ${member.email}`}
                      aria-label={`Remove ${member.email}`}
                    >
                      <Trash2 className="size-4" />
                    </Button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    );
  };

  if (isLoading && !data) {
    return (
      <div className="mx-auto grid max-w-[1040px] gap-8 lg:grid-cols-[176px_minmax(0,1fr)]">
        <div className="h-72 animate-pulse rounded-md bg-surface-default-secondary" />
        <div className="h-96 animate-pulse rounded-md bg-surface-default-secondary" />
      </div>
    );
  }

  return (
    <div className="mx-auto grid w-full max-w-[1040px] min-w-0 gap-6 lg:grid-cols-[176px_minmax(0,1fr)] lg:gap-9">
      <aside className="min-w-0 lg:sticky lg:top-0 lg:self-start">
        <nav
          aria-label="Settings sections"
          className="flex gap-2 overflow-x-auto pb-2 lg:block lg:space-y-5 lg:overflow-visible lg:pb-0"
        >
          {SETTINGS_GROUPS.map((group) => (
            <div key={group.label} className="shrink-0 lg:space-y-1">
              <p className="mb-1 hidden px-2 text-xs font-medium text-onSurface-default-tertiary lg:block">
                {group.label}
              </p>
              <div className="flex gap-1 lg:block lg:space-y-0.5">
                {group.items.map((item) => {
                  const Icon = item.icon;
                  const active = item.section === section;
                  return (
                    <Link
                      key={item.section}
                      href={`/dashboard/settings?tab=${item.section}`}
                      aria-current={active ? "page" : undefined}
                      className={cn(
                        "flex h-9 items-center gap-2 whitespace-nowrap rounded-md px-2.5 text-sm transition-colors",
                        active
                          ? "bg-surface-default-tertiary font-medium text-onSurface-default-primary"
                          : "text-onSurface-default-secondary hover:bg-surface-default-secondary-hover",
                      )}
                    >
                      <Icon className="size-4 shrink-0" />
                      {item.label}
                    </Link>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>

      <main className="min-w-0 space-y-5">
        <header className="flex min-h-9 flex-wrap items-center justify-between gap-3">
          <h1 className="text-xl font-semibold text-onSurface-default-primary">
            {sectionTitle(section)}
          </h1>
          <div className="flex flex-wrap items-center gap-2">
            {section === "project-general" && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setCreateKind("project");
                    setCreateName("");
                  }}
                  disabled={!canManageOrganization}
                >
                  <Plus className="mr-1.5 size-4" />
                  Create New Project
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => openInvite("project")}
                  disabled={!canManageProject}
                >
                  <UserPlus className="mr-1.5 size-4" />
                  Invite Member
                </Button>
              </>
            )}
            {section === "org-general" && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => {
                  setCreateKind("organization");
                  setCreateName("");
                }}
                disabled={!isAdmin}
              >
                <Plus className="mr-1.5 size-4" />
                Create New Organization
              </Button>
            )}
            {section === "org-members" && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => openInvite("organization")}
                disabled={!canManageOrganization}
              >
                <UserPlus className="mr-1.5 size-4" />
                Invite Member
              </Button>
            )}
            {section === "project-extraction" && extractionMode === "view" && (
              <>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => setExtractionMode("edit")}
                >
                  <Pencil className="mr-1.5 size-4" />
                  Edit
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    void navigator.clipboard.writeText(
                      workspace.extraction.custom_instructions,
                    );
                    toast({ title: "Instructions copied", variant: "success" });
                  }}
                >
                  <Copy className="mr-1.5 size-4" />
                  Copy
                </Button>
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => setExtractionMode("configure")}
                >
                  <RotateCcw className="mr-1.5 size-4" />
                  Reconfigure
                </Button>
              </>
            )}
          </div>
        </header>

        {section === "project-general" && (
          <>
            <SettingsPanel>
              <div className="space-y-5 p-5">
                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <Field label="Project Name">
                    <Input
                      value={selectedProject.name}
                      onChange={(event) =>
                        updateProjectDraft({ name: event.target.value })
                      }
                      onBlur={() => void saveProjectDetails()}
                      disabled={!canManageProject}
                    />
                  </Field>
                  {(selectedProject.is_default ||
                    selectedProject.id === "default-project") && (
                    <span className="h-6 justify-self-start rounded border border-memBorder-primary px-2 py-1 text-xs text-onSurface-default-tertiary sm:mt-7">
                      Default
                    </span>
                  )}
                </div>
                <Field label="Project ID">
                  <div className="flex gap-2">
                    <Input value={selectedProject.id} disabled />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() =>
                        void navigator.clipboard.writeText(selectedProject.id)
                      }
                      title="Copy project ID"
                      aria-label="Copy project ID"
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </Field>
                <Field label="Project Description">
                  <Textarea
                    value={selectedProject.description || ""}
                    onChange={(event) =>
                      updateProjectDraft({ description: event.target.value })
                    }
                    onBlur={() => void saveProjectDetails()}
                    placeholder="Enter project description..."
                    disabled={!canManageProject}
                    className="min-h-24 resize-y"
                  />
                </Field>
                {busy === "project-details" && (
                  <p className="text-xs text-onSurface-default-tertiary">
                    Saving project...
                  </p>
                )}
              </div>
            </SettingsPanel>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">Members</h2>
              <SettingsPanel>{renderMembers("project")}</SettingsPanel>
            </section>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">Danger Zone</h2>
              <SettingsPanel>
                <div className="flex flex-col gap-3 border-b border-memBorder-primary p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h3 className="text-sm font-medium">Delete all memories</h3>
                    <p className="mt-1 text-xs text-onSurface-default-tertiary">
                      Permanently remove every memory in this project. This
                      cannot be undone.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="shrink-0"
                    onClick={() => void deleteAllMemories()}
                    disabled={!canManageProject || busy === "delete-memories"}
                  >
                    Delete All Memories
                  </Button>
                </div>
                <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h3 className="text-sm font-medium">Delete project</h3>
                    <p className="mt-1 text-xs text-onSurface-default-tertiary">
                      Permanently delete this project and all data associated
                      with it.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="shrink-0"
                    onClick={() => void deleteProject()}
                    disabled={
                      !canManageProject ||
                      selectedProject.is_default ||
                      selectedProject.id === "default-project" ||
                      busy === "delete-project"
                    }
                  >
                    Delete Project
                  </Button>
                </div>
              </SettingsPanel>
            </section>
          </>
        )}

        {section === "project-extraction" && (
          <SettingsPanel>
            <div className="flex items-center justify-between gap-4 p-5">
              <div>
                <h2 className="text-sm font-semibold">
                  Multilingual Memory Extraction
                </h2>
                <p className="mt-1 text-xs text-onSurface-default-tertiary">
                  Memories stored in your input language.
                </p>
              </div>
              <Switch
                checked={workspace.extraction.multilingual}
                onCheckedChange={(checked) =>
                  updateExtraction({ multilingual: checked })
                }
                disabled={!canManageProject}
              />
            </div>
            <div className="border-t border-memBorder-primary p-5">
              {extractionMode === "configure" && (
                <div className="space-y-4">
                  <div className="grid gap-4 md:grid-cols-2">
                    <Field label="Select Usecase">
                      <select
                        value={workspace.extraction.use_case}
                        onChange={(event) =>
                          updateExtraction({ use_case: event.target.value })
                        }
                        className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                        disabled={!canManageProject}
                      >
                        <option value="">Select a usecase</option>
                        {USE_CASES.map((item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    </Field>
                    <Field label="Choose Memory Depth">
                      <select
                        value={workspace.extraction.memory_depth}
                        onChange={(event) =>
                          updateExtraction({ memory_depth: event.target.value })
                        }
                        className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                        disabled={!canManageProject}
                      >
                        {MEMORY_DEPTHS.map((item) => (
                          <option key={item} value={item}>
                            {item}
                          </option>
                        ))}
                      </select>
                    </Field>
                  </div>
                  <Field label="Specify any additional elements you want to include in your instructions">
                    <Textarea
                      value={workspace.extraction.include}
                      onChange={(event) =>
                        updateExtraction({ include: event.target.value })
                      }
                      placeholder="Enter any specific data points, formats, or information you want to include..."
                      disabled={!canManageProject}
                    />
                  </Field>
                  <Field label="Specify any elements you want to exclude from your instructions">
                    <Textarea
                      value={workspace.extraction.exclude}
                      onChange={(event) =>
                        updateExtraction({ exclude: event.target.value })
                      }
                      placeholder="Enter any data points, formats, or information you want to exclude..."
                      disabled={!canManageProject}
                    />
                  </Field>
                  <div className="flex flex-wrap justify-end gap-2 pt-1">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() => setExtractionMode("edit")}
                      disabled={!canManageProject}
                    >
                      Skip to Manual Customization
                    </Button>
                    <Button
                      type="button"
                      variant="primary"
                      onClick={() => void generateInstructions()}
                      disabled={
                        !canManageProject ||
                        !workspace.extraction.use_case ||
                        busy === "generate"
                      }
                    >
                      <Wand2 className="mr-2 size-4" />
                      {busy === "generate"
                        ? "Generating..."
                        : "Generate Instructions"}
                    </Button>
                  </div>
                </div>
              )}

              {extractionMode === "edit" && (
                <div className="space-y-4">
                  <Textarea
                    value={workspace.extraction.custom_instructions}
                    onChange={(event) =>
                      updateExtraction({
                        custom_instructions: event.target.value,
                      })
                    }
                    placeholder="Enter custom instructions..."
                    className="min-h-52 resize-y font-mono text-xs"
                    disabled={!canManageProject}
                  />
                  <button
                    type="button"
                    className="flex items-center gap-1.5 text-xs font-medium text-onSurface-default-secondary"
                    onClick={() => setDetailsOpen((current) => !current)}
                  >
                    {detailsOpen ? (
                      <ChevronUp className="size-4" />
                    ) : (
                      <ChevronDown className="size-4" />
                    )}
                    {detailsOpen ? "Hide Details" : "Show Details"}
                  </button>
                  {detailsOpen && (
                    <div className="grid gap-4 rounded-md border border-memBorder-primary p-4 md:grid-cols-2">
                      <Field label="Usecase">
                        <select
                          value={workspace.extraction.use_case}
                          onChange={(event) =>
                            updateExtraction({ use_case: event.target.value })
                          }
                          className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                        >
                          <option value="">Select a usecase</option>
                          {USE_CASES.map((item) => (
                            <option key={item}>{item}</option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Memory Depth">
                        <select
                          value={workspace.extraction.memory_depth}
                          onChange={(event) =>
                            updateExtraction({
                              memory_depth: event.target.value,
                            })
                          }
                          className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                        >
                          {MEMORY_DEPTHS.map((item) => (
                            <option key={item}>{item}</option>
                          ))}
                        </select>
                      </Field>
                      <Field label="Inclusions">
                        <Textarea
                          value={workspace.extraction.include}
                          onChange={(event) =>
                            updateExtraction({ include: event.target.value })
                          }
                        />
                      </Field>
                      <Field label="Exclusions">
                        <Textarea
                          value={workspace.extraction.exclude}
                          onChange={(event) =>
                            updateExtraction({ exclude: event.target.value })
                          }
                        />
                      </Field>
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    <Button
                      type="button"
                      variant="outline"
                      onClick={() =>
                        setExtractionMode(
                          workspace.extraction.custom_instructions
                            ? "view"
                            : "configure",
                        )
                      }
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      variant="primary"
                      onClick={() => void saveExtraction()}
                      disabled={!canManageProject || busy === "extraction"}
                    >
                      <Save className="mr-2 size-4" />
                      {busy === "extraction" ? "Saving..." : "Save Changes"}
                    </Button>
                  </div>
                </div>
              )}

              {extractionMode === "view" && (
                <div className="space-y-4">
                  <div className="min-h-48 whitespace-pre-wrap rounded-md border border-memBorder-primary bg-surface-default-secondary p-4 font-mono text-xs leading-5">
                    {workspace.extraction.custom_instructions ||
                      "No custom extraction instructions."}
                  </div>
                  <div className="grid gap-3 text-sm sm:grid-cols-2">
                    <div>
                      <p className="text-xs text-onSurface-default-tertiary">
                        Usecase
                      </p>
                      <p>{workspace.extraction.use_case || "Not selected"}</p>
                    </div>
                    <div>
                      <p className="text-xs text-onSurface-default-tertiary">
                        Memory Depth
                      </p>
                      <p>{workspace.extraction.memory_depth}</p>
                    </div>
                  </div>
                </div>
              )}
            </div>
          </SettingsPanel>
        )}

        {section === "project-categories" && (
          <SettingsPanel>
            <div className="grid gap-4 p-5 md:grid-cols-[minmax(0,0.8fr)_minmax(0,1.4fr)_auto] md:items-end">
              <Field label="Add Category">
                <Input
                  value={categoryName}
                  onChange={(event) => setCategoryName(event.target.value)}
                  placeholder="e.g. Sports"
                  disabled={!canManageProject}
                />
              </Field>
              <Field label="Description">
                <Input
                  value={categoryDescription}
                  onChange={(event) =>
                    setCategoryDescription(event.target.value)
                  }
                  placeholder="e.g. Anything related to sports, teams, or athletes"
                  disabled={!canManageProject}
                />
              </Field>
              <Button
                type="button"
                variant="primary"
                onClick={() => void addCategory()}
                disabled={
                  !canManageProject ||
                  !categoryName.trim() ||
                  busy === "categories"
                }
              >
                Save
              </Button>
            </div>
            <div className="border-t border-memBorder-primary">
              {workspace.categories.length === 0 ? (
                <p className="px-5 py-8 text-center text-sm text-onSurface-default-tertiary">
                  No custom categories present. Add a category to get started.
                </p>
              ) : (
                <div className="divide-y divide-memBorder-primary">
                  {workspace.categories.map((category) => (
                    <div
                      key={category.name}
                      className="flex items-start justify-between gap-4 px-5 py-4"
                    >
                      <div className="min-w-0">
                        <p className="text-sm font-medium">{category.name}</p>
                        <p className="mt-1 break-words text-xs text-onSurface-default-tertiary">
                          {category.description || "No description"}
                        </p>
                      </div>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-8 shrink-0"
                        onClick={() =>
                          void saveCategories(
                            workspace.categories.filter(
                              (item) => item.name !== category.name,
                            ),
                          )
                        }
                        disabled={!canManageProject || busy === "categories"}
                        title={`Delete ${category.name}`}
                        aria-label={`Delete ${category.name}`}
                      >
                        <Trash2 className="size-4" />
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          </SettingsPanel>
        )}

        {section === "project-retention" && (
          <SettingsPanel>
            <div className="flex items-center justify-between gap-4 p-5">
              <div>
                <h2 className="text-sm font-semibold">Memory Decay</h2>
                <p className="mt-1 max-w-xl text-xs leading-5 text-onSurface-default-tertiary">
                  Rank recently-accessed memories higher in search and gently
                  down-rank idle ones. Nothing is deleted; older memories still
                  surface when relevant.
                </p>
              </div>
              <Switch
                checked={workspace.retention.memory_decay}
                onCheckedChange={(checked) =>
                  updateRetention({ memory_decay: checked })
                }
                disabled={!canManageProject}
              />
            </div>
            <div className="space-y-4 border-t border-memBorder-primary p-5">
              <Field label="Memory Expiration Date">
                <Popover>
                  <PopoverTrigger asChild>
                    <Button
                      type="button"
                      variant="outline"
                      className="w-full justify-start font-normal sm:w-64"
                      disabled={!canManageProject}
                    >
                      <CalendarDays className="mr-2 size-4" />
                      {workspace.retention.expiration_date
                        ? format(
                            new Date(
                              `${workspace.retention.expiration_date}T00:00:00`,
                            ),
                            "PPP",
                            { locale: language === "zh" ? zhCN : enUS },
                          )
                        : "Pick a date"}
                    </Button>
                  </PopoverTrigger>
                  <PopoverContent
                    align="start"
                    className="w-auto rounded-md p-0"
                  >
                    <Calendar
                      mode="single"
                      selected={
                        workspace.retention.expiration_date
                          ? new Date(
                              `${workspace.retention.expiration_date}T00:00:00`,
                            )
                          : undefined
                      }
                      onSelect={(date) =>
                        updateRetention({
                          expiration_date: date
                            ? format(date, "yyyy-MM-dd")
                            : null,
                        })
                      }
                      initialFocus
                    />
                  </PopoverContent>
                </Popover>
              </Field>
              <div className="flex flex-wrap justify-between gap-2">
                {workspace.retention.expiration_date ? (
                  <Button
                    type="button"
                    variant="ghost"
                    size="sm"
                    onClick={() => updateRetention({ expiration_date: null })}
                  >
                    <X className="mr-1.5 size-4" />
                    Clear date
                  </Button>
                ) : (
                  <span />
                )}
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => void saveRetention()}
                  disabled={!canManageProject || busy === "retention"}
                >
                  <Save className="mr-1.5 size-4" />
                  {busy === "retention" ? "Saving..." : "Save Changes"}
                </Button>
              </div>
            </div>
          </SettingsPanel>
        )}

        {section === "project-playground" && (
          <SettingsPanel>
            <div className="space-y-5 p-5">
              <Field label="Reply instructions">
                <Textarea
                  value={workspace.playground.custom_instructions}
                  onChange={(event) =>
                    updatePlayground({
                      custom_instructions: event.target.value,
                    })
                  }
                  placeholder="Instructions used only for Playground replies"
                  className="min-h-28"
                  disabled={!canManageProject}
                />
              </Field>

              <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                {PLAYGROUND_NUMBER_FIELDS.map(
                  ({ key, label, step, min, max }) => (
                    <Field key={key} label={label}>
                      <Input
                        type="number"
                        step={step}
                        min={min}
                        max={max}
                        value={workspace.playground[key]}
                        onChange={(event) =>
                          updatePlayground({
                            [key]: Number(event.target.value),
                          })
                        }
                        disabled={!canManageProject}
                      />
                    </Field>
                  ),
                )}
              </div>
            </div>

            <div className="divide-y divide-memBorder-primary border-t border-memBorder-primary">
              <div className="flex items-center justify-between gap-4 px-5 py-4">
                <Label
                  htmlFor="playground-force-add-only"
                  className="text-sm font-medium"
                >
                  Store raw messages without extraction
                </Label>
                <Switch
                  id="playground-force-add-only"
                  checked={workspace.playground.force_add_only}
                  onCheckedChange={(checked) =>
                    updatePlayground({ force_add_only: checked })
                  }
                  disabled={!canManageProject}
                />
              </div>
              <div className="flex items-center justify-between gap-4 px-5 py-4">
                <Label
                  htmlFor="playground-reranking"
                  className="text-sm font-medium"
                >
                  Rerank retrieved memories
                </Label>
                <Switch
                  id="playground-reranking"
                  checked={workspace.playground.reranking}
                  onCheckedChange={(checked) =>
                    updatePlayground({ reranking: checked })
                  }
                  disabled={!canManageProject}
                />
              </div>
            </div>

            <div className="flex justify-end border-t border-memBorder-primary p-4">
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => void savePlayground()}
                disabled={!canManageProject || busy === "playground"}
              >
                <Save className="mr-1.5 size-4" />
                {busy === "playground" ? "Saving..." : "Save Changes"}
              </Button>
            </div>
          </SettingsPanel>
        )}

        {section === "org-general" && (
          <>
            <SettingsPanel>
              <div className="space-y-5 p-5">
                <div className="grid gap-4 sm:grid-cols-[minmax(0,1fr)_auto]">
                  <Field label="Organization Name">
                    <Input
                      value={selectedOrganization.name}
                      onChange={(event) =>
                        updateOrganizationDraft(event.target.value)
                      }
                      onBlur={() => void saveOrganization()}
                      disabled={!canManageOrganization}
                    />
                  </Field>
                  {selectedOrganization.id === "org_default" && (
                    <span className="h-6 justify-self-start rounded border border-memBorder-primary px-2 py-1 text-xs text-onSurface-default-tertiary sm:mt-7">
                      Default
                    </span>
                  )}
                </div>
                <Field label="Organization ID">
                  <div className="flex gap-2">
                    <Input value={selectedOrganization.id} disabled />
                    <Button
                      type="button"
                      variant="outline"
                      size="icon"
                      onClick={() =>
                        void navigator.clipboard.writeText(
                          selectedOrganization.id,
                        )
                      }
                      title="Copy organization ID"
                      aria-label="Copy organization ID"
                    >
                      <Copy className="size-4" />
                    </Button>
                  </div>
                </Field>
                {busy === "organization" && (
                  <p className="text-xs text-onSurface-default-tertiary">
                    Saving organization...
                  </p>
                )}
              </div>
            </SettingsPanel>
            <section className="space-y-2">
              <h2 className="text-base font-semibold">Danger Zone</h2>
              <SettingsPanel>
                <div className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <h3 className="text-sm font-medium">Delete organization</h3>
                    <p className="mt-1 text-xs text-onSurface-default-tertiary">
                      Permanently delete this organization and all projects,
                      members, and data within it.
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="shrink-0"
                    onClick={() => void deleteOrganization()}
                    disabled={
                      !canManageOrganization ||
                      selectedOrganization.id === "org_default" ||
                      busy === "delete-organization"
                    }
                  >
                    Delete Organization
                  </Button>
                </div>
              </SettingsPanel>
            </section>
          </>
        )}

        {section === "org-members" && (
          <SettingsPanel>{renderMembers("organization")}</SettingsPanel>
        )}

        {section === "profile" && (
          <>
            <SettingsPanel>
              <div className="space-y-5 p-5">
                <div className="grid gap-4 sm:grid-cols-2">
                  <Field label="First Name">
                    <Input
                      value={firstName}
                      onChange={(event) => setFirstName(event.target.value)}
                    />
                  </Field>
                  <Field label="Last Name">
                    <Input
                      value={lastName}
                      onChange={(event) => setLastName(event.target.value)}
                    />
                  </Field>
                </div>
                <div className="flex justify-end">
                  <Button
                    type="button"
                    variant="primary"
                    size="sm"
                    onClick={() => void saveProfile()}
                    disabled={!firstName.trim() || busy === "profile"}
                  >
                    {busy === "profile" ? "Saving..." : "Save"}
                  </Button>
                </div>
                <Field label="Email">
                  <div className="relative">
                    <LockKeyhole className="absolute left-3 top-2.5 size-4 text-onSurface-default-tertiary" />
                    <Input
                      value={user?.email || ""}
                      disabled
                      className="pl-9"
                    />
                  </div>
                </Field>
              </div>
            </SettingsPanel>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">Appearance</h2>
              <SettingsPanel>
                <div className="grid gap-2 p-4 sm:grid-cols-3">
                  {[
                    { value: "light", label: "Light", icon: Sun },
                    { value: "dark", label: "Dark", icon: Moon },
                    { value: "system", label: "System", icon: Monitor },
                  ].map((option) => {
                    const Icon = option.icon;
                    const active = theme === option.value;
                    return (
                      <Button
                        key={option.value}
                        type="button"
                        variant={active ? "tertiary" : "outline"}
                        className="justify-start"
                        onClick={() => setTheme(option.value)}
                      >
                        <Icon className="mr-2 size-4" />
                        {option.label}
                        {active && <Check className="ml-auto size-4" />}
                      </Button>
                    );
                  })}
                </div>
              </SettingsPanel>
            </section>

            <section className="space-y-2">
              <h2 className="text-base font-semibold">Authorization</h2>
              <SettingsPanel>
                <div className="flex flex-wrap gap-2 p-4">
                  <Button
                    type="button"
                    variant="outline"
                    onClick={() => void logout()}
                  >
                    <LogOut className="mr-2 size-4" />
                    Logout
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    onClick={() => void deleteAccount()}
                    disabled={busy === "delete-account"}
                  >
                    <Trash2 className="mr-2 size-4" />
                    Delete Account
                  </Button>
                </div>
              </SettingsPanel>
            </section>
          </>
        )}
      </main>

      <Dialog open={inviteOpen} onOpenChange={setInviteOpen}>
        <DialogContent className="max-w-md rounded-md">
          <DialogHeader>
            <DialogTitle>Invite Member</DialogTitle>
            <DialogDescription>
              Invite a new member to your {inviteScope}.
            </DialogDescription>
          </DialogHeader>
          <div className="grid grid-cols-2 rounded-md border border-memBorder-primary bg-surface-default-secondary p-1">
            {(["organization", "project"] as const).map((scope) => (
              <button
                key={scope}
                type="button"
                onClick={() => setInviteScope(scope)}
                className={cn(
                  "flex h-9 items-center justify-center gap-2 rounded text-sm capitalize",
                  inviteScope === scope
                    ? "bg-surface-default-primary font-medium shadow-sm"
                    : "text-onSurface-default-tertiary",
                )}
              >
                {scope === "organization" ? (
                  <Building2 className="size-4" />
                ) : (
                  <Folder className="size-4" />
                )}
                {scope}
              </button>
            ))}
          </div>
          <div className="space-y-4">
            <Field label="Email">
              <Input
                type="email"
                value={inviteEmail}
                onChange={(event) => setInviteEmail(event.target.value)}
                placeholder="Enter member email ID"
              />
              <p className="text-xs text-onSurface-default-tertiary">
                Enter the member&apos;s email address
              </p>
            </Field>
            <Field label="Role">
              <select
                value={inviteRole}
                onChange={(event) =>
                  setInviteRole(event.target.value as MemberRole)
                }
                className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
              >
                {(Object.keys(ROLE_DETAILS) as MemberRole[]).map((role) => (
                  <option key={role} value={role}>
                    {ROLE_DETAILS[role].label}
                  </option>
                ))}
              </select>
              <p className="text-xs text-onSurface-default-tertiary">
                {ROLE_DETAILS[inviteRole].description}
              </p>
            </Field>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setInviteOpen(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="primary"
              onClick={() => void inviteMember()}
              disabled={!inviteEmail.trim() || busy === "invite"}
            >
              {busy === "invite"
                ? "Inviting..."
                : `Invite to ${inviteScope === "organization" ? "Organization" : "Project"}`}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={createKind !== null}
        onOpenChange={(open) => !open && setCreateKind(null)}
      >
        <DialogContent className="max-w-md rounded-md">
          <DialogHeader>
            <DialogTitle>
              Create New {createKind === "project" ? "Project" : "Organization"}
            </DialogTitle>
            <DialogDescription>
              Choose a name. It can be changed later in General settings.
            </DialogDescription>
          </DialogHeader>
          <Field
            label={
              createKind === "project" ? "Project Name" : "Organization Name"
            }
          >
            <Input
              value={createName}
              onChange={(event) => setCreateName(event.target.value)}
              autoFocus
              onKeyDown={(event) => {
                if (event.key === "Enter") void createScope();
              }}
            />
          </Field>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => setCreateKind(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="primary"
              onClick={() => void createScope()}
              disabled={!createName.trim() || busy === "create"}
            >
              <Plus className="mr-2 size-4" />
              {busy === "create" ? "Creating..." : "Create"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
