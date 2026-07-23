"use client";

import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";
import {
  AppWindow,
  CheckCircle2,
  Clock3,
  History,
  Link2,
  Loader2,
  Plus,
  RefreshCw,
  ShieldCheck,
  Unplug,
  UserRound,
  UsersRound,
  XCircle,
} from "lucide-react";

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { toast } from "@/components/ui/use-toast";
import { useAuth } from "@/hooks/use-auth";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import type {
  OAuthApplicationCreate,
  OAuthApplicationListResponse,
  OAuthApplicationSummary,
  OAuthDeviceRequest,
  OAuthGrantListResponse,
  OAuthGrantSummary,
} from "@/types/api";
import { api, getActiveProjectId } from "@/utils/api";
import { OAUTH_ENDPOINTS } from "@/utils/api-endpoints";

type Confirmation = {
  title: string;
  description: string;
  actionLabel: string;
  run: () => Promise<void>;
};

const EMPTY_GRANTS: OAuthGrantListResponse = {
  items: [],
  audit_events: [],
  can_manage_project: false,
};

const REGISTERABLE_SCOPES = new Set(["memory:read", "memory:write"]);

function normalizeUserCode(value: string) {
  const compact = value
    .toUpperCase()
    .replace(/[^A-Z0-9]/g, "")
    .slice(0, 8);
  return compact.length > 4
    ? `${compact.slice(0, 4)}-${compact.slice(4)}`
    : compact;
}

function formatDate(value: string | null | undefined, locale: string) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString(locale);
}

function splitList(value: string) {
  return Array.from(
    new Set(
      value
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean),
    ),
  );
}

function statusClasses(status: string) {
  switch (status.toLowerCase()) {
    case "active":
    case "approved":
      return "border-onSurface-positive-secondary bg-surface-positive-primary text-onSurface-positive-primary";
    case "pending":
      return "border-memBorder-secondary bg-surface-default-tertiary text-onSurface-default-secondary";
    case "denied":
    case "revoked":
    case "expired":
      return "border-onSurface-danger-secondary bg-surface-danger-primary text-onSurface-danger-primary";
    default:
      return "border-memBorder-secondary bg-surface-default-tertiary text-onSurface-default-secondary";
  }
}

function StatusBadge({ status }: { status: string }) {
  const { t } = useI18n();
  return (
    <Badge variant="outline" className={statusClasses(status)}>
      {t(status)}
    </Badge>
  );
}

export default function ConnectedAppsPage() {
  const { t, language } = useI18n();
  const { isAdmin } = useAuth();
  const locale = language === "zh" ? "zh-CN" : "en-US";
  const [userCode, setUserCode] = useState("");
  const [deviceRequest, setDeviceRequest] = useState<OAuthDeviceRequest | null>(
    null,
  );
  const [selectedScopes, setSelectedScopes] = useState<Set<string>>(new Set());
  const [lookupLoading, setLookupLoading] = useState(false);
  const [busyAction, setBusyAction] = useState<string | null>(null);
  const [grants, setGrants] = useState<OAuthGrantListResponse>(EMPTY_GRANTS);
  const [grantsLoading, setGrantsLoading] = useState(true);
  const [grantsError, setGrantsError] = useState("");
  const [applications, setApplications] = useState<OAuthApplicationSummary[]>(
    [],
  );
  const [canRegisterApplication, setCanRegisterApplication] = useState(false);
  const [applicationsLoading, setApplicationsLoading] = useState(true);
  const [applicationsError, setApplicationsError] = useState("");
  const [confirmation, setConfirmation] = useState<Confirmation | null>(null);
  const [registration, setRegistration] = useState({
    clientId: "",
    displayName: "",
    scopes: "memory:read, memory:write",
  });

  const normalizedCode = useMemo(() => normalizeUserCode(userCode), [userCode]);
  const codeReady = normalizedCode.replace("-", "").length === 8;

  const loadGrants = useCallback(async () => {
    setGrantsLoading(true);
    setGrantsError("");
    try {
      const response = await api.get<OAuthGrantListResponse>(
        OAUTH_ENDPOINTS.GRANTS,
      );
      setGrants(response.data);
    } catch (error) {
      setGrantsError(getErrorMessage(error, t("Failed to load connections")));
    } finally {
      setGrantsLoading(false);
    }
  }, [t]);

  const loadApplications = useCallback(async () => {
    setApplicationsLoading(true);
    setApplicationsError("");
    try {
      const response = await api.get<OAuthApplicationListResponse>(
        OAUTH_ENDPOINTS.APPLICATIONS,
      );
      setApplications(response.data.items ?? []);
      setCanRegisterApplication(response.data.can_register);
    } catch (error) {
      setCanRegisterApplication(false);
      setApplicationsError(
        getErrorMessage(error, t("Application management is unavailable")),
      );
    } finally {
      setApplicationsLoading(false);
    }
  }, [t]);

  const lookupRequest = useCallback(
    async (rawCode: string) => {
      const code = normalizeUserCode(rawCode);
      if (code.replace("-", "").length !== 8) return;
      setLookupLoading(true);
      try {
        const response = await api.post<OAuthDeviceRequest>(
          OAUTH_ENDPOINTS.DEVICE_LOOKUP,
          { user_code: code },
        );
        setDeviceRequest(response.data);
        setSelectedScopes(
          new Set(
            response.data.approved_scopes.length > 0
              ? response.data.approved_scopes
              : response.data.requested_scopes,
          ),
        );
      } catch (error) {
        setDeviceRequest(null);
        toast({
          title: t("Authorization request"),
          description: getErrorMessage(error),
          variant: "destructive",
        });
      } finally {
        setLookupLoading(false);
      }
    },
    [t],
  );

  useEffect(() => {
    void loadGrants();
    if (isAdmin) void loadApplications();
  }, [isAdmin, loadApplications, loadGrants]);

  useEffect(() => {
    const currentUrl = new URL(window.location.href);
    const queryCode = currentUrl.searchParams.get("user_code");
    if (!queryCode) return;

    currentUrl.searchParams.delete("user_code");
    window.history.replaceState(
      window.history.state,
      "",
      `${currentUrl.pathname}${currentUrl.search}${currentUrl.hash}`,
    );
    const normalized = normalizeUserCode(queryCode);
    setUserCode(normalized);
    void lookupRequest(normalized);
  }, [lookupRequest]);

  const ownGrants = grants.items.filter((grant) => grant.is_owner);
  const projectGrants = grants.items.filter((grant) => !grant.is_owner);

  const approveRequest = async () => {
    if (!deviceRequest || selectedScopes.size === 0) return;
    setBusyAction("approve-request");
    try {
      const scopes = deviceRequest.requested_scopes.filter((scope) =>
        selectedScopes.has(scope),
      );
      const response = await api.post<OAuthDeviceRequest>(
        OAUTH_ENDPOINTS.DEVICE_APPROVE(deviceRequest.id),
        { project_id: getActiveProjectId(), approved_scopes: scopes },
      );
      setDeviceRequest(response.data);
      toast({ title: t("Connection approved"), variant: "success" });
      await loadGrants();
    } catch (error) {
      toast({
        title: t("Approve connection"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const rejectRequest = async () => {
    if (!deviceRequest) return;
    setBusyAction("reject-request");
    try {
      const response = await api.post<OAuthDeviceRequest>(
        OAUTH_ENDPOINTS.DEVICE_REJECT(deviceRequest.id),
      );
      setDeviceRequest(response.data);
      toast({ title: t("Connection rejected"), variant: "success" });
    } catch (error) {
      toast({
        title: t("Reject connection"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const revokeGrant = async (grant: OAuthGrantSummary) => {
    setBusyAction(`grant-${grant.id}`);
    try {
      await api.post(OAUTH_ENDPOINTS.GRANT_REVOKE(grant.id));
      toast({ title: t("Connection revoked"), variant: "success" });
      await loadGrants();
    } catch (error) {
      toast({
        title: t("Revoke connection"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const revokeApplicationGrants = async (
    clientId: string,
    projectId?: string,
  ) => {
    setBusyAction(`application-grants-${clientId}-${projectId ?? "self"}`);
    try {
      await api.post(OAUTH_ENDPOINTS.GRANTS_REVOKE_BY_APPLICATION, {
        client_id: clientId,
        ...(projectId ? { project_id: projectId } : {}),
      });
      toast({ title: t("Application access revoked"), variant: "success" });
      await loadGrants();
    } catch (error) {
      toast({
        title: t("Revoke application access"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const registerApplication = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const allowedScopes = splitList(registration.scopes);
    if (
      !registration.clientId.trim() ||
      !registration.displayName.trim() ||
      allowedScopes.length === 0 ||
      allowedScopes.some((scope) => !REGISTERABLE_SCOPES.has(scope))
    ) {
      toast({
        title: t("Complete all application fields"),
        variant: "destructive",
      });
      return;
    }

    const payload: OAuthApplicationCreate = {
      client_id: registration.clientId.trim(),
      display_name: registration.displayName.trim(),
      client_type: "public",
      allowed_audiences: ["yiqiao:memory-api"],
      allowed_scopes: allowedScopes,
    };
    setBusyAction("register-application");
    try {
      await api.post(OAUTH_ENDPOINTS.APPLICATIONS, payload);
      setRegistration({
        clientId: "",
        displayName: "",
        scopes: "memory:read, memory:write",
      });
      toast({ title: t("Application registered"), variant: "success" });
      await loadApplications();
    } catch (error) {
      toast({
        title: t("Register application"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const revokeRegisteredApplication = async (clientId: string) => {
    setBusyAction(`registered-application-${clientId}`);
    try {
      await api.post(OAUTH_ENDPOINTS.APPLICATION_REVOKE(clientId));
      toast({ title: t("Application revoked"), variant: "success" });
      await Promise.all([loadApplications(), loadGrants()]);
    } catch (error) {
      toast({
        title: t("Revoke application"),
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setBusyAction(null);
    }
  };

  const grantCard = (grant: OAuthGrantSummary, projectWide = false) => (
    <Card key={grant.id} className="min-w-0">
      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
        <div className="min-w-0">
          <CardTitle className="truncate text-base">
            {grant.application_name}
          </CardTitle>
          <p className="mt-1 break-all font-mono text-xs text-onSurface-default-tertiary">
            {grant.client_id}
          </p>
        </div>
        <StatusBadge status={grant.status} />
      </CardHeader>
      <CardContent className="space-y-4 text-sm">
        <dl className="grid gap-3 sm:grid-cols-2">
          <div>
            <dt className="text-onSurface-default-tertiary">{t("Audience")}</dt>
            <dd className="mt-1 break-all font-mono text-xs">
              {grant.audience}
            </dd>
          </div>
          <div>
            <dt className="text-onSurface-default-tertiary">{t("Project")}</dt>
            <dd className="mt-1 break-all font-mono text-xs">
              {grant.project_id}
            </dd>
          </div>
          <div>
            <dt className="text-onSurface-default-tertiary">
              {t("Last used")}
            </dt>
            <dd className="mt-1">{formatDate(grant.last_used_at, locale)}</dd>
          </div>
          <div>
            <dt className="text-onSurface-default-tertiary">{t("Owner")}</dt>
            <dd className="mt-1 break-all">
              {grant.is_owner ? t("You") : (grant.owner_email ?? "-")}
            </dd>
          </div>
        </dl>
        <div>
          <p className="text-onSurface-default-tertiary">
            {t("Approved scopes")}
          </p>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {grant.scopes.map((scope) => (
              <Badge
                key={scope}
                variant="secondary"
                className="font-mono font-normal"
              >
                {scope}
              </Badge>
            ))}
          </div>
        </div>
        {grant.status !== "revoked" && (
          <div className="flex flex-wrap gap-2 border-t border-memBorder-primary pt-4">
            <Button
              type="button"
              variant="destructive"
              size="sm"
              disabled={busyAction !== null}
              onClick={() =>
                setConfirmation({
                  title: t("Revoke connection?"),
                  description: t(
                    "This app will no longer be able to access the approved project data.",
                  ),
                  actionLabel: t("Revoke connection"),
                  run: () => revokeGrant(grant),
                })
              }
            >
              <Unplug className="size-4" />
              {t("Revoke")}
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={busyAction !== null}
              onClick={() =>
                setConfirmation({
                  title: t("Revoke all application access?"),
                  description: projectWide
                    ? t(
                        "All project grants for this application will be revoked.",
                      )
                    : t(
                        "All of your grants for this application will be revoked.",
                      ),
                  actionLabel: t("Revoke all"),
                  run: () =>
                    revokeApplicationGrants(
                      grant.client_id,
                      projectWide ? grant.project_id : undefined,
                    ),
                })
              }
            >
              {t("Revoke all")}
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );

  return (
    <div className="mx-auto w-full max-w-6xl space-y-6">
      <div className="flex items-start gap-3">
        <div className="grid size-10 shrink-0 place-items-center rounded-md border border-memBorder-primary bg-surface-default-tertiary">
          <Link2 className="size-5" />
        </div>
        <div className="min-w-0">
          <h1 className="text-xl font-semibold sm:text-2xl">
            {t("Connected Apps")}
          </h1>
          <p className="mt-1 max-w-2xl text-sm text-onSurface-default-secondary">
            {t(
              "Approve new connections and manage access to this YiQiao workspace.",
            )}
          </p>
        </div>
      </div>

      <Tabs defaultValue="authorize" className="w-full">
        <div className="overflow-x-auto pb-1">
          <TabsList
            className={
              isAdmin
                ? "grid w-full grid-cols-3 sm:w-auto"
                : "grid w-full grid-cols-2 sm:w-auto"
            }
          >
            <TabsTrigger value="authorize">{t("Authorize")}</TabsTrigger>
            <TabsTrigger value="connections">{t("Connections")}</TabsTrigger>
            {isAdmin && (
              <TabsTrigger value="applications">
                {t("Applications")}
              </TabsTrigger>
            )}
          </TabsList>
        </div>

        <TabsContent value="authorize" className="mt-5 space-y-5">
          <section
            aria-labelledby="authorization-heading"
            className="space-y-4"
          >
            <div>
              <h2
                id="authorization-heading"
                className="text-base font-semibold"
              >
                {t("Authorize an application")}
              </h2>
              <p className="mt-1 text-sm text-onSurface-default-secondary">
                {t(
                  "Enter the user code displayed by the application requesting access.",
                )}
              </p>
            </div>
            <div className="flex max-w-xl flex-col gap-2 sm:flex-row sm:items-end">
              <div className="min-w-0 flex-1 space-y-2">
                <Label htmlFor="oauth-user-code">{t("User code")}</Label>
                <Input
                  id="oauth-user-code"
                  value={normalizedCode}
                  onChange={(event) => {
                    setUserCode(event.target.value);
                    setDeviceRequest(null);
                  }}
                  placeholder="ABCD-1234"
                  autoComplete="one-time-code"
                  className="font-mono uppercase"
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={!codeReady || lookupLoading}
                onClick={() => void lookupRequest(normalizedCode)}
              >
                {lookupLoading ? (
                  <Loader2 className="size-4 animate-spin" />
                ) : (
                  <RefreshCw className="size-4" />
                )}
                {t("Look up request")}
              </Button>
            </div>
          </section>

          {deviceRequest && (
            <Card className="max-w-3xl">
              <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
                <div className="flex min-w-0 items-center gap-2">
                  <ShieldCheck className="size-5 shrink-0" />
                  <div className="min-w-0">
                    <CardTitle className="truncate text-base">
                      {deviceRequest.application_name}
                    </CardTitle>
                    <p className="mt-1 break-all font-mono text-xs text-onSurface-default-tertiary">
                      {deviceRequest.client_id}
                    </p>
                  </div>
                </div>
                <StatusBadge status={deviceRequest.status} />
              </CardHeader>
              <CardContent className="space-y-5">
                <dl className="grid gap-4 text-sm sm:grid-cols-2">
                  <div>
                    <dt className="text-onSurface-default-tertiary">
                      {t("Audience")}
                    </dt>
                    <dd className="mt-1 break-all font-mono text-xs">
                      {deviceRequest.audience}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-onSurface-default-tertiary">
                      {t("Expires")}
                    </dt>
                    <dd className="mt-1 flex items-center gap-1.5">
                      <Clock3 className="size-4" />
                      {formatDate(deviceRequest.expires_at, locale)}
                    </dd>
                  </div>
                </dl>

                <fieldset disabled={deviceRequest.status !== "pending"}>
                  <legend className="text-sm font-medium">
                    {t("Requested scopes")}
                  </legend>
                  <p className="mt-1 text-xs text-onSurface-default-tertiary">
                    {t("Clear any permission you do not want to grant.")}
                  </p>
                  <div className="mt-3 grid gap-2 sm:grid-cols-2">
                    {deviceRequest.requested_scopes.map((scope) => {
                      const id = `scope-${scope.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
                      return (
                        <label
                          key={scope}
                          htmlFor={id}
                          className="flex min-w-0 cursor-pointer items-center gap-2 rounded-md border border-memBorder-primary bg-surface-default-secondary px-3 py-2 text-sm"
                        >
                          <Checkbox
                            id={id}
                            checked={selectedScopes.has(scope)}
                            onCheckedChange={(checked) => {
                              setSelectedScopes((current) => {
                                const next = new Set(current);
                                if (checked) next.add(scope);
                                else next.delete(scope);
                                return next;
                              });
                            }}
                          />
                          <span className="min-w-0 break-all font-mono text-xs">
                            {scope}
                          </span>
                        </label>
                      );
                    })}
                  </div>
                </fieldset>

                {deviceRequest.status === "pending" && (
                  <div className="flex flex-col-reverse gap-2 border-t border-memBorder-primary pt-4 sm:flex-row">
                    <Button
                      type="button"
                      variant="outline"
                      disabled={busyAction !== null}
                      onClick={() =>
                        setConfirmation({
                          title: t("Reject connection?"),
                          description: t(
                            "The application will not receive access.",
                          ),
                          actionLabel: t("Reject connection"),
                          run: rejectRequest,
                        })
                      }
                    >
                      <XCircle className="size-4" />
                      {t("Reject")}
                    </Button>
                    <Button
                      type="button"
                      disabled={
                        busyAction !== null || selectedScopes.size === 0
                      }
                      onClick={() => void approveRequest()}
                    >
                      <CheckCircle2 className="size-4" />
                      {t("Approve selected scopes")}
                    </Button>
                  </div>
                )}
              </CardContent>
            </Card>
          )}
        </TabsContent>

        <TabsContent value="connections" className="mt-5 space-y-8">
          <div className="flex items-start justify-between gap-3">
            <div>
              <h2 className="text-base font-semibold">
                {t("Active connections")}
              </h2>
              <p className="mt-1 text-sm text-onSurface-default-secondary">
                {t("Review and revoke application access for this project.")}
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon"
              disabled={grantsLoading}
              onClick={() => void loadGrants()}
              aria-label={t("Refresh connections")}
              title={t("Refresh connections")}
            >
              <RefreshCw
                className={`size-4 ${grantsLoading ? "animate-spin" : ""}`}
              />
            </Button>
          </div>

          {grantsError && (
            <p
              role="alert"
              className="rounded-md border border-onSurface-danger-secondary bg-surface-danger-primary p-3 text-sm text-onSurface-danger-primary"
            >
              {grantsError}
            </p>
          )}

          {grantsLoading && grants.items.length === 0 ? (
            <div className="flex min-h-40 items-center justify-center text-onSurface-default-secondary">
              <Loader2 className="mr-2 size-4 animate-spin" />
              {t("Loading connections...")}
            </div>
          ) : (
            <>
              <section
                aria-labelledby="your-connections-heading"
                className="space-y-3"
              >
                <div className="flex items-center gap-2">
                  <UserRound className="size-4" />
                  <h3 id="your-connections-heading" className="font-semibold">
                    {t("Your connections")}
                  </h3>
                </div>
                {ownGrants.length > 0 ? (
                  <div className="grid gap-3 lg:grid-cols-2">
                    {ownGrants.map((grant) => grantCard(grant))}
                  </div>
                ) : (
                  <p className="rounded-md border border-dashed border-memBorder-secondary p-6 text-sm text-onSurface-default-secondary">
                    {t("You have no connected applications.")}
                  </p>
                )}
              </section>

              {grants.can_manage_project && (
                <section
                  aria-labelledby="project-connections-heading"
                  className="space-y-3"
                >
                  <div className="flex items-center gap-2">
                    <UsersRound className="size-4" />
                    <h3
                      id="project-connections-heading"
                      className="font-semibold"
                    >
                      {t("Project connections")}
                    </h3>
                  </div>
                  {projectGrants.length > 0 ? (
                    <div className="grid gap-3 lg:grid-cols-2">
                      {projectGrants.map((grant) => grantCard(grant, true))}
                    </div>
                  ) : (
                    <p className="rounded-md border border-dashed border-memBorder-secondary p-6 text-sm text-onSurface-default-secondary">
                      {t("No other project connections.")}
                    </p>
                  )}
                </section>
              )}
            </>
          )}

          <section
            aria-labelledby="connection-history-heading"
            className="space-y-3"
          >
            <div className="flex items-center gap-2">
              <History className="size-4" />
              <h3 id="connection-history-heading" className="font-semibold">
                {t("Recent access activity")}
              </h3>
            </div>
            {grants.audit_events.length > 0 ? (
              <div className="overflow-x-auto rounded-md border border-memBorder-primary">
                <table className="w-full min-w-[42rem] text-left text-sm">
                  <thead className="bg-surface-default-secondary text-xs text-onSurface-default-secondary">
                    <tr>
                      <th className="px-3 py-2 font-medium">{t("Event")}</th>
                      <th className="px-3 py-2 font-medium">
                        {t("Application")}
                      </th>
                      <th className="px-3 py-2 font-medium">{t("Outcome")}</th>
                      <th className="px-3 py-2 font-medium">{t("Project")}</th>
                      <th className="px-3 py-2 font-medium">{t("Time")}</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-memBorder-primary">
                    {grants.audit_events.map((event) => (
                      <tr key={event.id}>
                        <td className="px-3 py-2">{t(event.event_type)}</td>
                        <td className="px-3 py-2">
                          <span className="block">
                            {event.application_name ?? "-"}
                          </span>
                          <span className="block break-all font-mono text-xs text-onSurface-default-tertiary">
                            {event.client_id}
                          </span>
                        </td>
                        <td className="px-3 py-2">
                          <StatusBadge status={event.outcome} />
                        </td>
                        <td className="break-all px-3 py-2 font-mono text-xs">
                          {event.project_id ?? "-"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-2">
                          {formatDate(event.created_at, locale)}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="text-sm text-onSurface-default-secondary">
                {t("No recent access activity.")}
              </p>
            )}
          </section>
        </TabsContent>

        {isAdmin && (
          <TabsContent value="applications" className="mt-5 space-y-8">
            {canRegisterApplication && (
              <section
                aria-labelledby="register-application-heading"
                className="space-y-4"
              >
                <div>
                  <h2
                    id="register-application-heading"
                    className="text-base font-semibold"
                  >
                    {t("Register a public application")}
                  </h2>
                  <p className="mt-1 text-sm text-onSurface-default-secondary">
                    {t(
                      "Define the audiences and scopes this application may request.",
                    )}
                  </p>
                </div>
                <form
                  onSubmit={registerApplication}
                  className="grid max-w-3xl gap-4 sm:grid-cols-2"
                >
                  <div className="space-y-2">
                    <Label htmlFor="oauth-client-id">{t("Client ID")}</Label>
                    <Input
                      id="oauth-client-id"
                      value={registration.clientId}
                      onChange={(event) =>
                        setRegistration((current) => ({
                          ...current,
                          clientId: event.target.value,
                        }))
                      }
                      placeholder="my-public-app"
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="oauth-display-name">
                      {t("Display name")}
                    </Label>
                    <Input
                      id="oauth-display-name"
                      value={registration.displayName}
                      onChange={(event) =>
                        setRegistration((current) => ({
                          ...current,
                          displayName: event.target.value,
                        }))
                      }
                      placeholder={t("Application name")}
                      autoComplete="off"
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="oauth-audiences">
                      {t("Allowed audiences")}
                    </Label>
                    <Input
                      id="oauth-audiences"
                      value="yiqiao:memory-api"
                      readOnly
                    />
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="oauth-scopes">{t("Allowed scopes")}</Label>
                    <Input
                      id="oauth-scopes"
                      value={registration.scopes}
                      onChange={(event) =>
                        setRegistration((current) => ({
                          ...current,
                          scopes: event.target.value,
                        }))
                      }
                      placeholder="memory:read, memory:write"
                      autoComplete="off"
                    />
                    <p className="text-xs text-onSurface-default-tertiary">
                      {t("Separate multiple values with commas.")}
                    </p>
                  </div>
                  <div className="sm:col-span-2">
                    <Button type="submit" disabled={busyAction !== null}>
                      {busyAction === "register-application" ? (
                        <Loader2 className="size-4 animate-spin" />
                      ) : (
                        <Plus className="size-4" />
                      )}
                      {t("Register application")}
                    </Button>
                  </div>
                </form>
              </section>
            )}

            <section
              aria-labelledby="registered-applications-heading"
              className="space-y-3"
            >
              <div className="flex items-center justify-between gap-3">
                <div className="flex items-center gap-2">
                  <AppWindow className="size-4" />
                  <h3
                    id="registered-applications-heading"
                    className="font-semibold"
                  >
                    {t("Registered applications")}
                  </h3>
                </div>
                <Button
                  type="button"
                  variant="outline"
                  size="icon"
                  disabled={applicationsLoading}
                  onClick={() => void loadApplications()}
                  aria-label={t("Refresh applications")}
                  title={t("Refresh applications")}
                >
                  <RefreshCw
                    className={`size-4 ${applicationsLoading ? "animate-spin" : ""}`}
                  />
                </Button>
              </div>
              {applicationsError ? (
                <p
                  role="alert"
                  className="rounded-md border border-memBorder-primary bg-surface-default-secondary p-3 text-sm text-onSurface-default-secondary"
                >
                  {applicationsError}
                </p>
              ) : applicationsLoading && applications.length === 0 ? (
                <div className="flex min-h-32 items-center justify-center text-sm text-onSurface-default-secondary">
                  <Loader2 className="mr-2 size-4 animate-spin" />
                  {t("Loading applications...")}
                </div>
              ) : applications.length > 0 ? (
                <div className="grid gap-3 lg:grid-cols-2">
                  {applications.map((application) => (
                    <Card key={application.client_id}>
                      <CardHeader className="flex-row items-start justify-between gap-3 space-y-0">
                        <div className="min-w-0">
                          <CardTitle className="truncate text-base">
                            {application.display_name}
                          </CardTitle>
                          <p className="mt-1 break-all font-mono text-xs text-onSurface-default-tertiary">
                            {application.client_id}
                          </p>
                        </div>
                        <StatusBadge status={application.status} />
                      </CardHeader>
                      <CardContent className="space-y-4 text-sm">
                        <div>
                          <p className="text-onSurface-default-tertiary">
                            {t("Allowed audiences")}
                          </p>
                          <p className="mt-1 break-all font-mono text-xs">
                            {application.allowed_audiences.join(", ")}
                          </p>
                        </div>
                        <div>
                          <p className="text-onSurface-default-tertiary">
                            {t("Allowed scopes")}
                          </p>
                          <div className="mt-2 flex flex-wrap gap-1.5">
                            {application.allowed_scopes.map((scope) => (
                              <Badge
                                key={scope}
                                variant="secondary"
                                className="font-mono font-normal"
                              >
                                {scope}
                              </Badge>
                            ))}
                          </div>
                        </div>
                        {application.status !== "revoked" && (
                          <Button
                            type="button"
                            variant="destructive"
                            size="sm"
                            disabled={busyAction !== null}
                            onClick={() =>
                              setConfirmation({
                                title: t("Revoke application?"),
                                description: t(
                                  "New authorization requests from this application will be blocked.",
                                ),
                                actionLabel: t("Revoke application"),
                                run: () =>
                                  revokeRegisteredApplication(
                                    application.client_id,
                                  ),
                              })
                            }
                          >
                            <Unplug className="size-4" />
                            {t("Revoke application")}
                          </Button>
                        )}
                      </CardContent>
                    </Card>
                  ))}
                </div>
              ) : (
                <p className="rounded-md border border-dashed border-memBorder-secondary p-6 text-sm text-onSurface-default-secondary">
                  {t("No applications registered.")}
                </p>
              )}
            </section>
          </TabsContent>
        )}
      </Tabs>

      <AlertDialog
        open={confirmation !== null}
        onOpenChange={(open) => !open && setConfirmation(null)}
      >
        <AlertDialogContent className="w-[calc(100vw-2rem)] rounded-md">
          <AlertDialogHeader>
            <AlertDialogTitle>{confirmation?.title}</AlertDialogTitle>
            <AlertDialogDescription>
              {confirmation?.description}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t("Cancel")}</AlertDialogCancel>
            <AlertDialogAction
              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
              onClick={() => {
                const action = confirmation?.run;
                setConfirmation(null);
                if (action) void action();
              }}
            >
              {confirmation?.actionLabel}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );
}
