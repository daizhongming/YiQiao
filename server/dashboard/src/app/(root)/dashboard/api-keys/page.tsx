// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import DeleteConfirmationModal from "@/components/ui/delete-confirmation-modal";
import { api } from "@/utils/api";
import { API_KEY_ENDPOINTS } from "@/utils/api-endpoints";
import { toast } from "@/components/ui/use-toast";
import {
  CalendarDays,
  Check,
  Copy,
  FolderKey,
  Info,
  KeyRound,
  ShieldCheck,
  Plus,
  Trash2,
} from "lucide-react";
import { CopyToClipboard } from "react-copy-to-clipboard";
import { format } from "date-fns";
import { enUS, zhCN } from "date-fns/locale";
import { getErrorMessage } from "@/lib/error-message";
import { useI18n } from "@/lib/i18n";
import { useApiQuery } from "@/hooks/use-api-query";
import { ApiKey, ApiKeyCreateResponse, ApiKeyScope } from "@/types/api";

const DEFAULT_SCOPES: ApiKeyScope[] = ["memory:read", "memory:write"];

export default function ApiKeysPage() {
  const { language, t } = useI18n();
  const dateLocale = language === "zh" ? zhCN : enUS;
  const [createOpen, setCreateOpen] = useState(false);
  const [newLabel, setNewLabel] = useState(() => t("YiQiao Default Key"));
  const [newKey, setNewKey] = useState("");
  const [newScopes, setNewScopes] = useState<ApiKeyScope[]>(DEFAULT_SCOPES);
  const [newExpiresAt, setNewExpiresAt] = useState("");
  const [copied, setCopied] = useState(false);
  const [creating, setCreating] = useState(false);
  const [keyToRevoke, setKeyToRevoke] = useState<ApiKey | null>(null);

  const {
    data: keys = [],
    isLoading,
    refetch,
  } = useApiQuery<ApiKey[]>(
    async () => {
      const res = await api.get<ApiKey[]>(API_KEY_ENDPOINTS.BASE);
      return res.data ?? [];
    },
    { errorToast: "Failed to load API keys", initialData: [] },
  );

  const handleCreate = async () => {
    setCreating(true);
    try {
      const res = await api.post<ApiKeyCreateResponse>(API_KEY_ENDPOINTS.BASE, {
        label: newLabel.trim(),
        scopes: newScopes,
        expires_at: newExpiresAt ? new Date(newExpiresAt).toISOString() : null,
      });
      setNewKey(res.data.key);
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to create key",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setCreating(false);
    }
  };

  const toggleScope = (scope: ApiKeyScope, checked: boolean) => {
    setNewScopes((current) =>
      checked
        ? Array.from(new Set([...current, scope]))
        : current.filter((item) => item !== scope),
    );
  };

  const handleRevoke = async () => {
    if (!keyToRevoke) return;
    try {
      await api.delete(API_KEY_ENDPOINTS.BY_ID(keyToRevoke.id));
      toast({ title: "API key revoked", variant: "success" });
      setKeyToRevoke(null);
      void refetch();
    } catch (error) {
      toast({
        title: "Failed to revoke key",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const handleDialogClose = (open: boolean) => {
    if (!open) {
      setNewKey("");
      setNewLabel(t("YiQiao Default Key"));
      setNewScopes(DEFAULT_SCOPES);
      setNewExpiresAt("");
      setCopied(false);
    }
    setCreateOpen(open);
  };

  const columns = [
    {
      key: "label" as keyof ApiKey,
      label: "Key Name",
      icon: FolderKey,
      width: 180,
    },
    {
      key: "key_prefix" as keyof ApiKey,
      label: "API Key",
      icon: KeyRound,
      width: 280,
      render: (value: string) => (
        <code className="block truncate text-xs font-mono">
          {value}
          {"*".repeat(28)}
        </code>
      ),
    },
    {
      key: "scopes" as keyof ApiKey,
      label: "Permissions",
      icon: ShieldCheck,
      width: 220,
      render: (_: ApiKey[keyof ApiKey], row: ApiKey) => {
        const effectiveScopes = row.scopes ?? DEFAULT_SCOPES;
        return (
          <div className="flex flex-wrap gap-1">
            {effectiveScopes.map((scope) => (
              <Badge key={scope} variant="outline" className="font-mono">
                {scope}
              </Badge>
            ))}
            {row.scopes === null && <Badge variant="secondary">Legacy</Badge>}
            {effectiveScopes.length === 0 && (
              <span className="text-xs text-onSurface-default-secondary">
                None
              </span>
            )}
          </div>
        );
      },
    },
    {
      key: "expires_at" as keyof ApiKey,
      label: "Expires At",
      icon: CalendarDays,
      width: 190,
      render: (value: ApiKey[keyof ApiKey]) =>
        typeof value === "string"
          ? format(new Date(value), "PPp", { locale: dateLocale })
          : "Never",
    },
    {
      key: "created_at" as keyof ApiKey,
      label: "Created At",
      icon: CalendarDays,
      width: 190,
      render: (value: string) =>
        format(
          new Date(value),
          language === "zh"
            ? "yyyy年M月d日 HH:mm:ss"
            : "h:mm:ss a, MMM d, yyyy",
          { locale: dateLocale },
        ),
    },
    {
      key: "id" as keyof ApiKey,
      label: "",
      width: 56,
      render: (_: string, row: ApiKey) => (
        <div className="flex items-center justify-end gap-1">
          <Button
            variant="ghost"
            size="icon"
            onClick={() => setKeyToRevoke(row)}
            className="size-7"
            aria-label={t("Revoke API key")}
            title={t("Revoke API key")}
          >
            <Trash2 className="size-3.5 text-onSurface-danger-primary" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-xl font-semibold font-fustat">Manage API Keys</h1>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            Easily create, view, and manage your API keys for seamless
            integration.
          </p>
        </div>
        <Dialog open={createOpen} onOpenChange={handleDialogClose}>
          <DialogTrigger asChild>
            <Button size="sm" className="shrink-0">
              <Plus className="size-4 mr-1" /> Create API Key
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create API Key</DialogTitle>
            </DialogHeader>
            {!newKey ? (
              <form
                className="mt-2 space-y-4"
                onSubmit={(event) => {
                  event.preventDefault();
                  void handleCreate();
                }}
              >
                <div className="space-y-2">
                  <Label htmlFor="api-key-label">Key Name</Label>
                  <Input
                    id="api-key-label"
                    value={newLabel}
                    onChange={(e) => setNewLabel(e.target.value)}
                    placeholder={t("YiQiao Default Key")}
                    autoFocus
                  />
                </div>
                <fieldset className="space-y-2">
                  <legend className="text-sm font-medium">Permissions</legend>
                  <div className="grid gap-2 sm:grid-cols-2">
                    {DEFAULT_SCOPES.map((scope) => (
                      <Label
                        key={scope}
                        htmlFor={`api-key-scope-${scope}`}
                        className="flex min-h-10 cursor-pointer items-center gap-2 rounded-md border border-memBorder-primary px-3 py-2 font-normal"
                      >
                        <Checkbox
                          id={`api-key-scope-${scope}`}
                          checked={newScopes.includes(scope)}
                          onCheckedChange={(checked) =>
                            toggleScope(scope, checked === true)
                          }
                        />
                        <span className="font-mono text-xs">{scope}</span>
                      </Label>
                    ))}
                  </div>
                </fieldset>
                <div className="space-y-2">
                  <Label htmlFor="api-key-expiration">Expires At</Label>
                  <Input
                    id="api-key-expiration"
                    type="datetime-local"
                    value={newExpiresAt}
                    onChange={(event) => setNewExpiresAt(event.target.value)}
                  />
                </div>
                <Button
                  type="submit"
                  disabled={!newLabel.trim() || creating}
                  className="w-full"
                >
                  {creating ? "Creating..." : "Create API Key"}
                </Button>
              </form>
            ) : (
              <div className="space-y-4 mt-2">
                <div className="space-y-2">
                  <Label htmlFor="api-key-new">Your API Key</Label>
                  <div className="flex gap-2">
                    <Input
                      id="api-key-new"
                      value={newKey}
                      readOnly
                      className="font-mono text-sm"
                    />
                    <CopyToClipboard
                      text={newKey}
                      onCopy={() => {
                        setCopied(true);
                        setTimeout(() => setCopied(false), 2000);
                      }}
                    >
                      <Button
                        variant="outline"
                        size="icon"
                        aria-label={t(copied ? "Copied" : "Copy API key")}
                        title={t(copied ? "Copied" : "Copy API key")}
                      >
                        {copied ? (
                          <Check className="size-4" />
                        ) : (
                          <Copy className="size-4" />
                        )}
                      </Button>
                    </CopyToClipboard>
                  </div>
                  <p className="text-xs text-onSurface-danger-primary">
                    Save this key -- you won&apos;t see it again.
                  </p>
                </div>
                <Button
                  onClick={() => handleDialogClose(false)}
                  className="w-full"
                >
                  Done
                </Button>
              </div>
            )}
          </DialogContent>
        </Dialog>
      </div>

      <div className="flex items-start gap-3 rounded-md border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-700 dark:border-blue-900/50 dark:bg-blue-950/30 dark:text-blue-200">
        <Info className="mt-0.5 size-4 shrink-0" />
        <p>
          API keys are securely hashed and cannot be viewed again. If you did
          not save a key, create a new one and update your integration.
        </p>
      </div>

      {isLoading ? (
        <TableSkeleton rows={3} columns={4} />
      ) : keys.length === 0 ? (
        <div className="py-6 text-center text-sm text-onSurface-default-secondary">
          No API keys found.
        </div>
      ) : (
        <Card className="border-memBorder-primary overflow-hidden">
          <DataTable
            data={keys}
            columns={columns}
            getRowKey={(row) => row.id}
          />
        </Card>
      )}

      <DeleteConfirmationModal
        isOpen={!!keyToRevoke}
        onClose={() => setKeyToRevoke(null)}
        onConfirm={handleRevoke}
        title="Revoke API key"
        description="Applications using this key will immediately stop working. This cannot be undone."
        itemName={keyToRevoke?.label ?? ""}
        confirmButtonText="Revoke"
      />
    </div>
  );
}
