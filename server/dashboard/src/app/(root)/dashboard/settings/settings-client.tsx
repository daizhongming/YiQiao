"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import {
  Sun,
  Moon,
  Monitor,
  Save,
  Trash2,
  UserPlus,
  Wand2,
  SlidersHorizontal,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { toast } from "@/components/ui/use-toast";
import { useAuth } from "@/hooks/use-auth";
import { useApiQuery } from "@/hooks/use-api-query";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { getCategoryValidationError, isValidEmail } from "@/lib/validators";
import { api, getActiveProjectId, setActiveProjectId } from "@/utils/api";
import {
  AUTH_ENDPOINTS,
  MEMORY_ENDPOINTS,
  SETTINGS_ENDPOINTS,
} from "@/utils/api-endpoints";

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
    extraction?: WorkspaceSettings["extraction"];
    categories?: WorkspaceSettings["categories"];
    retention?: WorkspaceSettings["retention"];
    playground?: WorkspaceSettings["playground"];
  }[];
  members: {
    email: string;
    role: "OWNER" | "READER";
    status: "active" | "invited";
    project_id?: string;
    organization_id?: string;
  }[];
  extraction: {
    multilingual: boolean;
    use_case: string;
    memory_depth: string;
    include: string;
    exclude: string;
    custom_instructions: string;
  };
  categories: { name: string; description: string }[];
  retention: { memory_decay: boolean; expiration_date: string | null };
  playground: {
    custom_instructions: string;
    categories: { name: string; description: string }[];
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
  extraction: {
    multilingual: true,
    use_case: "",
    memory_depth: "Essential Insights",
    include: "",
    exclude: "",
    custom_instructions: "",
  },
  categories: [],
  retention: { memory_decay: true, expiration_date: null },
  playground: {
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
  },
};

const GENERATION_TIMEOUT_MS = 75_000;

const effectiveProjectSettings = (
  workspace: WorkspaceSettings,
  projectId: string,
) => {
  const project = workspace.projects.find((item) => item.id === projectId);
  return {
    extraction: {
      ...DEFAULT_WORKSPACE.extraction,
      ...(workspace.extraction || {}),
      ...(project?.extraction || {}),
    },
    categories: project?.categories ?? workspace.categories ?? [],
    retention: {
      ...DEFAULT_WORKSPACE.retention,
      ...(workspace.retention || {}),
      ...(project?.retention || {}),
    },
    playground: {
      ...DEFAULT_WORKSPACE.playground,
      ...(workspace.playground || {}),
      ...(project?.playground || {}),
    },
  };
};

const hydrateWorkspaceForProject = (
  workspace: WorkspaceSettings,
  projectId: string,
): WorkspaceSettings => {
  const effectiveSettings = effectiveProjectSettings(workspace, projectId);
  return {
    ...workspace,
    active_project_id: projectId,
    ...effectiveSettings,
  };
};

const USE_CASES = [
  "Healthcare",
  "AI Companion",
  "Customer Support",
  "E-commerce",
  "Education",
  "Research",
  "Personal",
  "CODING_AGENT",
  "VOICE_AGENT",
  "OPENCLAW",
  "ENTERPRISE_SAAS",
  "Assistant",
];

type PlaygroundNumberField = {
  key: "temperature" | "threshold" | "top_p" | "top_k" | "max_tokens";
  label: string;
  step: number;
  min: number;
  max?: number;
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

export type SettingsSection =
  | "projects"
  | "members"
  | "extraction"
  | "categories-retention"
  | "playground"
  | "profile"
  | "password";

const SECTION_TITLES: Record<SettingsSection, string> = {
  projects: "Projects",
  members: "Members",
  extraction: "Extraction",
  "categories-retention": "Categories and Retention",
  playground: "Playground Settings",
  profile: "Profile",
  password: "Password",
};

const SETTINGS_NAV_ITEMS: { section: SettingsSection; label: string }[] = [
  { section: "projects", label: "Projects" },
  { section: "members", label: "Members" },
  { section: "extraction", label: "Extraction" },
  { section: "categories-retention", label: "Categories and Retention" },
  { section: "playground", label: "Playground Settings" },
  { section: "profile", label: "Profile" },
  { section: "password", label: "Password" },
];

const SAVABLE_PROJECT_SECTIONS = new Set<SettingsSection>([
  "projects",
  "extraction",
  "categories-retention",
  "playground",
]);

export default function SettingsClient({
  section,
}: {
  section: SettingsSection;
}) {
  const { user, isAdmin, refreshUser } = useAuth();
  const { theme, setTheme } = useTheme();
  const { t } = useI18n();
  const settingsNavRef = useRef<HTMLElement>(null);
  const customInstructionsRef = useRef<HTMLTextAreaElement>(null);

  const [name, setName] = useState("");
  const [email, setEmail] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);

  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [savingPassword, setSavingPassword] = useState(false);
  const [workspace, setWorkspace] =
    useState<WorkspaceSettings>(DEFAULT_WORKSPACE);
  const [activeProject, setActiveProject] = useState(
    DEFAULT_WORKSPACE.active_project_id,
  );
  const [savingWorkspace, setSavingWorkspace] = useState(false);
  const [generatingInstructions, setGeneratingInstructions] = useState(false);
  const [generatingCategories, setGeneratingCategories] = useState(false);
  const [lastWorkspaceSavedAt, setLastWorkspaceSavedAt] = useState<Date | null>(
    null,
  );
  const [inviteEmail, setInviteEmail] = useState("");
  const [inviteRole, setInviteRole] =
    useState<WorkspaceSettings["members"][number]["role"]>("READER");
  const [memberScope, setMemberScope] = useState<"project" | "organization">(
    "project",
  );
  const [invitingMember, setInvitingMember] = useState(false);
  const [updatingMemberEmail, setUpdatingMemberEmail] = useState<string | null>(
    null,
  );
  const [removingMemberEmail, setRemovingMemberEmail] = useState<string | null>(
    null,
  );
  const [creatingOrganization, setCreatingOrganization] = useState(false);
  const [creatingProject, setCreatingProject] = useState(false);
  const [deletingOrganization, setDeletingOrganization] = useState(false);
  const [deletingProject, setDeletingProject] = useState(false);

  useEffect(() => {
    const nav = settingsNavRef.current;
    if (!nav || nav.scrollWidth <= nav.clientWidth) return;

    const activeTab = nav.querySelector<HTMLElement>('[aria-current="page"]');
    if (!activeTab) return;

    const navBounds = nav.getBoundingClientRect();
    const tabBounds = activeTab.getBoundingClientRect();
    const isFullyVisible =
      tabBounds.left >= navBounds.left && tabBounds.right <= navBounds.right;

    if (!isFullyVisible) {
      activeTab.scrollIntoView({ block: "nearest", inline: "center" });
    }
  }, [section, t]);

  useEffect(() => {
    if (user) {
      setName(user.name);
      setEmail(user.email);
    }
  }, [user]);

  const { data: workspaceData, refetch: refetchWorkspace } =
    useApiQuery<WorkspaceSettings>(
      async () => {
        const res = await api.get<WorkspaceSettings>(
          SETTINGS_ENDPOINTS.WORKSPACE,
        );
        return res.data;
      },
      {
        errorToast: "Failed to load workspace settings",
      },
    );

  useEffect(() => {
    if (!workspaceData) return;
    const storedProject = getActiveProjectId();
    const serverProject = workspaceData.active_project_id;
    const serverProjectExists = workspaceData.projects.some(
      (project) => project.id === serverProject,
    );
    const storedProjectExists = workspaceData.projects.some(
      (project) => project.id === storedProject,
    );
    const nextProject = storedProjectExists
      ? storedProject
      : serverProjectExists
        ? serverProject
        : workspaceData.projects[0]?.id || DEFAULT_WORKSPACE.active_project_id;
    setWorkspace(hydrateWorkspaceForProject(workspaceData, nextProject));
    setActiveProject(nextProject);
    setActiveProjectId(nextProject);
    window.dispatchEvent(new Event("yiqiao-projects-updated"));
  }, [workspaceData]);

  const profileDirty =
    user !== null && (name !== user.name || email !== user.email);
  const profileValid = name.trim().length > 0 && email.trim().length > 0;
  const organizations = workspace.organizations?.length
    ? workspace.organizations
    : [workspace.organization];
  const activeOrganization =
    workspace.active_organization_id || organizations[0]?.id || "org_default";
  const selectedOrganization =
    organizations.find((org) => org.id === activeOrganization) ??
    organizations[0] ??
    DEFAULT_WORKSPACE.organization;
  const orgProjects = workspace.projects.filter(
    (project) =>
      (project.organization_id || selectedOrganization.id) ===
      selectedOrganization.id,
  );
  const selectedProject =
    orgProjects.find((project) => project.id === activeProject) ??
    orgProjects[0] ??
    workspace.projects[0] ??
    DEFAULT_WORKSPACE.projects[0];
  const projectScopeAvailable = orgProjects.some(
    (project) => project.id === selectedProject.id,
  );
  const effectiveMemberScope =
    memberScope === "project" && projectScopeAvailable
      ? "project"
      : "organization";
  const activeUserEmail = user?.email.toLowerCase();
  const canManageOrganization =
    isAdmin ||
    workspace.members.some(
      (member) =>
        member.email.toLowerCase() === activeUserEmail &&
        member.status === "active" &&
        member.role === "OWNER" &&
        !member.project_id &&
        member.organization_id === selectedOrganization.id,
    );
  const canManageProject =
    canManageOrganization ||
    workspace.members.some(
      (member) =>
        member.email.toLowerCase() === activeUserEmail &&
        member.status === "active" &&
        member.role === "OWNER" &&
        member.project_id === selectedProject.id,
    );
  const canManageSelectedMemberScope =
    effectiveMemberScope === "organization"
      ? canManageOrganization
      : canManageProject;
  const isDefaultOrganization = selectedOrganization.id === "org_default";
  const isDefaultProject =
    selectedProject.is_default || selectedProject.id === "default-project";

  const handleSaveProfile = async () => {
    setSavingProfile(true);
    try {
      await api.patch(AUTH_ENDPOINTS.ME, {
        name: name.trim(),
        email: email.trim(),
      });
      await refreshUser();
      toast({ title: "Profile updated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to update profile",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSavingProfile(false);
    }
  };

  const handleChangePassword = async () => {
    if (newPassword !== confirmPassword) {
      toast({
        title: "Passwords don't match",
        variant: "destructive",
      });
      return;
    }

    setSavingPassword(true);
    try {
      await api.post(AUTH_ENDPOINTS.CHANGE_PASSWORD, {
        current_password: currentPassword,
        new_password: newPassword,
      });
      setCurrentPassword("");
      setNewPassword("");
      setConfirmPassword("");
      toast({ title: "Password updated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to update password",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSavingPassword(false);
    }
  };

  const updateProject = (
    patch: Partial<WorkspaceSettings["projects"][number]>,
  ) => {
    setWorkspace((current) => ({
      ...current,
      active_project_id: selectedProject.id,
      projects: current.projects.map((project) =>
        project.id === selectedProject.id ? { ...project, ...patch } : project,
      ),
    }));
  };

  const updateOrganization = (nameValue: string) => {
    setWorkspace((current) => ({
      ...current,
      organization:
        current.active_organization_id === selectedOrganization.id
          ? { ...selectedOrganization, name: nameValue }
          : current.organization,
      organizations: organizations.map((org) =>
        org.id === selectedOrganization.id ? { ...org, name: nameValue } : org,
      ),
    }));
  };

  const selectOrganization = (orgId: string) => {
    const nextProject =
      workspace.projects.find((project) => project.organization_id === orgId) ??
      null;
    setWorkspace((current) => ({
      ...(nextProject
        ? hydrateWorkspaceForProject(current, nextProject.id)
        : current),
      active_organization_id: orgId,
      active_project_id: nextProject?.id || "",
    }));
    if (nextProject) {
      setActiveProject(nextProject.id);
      setActiveProjectId(nextProject.id);
      window.dispatchEvent(new Event("yiqiao-projects-updated"));
    }
  };

  const addOrganization = async () => {
    setCreatingOrganization(true);
    try {
      const orgRes = await api.post<{ id: string; name: string }>(
        SETTINGS_ENDPOINTS.ORGANIZATIONS,
        {
          name: "Organization",
        },
      );
      const projectRes = await api.post<WorkspaceSettings["projects"][number]>(
        SETTINGS_ENDPOINTS.ORG_PROJECTS(orgRes.data.id),
        {
          name: "Project",
        },
      );
      setActiveProject(projectRes.data.id);
      setActiveProjectId(projectRes.data.id);
      setWorkspace((current) => ({
        ...current,
        active_organization_id: orgRes.data.id,
        active_project_id: projectRes.data.id,
      }));
      await refreshWorkspaceState();
      toast({ title: "Organization created", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to create organization",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setCreatingOrganization(false);
    }
  };

  const selectProject = (projectId: string) => {
    setActiveProject(projectId);
    setActiveProjectId(projectId);
    window.dispatchEvent(new Event("yiqiao-projects-updated"));
    setWorkspace((current) => hydrateWorkspaceForProject(current, projectId));
  };

  const addProject = async () => {
    setCreatingProject(true);
    try {
      const res = await api.post<WorkspaceSettings["projects"][number]>(
        SETTINGS_ENDPOINTS.ORG_PROJECTS(selectedOrganization.id),
        {
          name: "Project",
        },
      );
      setActiveProject(res.data.id);
      setActiveProjectId(res.data.id);
      await refreshWorkspaceState();
      toast({ title: "Project created", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to create project",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setCreatingProject(false);
    }
  };

  const deleteOrganization = async () => {
    if (isDefaultOrganization) return;
    if (
      !window.confirm(
        `Delete organization "${selectedOrganization.name || selectedOrganization.id}" and all of its projects?`,
      )
    ) {
      return;
    }
    setDeletingOrganization(true);
    try {
      await api.delete(
        SETTINGS_ENDPOINTS.ORGANIZATION(selectedOrganization.id),
      );
      const fallbackProject =
        workspace.projects.find(
          (project) => project.organization_id !== selectedOrganization.id,
        ) || DEFAULT_WORKSPACE.projects[0];
      setActiveProject(fallbackProject.id);
      setActiveProjectId(fallbackProject.id);
      setWorkspace((current) => ({
        ...current,
        active_organization_id:
          fallbackProject.organization_id || DEFAULT_WORKSPACE.organization.id,
        active_project_id: fallbackProject.id,
      }));
      await refreshWorkspaceState();
      toast({ title: "Organization deleted", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to delete organization",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setDeletingOrganization(false);
    }
  };

  const deleteProject = async () => {
    if (isDefaultProject || !projectScopeAvailable) return;
    if (
      !window.confirm(
        `Delete project "${selectedProject.name || selectedProject.id}"?`,
      )
    ) {
      return;
    }
    setDeletingProject(true);
    try {
      await api.delete(
        SETTINGS_ENDPOINTS.ORG_PROJECT(
          selectedOrganization.id,
          selectedProject.id,
        ),
      );
      const fallbackProject =
        workspace.projects.find(
          (project) =>
            project.id !== selectedProject.id &&
            project.organization_id === selectedOrganization.id,
        ) ||
        workspace.projects.find(
          (project) => project.id !== selectedProject.id,
        ) ||
        DEFAULT_WORKSPACE.projects[0];
      setActiveProject(fallbackProject.id);
      setActiveProjectId(fallbackProject.id);
      setWorkspace((current) => ({
        ...current,
        active_organization_id:
          fallbackProject.organization_id || current.active_organization_id,
        active_project_id: fallbackProject.id,
      }));
      await refreshWorkspaceState();
      toast({ title: "Project deleted", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to delete project",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setDeletingProject(false);
    }
  };

  const scopedMembers = workspace.members.filter((member) =>
    effectiveMemberScope === "organization"
      ? !member.project_id && member.organization_id === selectedOrganization.id
      : member.project_id === selectedProject.id,
  );

  const visibleMembers = [
    ...(user &&
    !scopedMembers.some(
      (member) => member.email.toLowerCase() === user.email.toLowerCase(),
    )
      ? [
          {
            email: user.email,
            role: "OWNER" as const,
            status: "active" as const,
            project_id:
              effectiveMemberScope === "project"
                ? selectedProject.id
                : undefined,
            organization_id: selectedOrganization.id,
          },
        ]
      : []),
    ...scopedMembers,
  ];

  const refreshWorkspaceState = async () => {
    await refetchWorkspace();
    window.dispatchEvent(new Event("yiqiao-projects-updated"));
  };

  const addMember = async () => {
    const emailValue = inviteEmail.trim().toLowerCase();
    if (!emailValue) return;
    if (!isValidEmail(emailValue)) {
      toast({
        title: "Invite Member",
        description: "Enter a valid email address.",
        variant: "destructive",
      });
      return;
    }
    setInvitingMember(true);
    try {
      const res = await api.post<WorkspaceSettings>(
        effectiveMemberScope === "organization"
          ? SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id)
          : SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
              selectedOrganization.id,
              selectedProject.id,
            ),
        {
          email: emailValue,
          role: inviteRole,
        },
      );
      setWorkspace(res.data);
      await refreshWorkspaceState();
      setInviteEmail("");
      toast({ title: "Member invited", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to invite member",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setInvitingMember(false);
    }
  };

  const removeMember = async (emailValue: string) => {
    setRemovingMemberEmail(emailValue);
    try {
      const res =
        effectiveMemberScope === "organization"
          ? await api.delete<WorkspaceSettings>(
              SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id),
              { data: { email: emailValue } },
            )
          : await api.delete<WorkspaceSettings>(
              SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
                selectedOrganization.id,
                selectedProject.id,
              ),
              { params: { email: emailValue } },
            );
      setWorkspace(res.data);
      await refreshWorkspaceState();
      toast({ title: "Member removed", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to remove member",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setRemovingMemberEmail(null);
    }
  };

  const updateMemberRole = async (
    emailValue: string,
    role: WorkspaceSettings["members"][number]["role"],
  ) => {
    setUpdatingMemberEmail(emailValue);
    try {
      const res =
        effectiveMemberScope === "organization"
          ? await api.put<WorkspaceSettings>(
              SETTINGS_ENDPOINTS.ORG_MEMBERS(selectedOrganization.id),
              {
                email: emailValue,
                role,
              },
            )
          : await api.put<WorkspaceSettings>(
              SETTINGS_ENDPOINTS.ORG_PROJECT_MEMBERS(
                selectedOrganization.id,
                selectedProject.id,
              ),
              {
                email: emailValue,
                role,
              },
            );
      setWorkspace(res.data);
      await refreshWorkspaceState();
      toast({ title: "Member role updated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to update member role",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setUpdatingMemberEmail(null);
    }
  };

  const updateSection = <K extends keyof WorkspaceSettings>(
    section: K,
    patch: Partial<WorkspaceSettings[K]>,
  ) => {
    setWorkspace((current) => ({
      ...current,
      [section]: { ...(current[section] as object), ...patch },
    }));
  };

  const categoryText = workspace.categories
    .map((category) =>
      category.description
        ? `${category.name}: ${category.description}`
        : category.name,
    )
    .join("\n");
  const retentionDateLabel =
    workspace.retention.expiration_date || "No expiration date";
  const lastSavedLabel = lastWorkspaceSavedAt
    ? lastWorkspaceSavedAt.toLocaleTimeString([], {
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
      })
    : "Not saved in this session";

  const setCategoriesFromText = (value: string) => {
    setWorkspace((current) => ({
      ...current,
      categories: value
        .split("\n")
        .map((line) => line.trim())
        .filter(Boolean)
        .map((line) => {
          const normalizedLine = line.replace("：", ":");
          const [name, ...description] = normalizedLine.split(":");
          return {
            name: name.trim(),
            description: description.join(":").trim(),
          };
        }),
    }));
  };

  const requestGeneratedCategories = async () => {
    const res = await api.post<{ categories: WorkspaceSettings["categories"] }>(
      MEMORY_ENDPOINTS.GENERATE_CATEGORIES,
      {
        use_case: workspace.extraction.use_case || "General assistant memory",
        memory_depth: workspace.extraction.memory_depth,
        include: workspace.extraction.include,
        exclude: workspace.extraction.exclude,
        multilingual: workspace.extraction.multilingual,
        custom_instructions: workspace.extraction.custom_instructions,
      },
      { timeout: GENERATION_TIMEOUT_MS },
    );
    return Array.isArray(res.data.categories) ? res.data.categories : [];
  };

  const generateCategories = async () => {
    setGeneratingCategories(true);
    try {
      const categories = await requestGeneratedCategories();
      if (!categories.length) {
        throw new Error("No categories were generated.");
      }
      setWorkspace((current) => ({ ...current, categories }));
      toast({ title: "Categories generated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to generate categories",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setGeneratingCategories(false);
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

  const saveProjectDetails = async () => {
    if (!selectedProject.name.trim()) {
      toast({
        title: "Project name is required",
        variant: "destructive",
      });
      return false;
    }

    const originalOrganization = workspaceData?.organizations?.find(
      (org) => org.id === selectedOrganization.id,
    );
    if (
      canManageOrganization &&
      originalOrganization &&
      selectedOrganization.name !== originalOrganization.name
    ) {
      await api.patch(
        SETTINGS_ENDPOINTS.ORGANIZATION(selectedOrganization.id),
        {
          name: selectedOrganization.name,
        },
      );
    }

    await api.patch(
      SETTINGS_ENDPOINTS.ORG_PROJECT(
        selectedOrganization.id,
        selectedProject.id,
      ),
      {
        name: selectedProject.name,
        description: selectedProject.description,
      },
    );
    return true;
  };

  const saveProjectSection = async () => {
    const endpoint = SETTINGS_ENDPOINTS.ORG_PROJECT(
      selectedOrganization.id,
      selectedProject.id,
    );

    if (section === "extraction") {
      await api.patch(endpoint, { extraction: workspace.extraction });
      return;
    }
    if (section === "categories-retention") {
      await api.patch(endpoint, {
        categories: workspace.categories,
        retention: workspace.retention,
      });
      return;
    }
    if (section === "playground") {
      await api.patch(endpoint, { playground: workspace.playground });
    }
  };

  const saveActiveSection = async () => {
    const invalidPlaygroundValue =
      section === "playground" ? validatePlayground() : undefined;
    if (invalidPlaygroundValue) {
      toast({
        title: "Invalid playground settings",
        description: invalidPlaygroundValue,
        variant: "destructive",
      });
      return;
    }
    const invalidCategory =
      section === "categories-retention"
        ? getCategoryValidationError(workspace.categories)
        : null;
    if (invalidCategory) {
      toast({
        title: "Categories",
        description: invalidCategory,
        variant: "destructive",
      });
      return;
    }
    if (!projectScopeAvailable) {
      toast({
        title: "Create or select a project before saving",
        variant: "destructive",
      });
      return;
    }
    setSavingWorkspace(true);
    try {
      if (section === "projects") {
        const saved = await saveProjectDetails();
        if (!saved) return;
      } else {
        await saveProjectSection();
      }
      await refreshWorkspaceState();
      setLastWorkspaceSavedAt(new Date());
      toast({ title: "Settings saved", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to save settings",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSavingWorkspace(false);
    }
  };

  const generateExtractionInstructions = async () => {
    setGeneratingInstructions(true);
    try {
      const res = await api.post(
        MEMORY_ENDPOINTS.GENERATE_INSTRUCTIONS,
        {
          use_case: workspace.extraction.use_case || "General assistant memory",
          memory_depth: workspace.extraction.memory_depth,
          include: workspace.extraction.include,
          exclude: workspace.extraction.exclude,
          multilingual: workspace.extraction.multilingual,
        },
        { timeout: GENERATION_TIMEOUT_MS },
      );
      updateSection("extraction", {
        custom_instructions: res.data.custom_instructions || "",
      });
      toast({ title: "Instructions generated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to generate instructions",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setGeneratingInstructions(false);
    }
  };

  const focusCustomInstructions = () => {
    customInstructionsRef.current?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
    customInstructionsRef.current?.focus({ preventScroll: true });
  };

  const activeScopeLabel =
    section === "projects"
      ? t("Organization and project scope")
      : section === "members"
        ? effectiveMemberScope === "organization"
          ? `${t("Organization membership")}: ${selectedOrganization.name}`
          : `${t("Project membership")}: ${selectedProject.name}`
        : section === "extraction"
          ? `${t("Production memory extraction")}: ${selectedProject.name}`
          : section === "categories-retention"
            ? `${t("Production categories and new-memory expiration")}: ${selectedProject.name}`
            : section === "playground"
              ? `${t("Playground requests only")}: ${selectedProject.name}`
              : t("Personal account");

  return (
    <div className="min-w-0 space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <h1 className="text-xl font-semibold font-fustat">
            {t(SECTION_TITLES[section])}
          </h1>
          <p className="truncate text-xs text-onSurface-default-tertiary">
            {activeScopeLabel}
          </p>
        </div>
        {SAVABLE_PROJECT_SECTIONS.has(section) && (
          <Button
            onClick={saveActiveSection}
            disabled={savingWorkspace || !canManageProject}
          >
            <Save className="mr-2 size-4" />
            {savingWorkspace ? "Saving..." : "Save changes"}
          </Button>
        )}
      </div>

      <nav
        ref={settingsNavRef}
        className="flex max-w-full gap-5 overflow-x-auto border-b border-memBorder-primary"
        aria-label="Settings sections"
      >
        {SETTINGS_NAV_ITEMS.map((item) => (
          <Link
            key={item.section}
            href={`/dashboard/settings/${item.section}`}
            aria-current={section === item.section ? "page" : undefined}
            className={`shrink-0 border-b-2 px-0.5 pb-2 text-sm transition-colors ${
              section === item.section
                ? "border-onSurface-default-primary font-medium text-onSurface-default-primary"
                : "border-transparent text-onSurface-default-tertiary hover:text-onSurface-default-secondary"
            }`}
          >
            {t(item.label)}
          </Link>
        ))}
      </nav>

      <Card
        hidden={section !== "projects"}
        className="border-memBorder-primary"
      >
        <CardHeader>
          <CardTitle className="text-sm">Project</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="org-name" className="text-xs">
                Organization Name
              </Label>
              <Input
                id="org-name"
                value={selectedOrganization.name}
                onChange={(event) => updateOrganization(event.target.value)}
                disabled={!canManageOrganization}
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="active-org" className="text-xs">
                Active Organization
              </Label>
              <div className="flex gap-2">
                <select
                  id="active-org"
                  value={selectedOrganization.id}
                  onChange={(event) => selectOrganization(event.target.value)}
                  className="h-9 w-0 min-w-0 flex-1 rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                >
                  {organizations.map((org) => (
                    <option key={org.id} value={org.id}>
                      {org.name || org.id}
                    </option>
                  ))}
                </select>
                <Button
                  type="button"
                  variant="outline"
                  className="w-16 shrink-0"
                  onClick={addOrganization}
                  disabled={creatingOrganization || !isAdmin}
                >
                  {creatingOrganization ? "..." : "New"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label={t("Delete organization")}
                  title={t("Delete organization")}
                  onClick={deleteOrganization}
                  disabled={
                    isDefaultOrganization ||
                    deletingOrganization ||
                    !canManageOrganization
                  }
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="active-project" className="text-xs">
                Active Project
              </Label>
              <div className="flex gap-2">
                <select
                  id="active-project"
                  value={projectScopeAvailable ? selectedProject.id : ""}
                  onChange={(event) => selectProject(event.target.value)}
                  className="h-9 w-0 min-w-0 flex-1 rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
                >
                  {!orgProjects.length && <option value="">No projects</option>}
                  {orgProjects.map((project) => (
                    <option key={project.id} value={project.id}>
                      {project.name || project.id}
                    </option>
                  ))}
                </select>
                <Button
                  type="button"
                  variant="outline"
                  className="w-16 shrink-0"
                  onClick={addProject}
                  disabled={creatingProject || !canManageOrganization}
                >
                  {creatingProject ? "..." : "New"}
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  aria-label={t("Delete project")}
                  title={t("Delete project")}
                  onClick={deleteProject}
                  disabled={
                    !projectScopeAvailable ||
                    isDefaultProject ||
                    deletingProject ||
                    !canManageProject
                  }
                >
                  <Trash2 className="size-4" />
                </Button>
              </div>
            </div>
            <div className="space-y-1">
              <Label htmlFor="project-name" className="text-xs">
                Project Name
              </Label>
              <Input
                id="project-name"
                value={projectScopeAvailable ? selectedProject.name : ""}
                onChange={(event) =>
                  updateProject({ name: event.target.value })
                }
                disabled={!projectScopeAvailable || !canManageProject}
                placeholder="Create a project first"
              />
            </div>
          </div>
          <div className="space-y-1">
            <Label htmlFor="project-description" className="text-xs">
              Project Description
            </Label>
            <Textarea
              id="project-description"
              value={projectScopeAvailable ? selectedProject.description : ""}
              onChange={(event) =>
                updateProject({ description: event.target.value })
              }
              disabled={!projectScopeAvailable || !canManageProject}
            />
          </div>
        </CardContent>
      </Card>

      <Card hidden={section !== "members"} className="border-memBorder-primary">
        <CardHeader>
          <CardTitle className="text-sm">Members</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-[1fr_140px_140px_180px_auto]">
            <Input
              type="email"
              value={inviteEmail}
              onChange={(event) => setInviteEmail(event.target.value)}
              placeholder="teammate@example.com"
              disabled={!canManageSelectedMemberScope}
            />
            <select
              value={effectiveMemberScope}
              onChange={(event) =>
                setMemberScope(event.target.value as typeof memberScope)
              }
              disabled={!canManageProject && !canManageOrganization}
              className="h-9 rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
            >
              <option value="project" disabled={!projectScopeAvailable}>
                Project
              </option>
              <option value="organization">Organization</option>
            </select>
            <select
              value={inviteRole}
              onChange={(event) =>
                setInviteRole(
                  event.target
                    .value as WorkspaceSettings["members"][number]["role"],
                )
              }
              disabled={!canManageSelectedMemberScope}
              className="h-9 rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
            >
              {["READER", "OWNER"].map((role) => (
                <option key={role} value={role}>
                  {role}
                </option>
              ))}
            </select>
            <select
              value={
                effectiveMemberScope === "organization"
                  ? selectedOrganization.id
                  : selectedProject.id
              }
              disabled
              className="h-9 rounded-md border border-memBorder-primary bg-surface-default-secondary px-3 text-sm"
            >
              <option>
                {effectiveMemberScope === "organization"
                  ? selectedOrganization.name || selectedOrganization.id
                  : selectedProject.name || selectedProject.id}
              </option>
            </select>
            <Button
              type="button"
              variant="outline"
              onClick={addMember}
              disabled={
                invitingMember ||
                !canManageSelectedMemberScope ||
                !inviteEmail.trim() ||
                (effectiveMemberScope === "project" && !projectScopeAvailable)
              }
            >
              <UserPlus className="mr-2 size-4" />
              {invitingMember ? "Adding..." : "Add"}
            </Button>
          </div>

          <div className="divide-y divide-memBorder-primary rounded-md border border-memBorder-primary">
            {visibleMembers.map((member) => (
              <div
                key={`${member.email}-${member.organization_id}-${member.project_id || "org"}`}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-2 px-3 py-2 text-sm md:grid-cols-[minmax(0,1fr)_72px_96px_120px_72px_32px_32px] md:gap-3"
              >
                <span className="min-w-0 truncate">{member.email}</span>
                <span className="text-onSurface-default-tertiary">
                  {member.project_id ? "Project" : "Org"}
                </span>
                <select
                  value={member.role}
                  onChange={(event) =>
                    updateMemberRole(
                      member.email,
                      event.target
                        .value as WorkspaceSettings["members"][number]["role"],
                    )
                  }
                  disabled={
                    member.email === user?.email ||
                    !canManageSelectedMemberScope ||
                    updatingMemberEmail === member.email
                  }
                  className="h-8 rounded-md border border-memBorder-primary bg-surface-default-primary px-2 text-xs"
                >
                  {["READER", "OWNER"].map((role) => (
                    <option key={role} value={role}>
                      {role}
                    </option>
                  ))}
                </select>
                <span className="min-w-0 truncate text-onSurface-default-tertiary">
                  {member.project_id
                    ? selectedProject.name || member.project_id
                    : selectedOrganization.name || member.organization_id}
                </span>
                <span className="text-onSurface-default-tertiary">
                  {member.status}
                </span>
                {canManageProject ? (
                  <Button
                    asChild
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-7"
                    title={`Manage usage limits for ${member.email}`}
                    aria-label={`Manage usage limits for ${member.email}`}
                  >
                    <Link
                      href={`/dashboard/settings/usage-limits?scope_type=member&scope_id=${encodeURIComponent(member.email)}&project_id=${encodeURIComponent(selectedProject.id)}`}
                    >
                      <SlidersHorizontal className="size-3.5" />
                    </Link>
                  </Button>
                ) : (
                  <span />
                )}
                <Button
                  type="button"
                  variant="ghost"
                  size="icon"
                  className="size-7"
                  disabled={
                    member.email === user?.email ||
                    !canManageSelectedMemberScope ||
                    removingMemberEmail === member.email
                  }
                  onClick={() => removeMember(member.email)}
                  title={`Remove ${member.email}`}
                  aria-label={`Remove ${member.email}`}
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </div>
            ))}
          </div>
        </CardContent>
      </Card>

      <Card
        hidden={section !== "extraction"}
        className="border-memBorder-primary"
      >
        <CardHeader>
          <CardTitle className="text-sm">Extraction</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <label className="flex items-center gap-2 text-sm">
            <Switch
              checked={workspace.extraction.multilingual}
              onCheckedChange={(checked) =>
                updateSection("extraction", { multilingual: checked })
              }
              disabled={!canManageProject}
            />
            Multilingual Memory Extraction
          </label>
          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label className="text-xs">Usecase</Label>
              <select
                value={workspace.extraction.use_case}
                onChange={(event) =>
                  updateSection("extraction", { use_case: event.target.value })
                }
                disabled={!canManageProject}
                className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
              >
                <option value="">Select a usecase</option>
                {USE_CASES.map((useCase) => (
                  <option key={useCase} value={useCase}>
                    {useCase}
                  </option>
                ))}
              </select>
            </div>
            <div className="space-y-1">
              <Label className="text-xs">Memory Depth</Label>
              <select
                value={workspace.extraction.memory_depth}
                onChange={(event) =>
                  updateSection("extraction", {
                    memory_depth: event.target.value,
                  })
                }
                disabled={!canManageProject}
                className="h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 text-sm"
              >
                {[
                  "Essential Insights",
                  "Balanced Context",
                  "Comprehensive Knowledge",
                ].map((depth) => (
                  <option key={depth} value={depth}>
                    {depth}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Include</Label>
            <Textarea
              value={workspace.extraction.include}
              onChange={(event) =>
                updateSection("extraction", {
                  include: event.target.value,
                })
              }
              placeholder="Data points, formats, or information to include"
              disabled={!canManageProject}
            />
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Exclude</Label>
            <Textarea
              value={workspace.extraction.exclude}
              onChange={(event) =>
                updateSection("extraction", {
                  exclude: event.target.value,
                })
              }
              placeholder="Data points, formats, or information to exclude"
              disabled={!canManageProject}
            />
          </div>
          <div className="flex flex-wrap justify-between gap-3">
            <Button
              type="button"
              variant="outline"
              onClick={focusCustomInstructions}
              disabled={!canManageProject}
            >
              Skip to Manual Customization
            </Button>
            <Button
              type="button"
              onClick={generateExtractionInstructions}
              disabled={generatingInstructions || !canManageProject}
            >
              <Wand2 className="mr-2 size-4" />
              {generatingInstructions
                ? "Generating..."
                : "Generate Instructions"}
            </Button>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Custom Instructions</Label>
            <Textarea
              ref={customInstructionsRef}
              value={workspace.extraction.custom_instructions}
              onChange={(event) =>
                updateSection("extraction", {
                  custom_instructions: event.target.value,
                })
              }
              placeholder="Manual custom instructions"
              disabled={!canManageProject}
            />
          </div>
        </CardContent>
      </Card>

      <Card
        hidden={section !== "categories-retention"}
        className="border-memBorder-primary"
      >
        <CardHeader className="flex flex-row items-center justify-between gap-3">
          <CardTitle className="text-sm">Categories and Retention</CardTitle>
          <Button
            type="button"
            variant="outline"
            onClick={generateCategories}
            disabled={generatingCategories || !canManageProject}
          >
            <Wand2 className="mr-2 size-4" />
            {generatingCategories ? "Generating..." : "Generate categories"}
          </Button>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid gap-2 text-sm md:grid-cols-4">
            <div className="rounded-md border border-memBorder-primary px-3 py-2">
              <p className="text-xs text-onSurface-default-tertiary">
                Categories
              </p>
              <p className="font-medium">{workspace.categories.length}</p>
            </div>
            <div className="rounded-md border border-memBorder-primary px-3 py-2">
              <p className="text-xs text-onSurface-default-tertiary">
                Default expiration
              </p>
              <p className="font-medium">
                {workspace.retention.memory_decay ? "Enabled" : "Disabled"}
              </p>
            </div>
            <div className="rounded-md border border-memBorder-primary px-3 py-2">
              <p className="text-xs text-onSurface-default-tertiary">
                Expiration
              </p>
              <p className="font-medium">{retentionDateLabel}</p>
            </div>
            <div className="rounded-md border border-memBorder-primary px-3 py-2">
              <p className="text-xs text-onSurface-default-tertiary">
                Last saved
              </p>
              <p className="font-medium">{lastSavedLabel}</p>
            </div>
          </div>
          <div className="space-y-1">
            <Label className="text-xs">Categories</Label>
            <Textarea
              value={categoryText}
              onChange={(event) => setCategoriesFromText(event.target.value)}
              placeholder="Sports: Anything related to sports"
              disabled={!canManageProject}
            />
            {workspace.categories.length > 0 ? (
              <div className="flex flex-wrap gap-2 pt-1">
                {workspace.categories.slice(0, 12).map((category) => (
                  <span
                    key={category.name}
                    className="max-w-full truncate rounded-md border border-memBorder-primary px-2 py-1 text-xs text-onSurface-default-secondary"
                    title={category.description || category.name}
                  >
                    {category.name}
                  </span>
                ))}
                {workspace.categories.length > 12 ? (
                  <span className="rounded-md border border-memBorder-primary px-2 py-1 text-xs text-onSurface-default-tertiary">
                    +{workspace.categories.length - 12}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="flex items-center gap-2 text-sm">
              <Switch
                checked={workspace.retention.memory_decay}
                onCheckedChange={(checked) =>
                  updateSection("retention", { memory_decay: checked })
                }
                disabled={!canManageProject}
              />
              Apply default expiration
            </label>
            <div className="space-y-1">
              <Label className="text-xs">Default expiration date</Label>
              <Input
                type="date"
                value={workspace.retention.expiration_date ?? ""}
                onChange={(event) =>
                  updateSection("retention", {
                    expiration_date: event.target.value || null,
                  })
                }
                disabled={
                  !canManageProject || !workspace.retention.memory_decay
                }
              />
            </div>
          </div>
        </CardContent>
      </Card>

      <Card
        hidden={section !== "playground"}
        className="border-memBorder-primary"
      >
        <CardHeader>
          <CardTitle className="text-sm">{t("Playground Settings")}</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1">
            <Label className="text-xs">Reply instructions</Label>
            <Textarea
              value={workspace.playground.custom_instructions}
              onChange={(event) =>
                updateSection("playground", {
                  custom_instructions: event.target.value,
                })
              }
              placeholder="Instructions used only for Playground replies"
              disabled={!canManageProject}
            />
          </div>
          <div className="grid gap-3 md:grid-cols-3">
            {PLAYGROUND_NUMBER_FIELDS.map(({ key, label, step, min, max }) => (
              <div key={key} className="space-y-1">
                <Label className="text-xs">{t(label)}</Label>
                <Input
                  type="number"
                  step={step}
                  min={min}
                  max={max}
                  value={workspace.playground[key] as number}
                  onChange={(event) =>
                    updateSection("playground", {
                      [key]: Number(event.target.value),
                    })
                  }
                  disabled={!canManageProject}
                />
              </div>
            ))}
          </div>
          <div className="flex flex-wrap gap-4">
            <label className="flex items-center gap-2 text-sm">
              <Switch
                checked={workspace.playground.force_add_only}
                onCheckedChange={(checked) =>
                  updateSection("playground", { force_add_only: checked })
                }
                disabled={!canManageProject}
              />
              Store raw messages without extraction
            </label>
            <label className="flex items-center gap-2 text-sm">
              <Switch
                checked={workspace.playground.reranking}
                onCheckedChange={(checked) =>
                  updateSection("playground", { reranking: checked })
                }
                disabled={!canManageProject}
              />
              Rerank retrieved memories
            </label>
          </div>
        </CardContent>
      </Card>

      <Card hidden={section !== "profile"} className="border-memBorder-primary">
        <CardHeader>
          <CardTitle className="text-sm">Profile</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSaveProfile();
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="settings-name" className="text-xs">
                  Name
                </Label>
                <Input
                  id="settings-name"
                  name="name"
                  autoComplete="name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="settings-email" className="text-xs">
                  Email
                </Label>
                <Input
                  id="settings-email"
                  name="email"
                  type="email"
                  autoComplete="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                />
              </div>
            </div>
            <Button
              type="submit"
              disabled={!profileDirty || !profileValid || savingProfile}
            >
              {savingProfile ? "Saving..." : "Save profile"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card
        hidden={section !== "password"}
        className="border-memBorder-primary"
      >
        <CardHeader>
          <CardTitle className="text-sm">Password</CardTitle>
        </CardHeader>
        <CardContent>
          <form
            className="space-y-4"
            onSubmit={(event) => {
              event.preventDefault();
              void handleChangePassword();
            }}
          >
            <input
              type="email"
              name="username"
              autoComplete="username"
              value={email}
              readOnly
              hidden
            />
            <div className="space-y-1">
              <Label htmlFor="settings-current-password" className="text-xs">
                Current password
              </Label>
              <Input
                id="settings-current-password"
                name="current-password"
                type="password"
                autoComplete="current-password"
                value={currentPassword}
                onChange={(e) => setCurrentPassword(e.target.value)}
              />
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1">
                <Label htmlFor="settings-new-password" className="text-xs">
                  New password
                </Label>
                <Input
                  id="settings-new-password"
                  name="new-password"
                  type="password"
                  autoComplete="new-password"
                  value={newPassword}
                  onChange={(e) => setNewPassword(e.target.value)}
                  placeholder="Min 8 characters"
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="settings-confirm-password" className="text-xs">
                  Confirm new password
                </Label>
                <Input
                  id="settings-confirm-password"
                  name="confirm-password"
                  type="password"
                  autoComplete="new-password"
                  value={confirmPassword}
                  onChange={(e) => setConfirmPassword(e.target.value)}
                />
              </div>
            </div>
            <Button
              type="submit"
              disabled={
                !currentPassword ||
                newPassword.length < 8 ||
                !confirmPassword ||
                savingPassword
              }
            >
              {savingPassword ? "Saving..." : "Update password"}
            </Button>
          </form>
        </CardContent>
      </Card>

      <Card hidden={section !== "profile"} className="border-memBorder-primary">
        <CardHeader>
          <CardTitle className="text-sm">Appearance</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex items-center gap-4">
            <span className="text-sm text-onSurface-default-secondary">
              Theme
            </span>
            <Button
              type="button"
              variant={theme === "light" ? "secondary" : "ghost"}
              size="icon"
              onClick={() => setTheme("light")}
              title="Light theme"
              aria-label="Light theme"
            >
              <Sun className="size-4" />
            </Button>
            <Button
              type="button"
              variant={theme === "dark" ? "secondary" : "ghost"}
              size="icon"
              onClick={() => setTheme("dark")}
              title="Dark theme"
              aria-label="Dark theme"
            >
              <Moon className="size-4" />
            </Button>
            <Button
              type="button"
              variant={theme === "system" ? "secondary" : "ghost"}
              size="icon"
              onClick={() => setTheme("system")}
              title="System theme"
              aria-label="System theme"
            >
              <Monitor className="size-4" />
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
