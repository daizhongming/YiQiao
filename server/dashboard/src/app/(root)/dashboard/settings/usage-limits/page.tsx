"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { ArrowLeft, Save, ShieldAlert } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import { toast } from "@/components/ui/use-toast";
import { useApiQuery } from "@/hooks/use-api-query";
import { getErrorMessage } from "@/lib/error-message";
import { api } from "@/utils/api";
import { USAGE_ENDPOINTS } from "@/utils/api-endpoints";
import {
  QuotaMode,
  QuotaPolicy,
  UsageMetric,
  UsageScopeType,
  UsageSubjects,
} from "@/types/api";

type Period = QuotaPolicy["period"];

type PolicyDraft = {
  metric: UsageMetric;
  label: string;
  enabled: boolean;
  period: Period;
  limit: string;
  mode: QuotaMode;
  warning: string;
};

const BASE_DRAFTS: PolicyDraft[] = [
  {
    metric: "api_requests",
    label: "API requests",
    enabled: false,
    period: "month",
    limit: "",
    mode: "monitor",
    warning: "80",
  },
  {
    metric: "memory_writes",
    label: "Memory writes",
    enabled: false,
    period: "month",
    limit: "",
    mode: "monitor",
    warning: "80",
  },
  {
    metric: "memory_searches",
    label: "Memory searches",
    enabled: false,
    period: "month",
    limit: "",
    mode: "monitor",
    warning: "80",
  },
  {
    metric: "stored_memories",
    label: "Stored memories",
    enabled: false,
    period: "total",
    limit: "",
    mode: "monitor",
    warning: "80",
  },
];

const SCOPE_LABELS: Record<UsageScopeType, string> = {
  organization: "Organization",
  project: "Project",
  api_key: "API key",
  member: "Member",
};

export default function UsageLimitsPage() {
  const [scopeType, setScopeType] = useState<UsageScopeType>("project");
  const [scopeId, setScopeId] = useState("");
  const [drafts, setDrafts] = useState<PolicyDraft[]>(BASE_DRAFTS);
  const [loadingPolicies, setLoadingPolicies] = useState(false);
  const [saving, setSaving] = useState(false);
  const [queryApplied, setQueryApplied] = useState(false);

  const { data: subjects, isLoading } = useApiQuery<UsageSubjects>(
    async () => (await api.get<UsageSubjects>(USAGE_ENDPOINTS.SUBJECTS)).data,
    { errorToast: "Failed to load usage scopes" },
  );

  const availableScopes = useMemo(() => {
    const scopes: UsageScopeType[] = [];
    if (subjects?.can_manage_organization) scopes.push("organization");
    if (subjects?.can_manage_project)
      scopes.push("project", "api_key", "member");
    return scopes;
  }, [subjects]);

  const options = useMemo(() => {
    if (!subjects) return [];
    if (scopeType === "organization")
      return [
        { id: subjects.organization.id, label: subjects.organization.name },
      ];
    if (scopeType === "project")
      return [{ id: subjects.project.id, label: subjects.project.name }];
    if (scopeType === "api_key") {
      return subjects.api_keys.map((key) => ({
        id: key.id,
        label: `${key.label} (${key.key_prefix}...)`,
      }));
    }
    return subjects.members.map((member) => ({
      id: member.email,
      label: member.email,
    }));
  }, [scopeType, subjects]);

  useEffect(() => {
    if (!subjects || queryApplied || !availableScopes.length) return;
    const params = new URLSearchParams(window.location.search);
    const requestedType = params.get("scope_type") as UsageScopeType | null;
    const nextType =
      requestedType && availableScopes.includes(requestedType)
        ? requestedType
        : availableScopes[0];
    let requestedId = params.get("scope_id") || "";
    const source =
      nextType === "organization"
        ? [{ id: subjects.organization.id }]
        : nextType === "project"
          ? [{ id: subjects.project.id }]
          : nextType === "api_key"
            ? subjects.api_keys
            : subjects.members.map((member) => ({ id: member.email }));
    if (!source.some((item) => item.id === requestedId))
      requestedId = source[0]?.id || "";
    setScopeType(nextType);
    setScopeId(requestedId);
    setQueryApplied(true);
  }, [availableScopes, queryApplied, subjects]);

  useEffect(() => {
    if (!queryApplied) return;
    const nextId = options.some((option) => option.id === scopeId)
      ? scopeId
      : options[0]?.id || "";
    if (nextId !== scopeId) setScopeId(nextId);
  }, [options, queryApplied, scopeId]);

  useEffect(() => {
    if (!subjects || !scopeId || !availableScopes.includes(scopeType)) {
      setDrafts(BASE_DRAFTS);
      return;
    }
    let cancelled = false;
    const load = async () => {
      setLoadingPolicies(true);
      try {
        const response = await api.get<{ policies: QuotaPolicy[] }>(
          USAGE_ENDPOINTS.POLICIES,
          {
            params: {
              scope_type: scopeType,
              scope_id: scopeId,
              project_id: subjects.project.id,
            },
          },
        );
        if (cancelled) return;
        const byMetric = new Map(
          response.data.policies.map((policy) => [policy.metric, policy]),
        );
        setDrafts(
          BASE_DRAFTS.map((draft) => {
            const policy = byMetric.get(draft.metric);
            return policy
              ? {
                  ...draft,
                  enabled: true,
                  period: policy.period,
                  limit: String(policy.limit_value),
                  mode: policy.mode,
                  warning: String(Math.round(policy.warning_threshold * 100)),
                }
              : { ...draft };
          }),
        );
      } catch (error) {
        if (!cancelled) {
          toast({
            title: "Failed to load limits",
            description: getErrorMessage(error),
            variant: "destructive",
          });
        }
      } finally {
        if (!cancelled) setLoadingPolicies(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [availableScopes, scopeId, scopeType, subjects]);

  const updateDraft = (metric: UsageMetric, patch: Partial<PolicyDraft>) => {
    setDrafts((current) =>
      current.map((item) =>
        item.metric === metric ? { ...item, ...patch } : item,
      ),
    );
  };

  const handleSave = async () => {
    if (!subjects || !scopeId) return;
    const invalid = drafts.find(
      (draft) =>
        draft.enabled &&
        (!Number.isInteger(Number(draft.limit)) || Number(draft.limit) <= 0),
    );
    if (invalid) {
      toast({
        title: `${invalid.label} requires a positive whole-number limit`,
        variant: "destructive",
      });
      return;
    }
    setSaving(true);
    try {
      await api.put(USAGE_ENDPOINTS.POLICIES, {
        scope_type: scopeType,
        scope_id: scopeId,
        project_id: subjects.project.id,
        policies: drafts
          .filter((draft) => draft.enabled)
          .map((draft) => ({
            metric: draft.metric,
            period: draft.metric === "stored_memories" ? "total" : draft.period,
            limit_value: Number(draft.limit),
            mode: draft.mode,
            warning_threshold:
              Math.min(100, Math.max(1, Number(draft.warning) || 80)) / 100,
          })),
      });
      toast({ title: "Usage limits updated", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to update limits",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const visibleDrafts = drafts.filter(
    (draft) =>
      draft.metric !== "stored_memories" ||
      scopeType === "organization" ||
      scopeType === "project",
  );

  if (isLoading) {
    return (
      <div className="space-y-4">
        <Skeleton className="h-8 w-56" />
        <Skeleton className="h-[420px] w-full" />
      </div>
    );
  }

  if (!availableScopes.length) {
    return (
      <div className="space-y-5">
        <Button asChild variant="ghost" size="sm">
          <Link href="/dashboard/billing">
            <ArrowLeft className="mr-1 size-4" />
            Usage
          </Link>
        </Button>
        <Card className="border-memBorder-primary">
          <CardContent className="flex min-h-40 items-center gap-3 p-6">
            <ShieldAlert className="size-5 text-onSurface-default-secondary" />
            <p className="text-sm">
              Owner access is required to manage usage limits.
            </p>
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Button asChild variant="ghost" size="sm" className="-ml-2 mb-1">
            <Link href="/dashboard/billing">
              <ArrowLeft className="mr-1 size-4" />
              Usage
            </Link>
          </Button>
          <h1 className="text-xl font-semibold font-fustat">
            Usage &amp; Limits
          </h1>
        </div>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={saving || loadingPolicies || !scopeId}
        >
          <Save className="mr-1.5 size-4" />
          {saving ? "Saving" : "Save limits"}
        </Button>
      </div>

      <div className="grid gap-4 sm:grid-cols-2">
        <div className="space-y-2">
          <Label>Scope</Label>
          <Select
            value={scopeType}
            onValueChange={(value) => {
              setScopeType(value as UsageScopeType);
              setScopeId("");
            }}
          >
            <SelectTrigger variant="dropdown">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {availableScopes.map((scope) => (
                <SelectItem key={scope} value={scope}>
                  {SCOPE_LABELS[scope]}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <Label>Subject</Label>
          <Select
            value={scopeId}
            onValueChange={setScopeId}
            disabled={!options.length}
          >
            <SelectTrigger variant="dropdown">
              <SelectValue placeholder="No subjects available" />
            </SelectTrigger>
            <SelectContent>
              {options.map((option) => (
                <SelectItem key={option.id} value={option.id}>
                  {option.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      <Card className="border-memBorder-primary overflow-hidden">
        <CardHeader className="border-b border-memBorder-primary pb-4">
          <div className="flex items-center justify-between">
            <CardTitle className="text-sm">Policies</CardTitle>
            <span className="text-xs text-onSurface-default-secondary">
              Default: Unlimited
            </span>
          </div>
        </CardHeader>
        <CardContent className="p-0">
          {loadingPolicies ? (
            <div className="space-y-3 p-5">
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
              <Skeleton className="h-14 w-full" />
            </div>
          ) : (
            <div className="divide-y divide-memBorder-primary">
              {visibleDrafts.map((draft) => (
                <div
                  key={draft.metric}
                  className="grid gap-4 p-4 lg:grid-cols-[minmax(150px,1fr)_120px_150px_130px_110px] lg:items-end"
                >
                  <div className="flex min-h-9 items-center gap-3">
                    <Switch
                      checked={draft.enabled}
                      onCheckedChange={(enabled) =>
                        updateDraft(draft.metric, { enabled })
                      }
                    />
                    <div>
                      <p className="text-sm font-medium">{draft.label}</p>
                      <p className="text-xs text-onSurface-default-tertiary">
                        {draft.enabled ? "Limited" : "Unlimited"}
                      </p>
                    </div>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Limit</Label>
                    <Input
                      type="number"
                      min={1}
                      step={1}
                      value={draft.limit}
                      onChange={(event) =>
                        updateDraft(draft.metric, { limit: event.target.value })
                      }
                      disabled={!draft.enabled}
                    />
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Period</Label>
                    <Select
                      value={
                        draft.metric === "stored_memories"
                          ? "total"
                          : draft.period
                      }
                      onValueChange={(period) =>
                        updateDraft(draft.metric, { period: period as Period })
                      }
                      disabled={
                        !draft.enabled || draft.metric === "stored_memories"
                      }
                    >
                      <SelectTrigger variant="dropdown">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        {draft.metric === "stored_memories" ? (
                          <SelectItem value="total">Total</SelectItem>
                        ) : (
                          <>
                            <SelectItem value="minute">Per minute</SelectItem>
                            <SelectItem value="day">Per day</SelectItem>
                            <SelectItem value="month">Per month</SelectItem>
                          </>
                        )}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Mode</Label>
                    <Select
                      value={draft.mode}
                      onValueChange={(mode) =>
                        updateDraft(draft.metric, { mode: mode as QuotaMode })
                      }
                      disabled={!draft.enabled}
                    >
                      <SelectTrigger variant="dropdown">
                        <SelectValue />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectItem value="monitor">Monitor</SelectItem>
                        <SelectItem value="soft">Soft</SelectItem>
                        <SelectItem value="hard">Hard</SelectItem>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-1.5">
                    <Label className="text-xs">Warn at %</Label>
                    <Input
                      type="number"
                      min={1}
                      max={100}
                      value={draft.warning}
                      onChange={(event) =>
                        updateDraft(draft.metric, {
                          warning: event.target.value,
                        })
                      }
                      disabled={!draft.enabled || draft.mode === "monitor"}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
