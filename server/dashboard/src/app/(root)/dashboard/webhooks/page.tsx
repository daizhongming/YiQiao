// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { FormEvent, useState } from "react";
import { Copy, Trash2 } from "lucide-react";
import { format } from "date-fns";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { DataTable } from "@/components/shared/data-table";
import { TableSkeleton } from "@/components/shared/table-skeleton";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { api } from "@/utils/api";
import { WEBHOOK_ENDPOINTS } from "@/utils/api-endpoints";
import { useApiQuery } from "@/hooks/use-api-query";
import { Webhook } from "@/types/api";

const EVENTS = [
  ["memory.added", "Add Memory"],
  ["memory.updated", "Update Memory"],
  ["memory.deleted", "Delete Memory"],
  ["memory.categorized", "Categorize Memory"],
] as const;

export default function WebhooksPage() {
  const [url, setUrl] = useState("");
  const [name, setName] = useState("");
  const [events, setEvents] = useState<string[]>(["memory.added"]);
  const [secret, setSecret] = useState("");
  const [saving, setSaving] = useState(false);
  const [testingId, setTestingId] = useState("");

  const {
    data: webhooks = [],
    isLoading,
    refetch,
  } = useApiQuery<Webhook[]>(
    async () => {
      const res = await api.get<Webhook[]>(WEBHOOK_ENDPOINTS.BASE);
      return res.data ?? [];
    },
    { errorToast: "Failed to load webhooks", initialData: [] },
  );

  const toggleEvent = (event: string) => {
    if (event === "memory.added") return;
    setEvents((current) =>
      current.includes(event)
        ? current.filter((item) => item !== event)
        : [...current, event],
    );
  };

  const createWebhook = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    try {
      const res = await api.post<Webhook>(WEBHOOK_ENDPOINTS.BASE, {
        url: url.trim(),
        name: name.trim() || "Webhook",
        events,
        enabled: true,
      });
      setSecret(res.data.signing_secret ?? "");
      setUrl("");
      setName("");
      setEvents(["memory.added"]);
      toast({ title: "Webhook created", variant: "success" });
      await refetch();
    } catch (error) {
      toast({
        title: "Failed to create webhook",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setSaving(false);
    }
  };

  const setEnabled = async (hook: Webhook, enabled: boolean) => {
    try {
      await api.patch(WEBHOOK_ENDPOINTS.BY_ID(hook.id), { enabled });
      await refetch();
    } catch (error) {
      toast({
        title: "Failed to update webhook",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const deleteWebhook = async (hook: Webhook) => {
    try {
      await api.delete(WEBHOOK_ENDPOINTS.BY_ID(hook.id));
      toast({ title: "Webhook deleted", variant: "success" });
      await refetch();
    } catch (error) {
      toast({
        title: "Failed to delete webhook",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    }
  };

  const testWebhook = async (hook: Webhook) => {
    setTestingId(hook.id);
    try {
      await api.post(WEBHOOK_ENDPOINTS.TEST(hook.id));
      toast({ title: "Test webhook sent", variant: "success" });
      await refetch();
    } catch (error) {
      toast({
        title: "Failed to test webhook",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setTestingId("");
    }
  };

  const columns = [
    {
      key: "name" as keyof Webhook,
      label: "Webhook Name",
      width: 140,
    },
    {
      key: "url" as keyof Webhook,
      label: "Endpoint",
      width: 280,
      render: (value: string) => (
        <span className="break-all font-mono text-xs">{value}</span>
      ),
    },
    {
      key: "events" as keyof Webhook,
      label: "Events",
      width: 240,
      render: (value: string[]) => (
        <div className="flex flex-wrap gap-1">
          {value.map((event) => (
            <Badge key={event} variant="outline">
              {event}
            </Badge>
          ))}
        </div>
      ),
    },
    {
      key: "last_delivery_status" as keyof Webhook,
      label: "Last Delivery",
      width: 130,
      render: (value: string | null, row: Webhook) =>
        value ? (
          <span title={row.last_delivery_at ?? undefined}>{value}</span>
        ) : (
          "--"
        ),
    },
    {
      key: "enabled" as keyof Webhook,
      label: "Enabled",
      width: 100,
      render: (value: boolean, row: Webhook) => (
        <Button
          variant="outline"
          size="sm"
          onClick={() => setEnabled(row, !value)}
        >
          {value ? "On" : "Off"}
        </Button>
      ),
    },
    {
      key: "id" as keyof Webhook,
      label: "",
      width: 120,
      render: (_value: string, row: Webhook) => (
        <div className="flex justify-end gap-1">
          <Button
            variant="outline"
            size="sm"
            disabled={testingId === row.id}
            onClick={() => testWebhook(row)}
          >
            {testingId === row.id ? "Testing" : "Test"}
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="icon"
            onClick={() => deleteWebhook(row)}
            aria-label="Delete webhook"
            title="Delete webhook"
          >
            <Trash2 className="size-4 text-onSurface-danger-primary" />
          </Button>
        </div>
      ),
    },
  ];

  return (
    <div className="min-w-0 space-y-5">
      <div>
        <h1 className="text-xl font-semibold font-fustat">Webhooks</h1>
        <p className="text-sm text-onSurface-default-secondary mt-1">
          Manage your project&apos;s webhooks to receive notifications for
          various events.
        </p>
      </div>

      <Card className="border-memBorder-primary">
        <CardContent className="p-4 sm:p-6">
          <form className="space-y-5" onSubmit={createWebhook}>
            <h2 className="text-lg font-semibold">Add New Webhook</h2>

            <div className="space-y-2">
              <Label htmlFor="webhook-name">Webhook Name</Label>
              <Input
                id="webhook-name"
                name="name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                placeholder="Enter webhook name"
                required
              />
            </div>
            <div className="space-y-2">
              <Label htmlFor="webhook-url">Endpoint URL</Label>
              <Input
                id="webhook-url"
                name="url"
                type="url"
                value={url}
                onChange={(event) => setUrl(event.target.value)}
                placeholder="https://your-webhook-url.com"
                required
              />
            </div>

            <div className="space-y-2">
              <Label>Events (at least one required)</Label>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-4">
                {EVENTS.map(([event, label]) => (
                  <label
                    key={event}
                    className="flex min-h-8 items-center gap-2 text-sm"
                  >
                    <Checkbox
                      checked={events.includes(event)}
                      disabled={event === "memory.added"}
                      onCheckedChange={() => toggleEvent(event)}
                      aria-label={label}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>

            <Button type="submit" disabled={saving}>
              {saving ? "Adding..." : "Add Webhook"}
            </Button>

            {secret && (
              <div className="space-y-2 border-t border-memBorder-primary pt-5">
                <Label>Signing secret</Label>
                <div className="flex min-w-0 gap-2">
                  <Input
                    readOnly
                    value={secret}
                    className="min-w-0 flex-1 font-mono text-xs"
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="icon"
                    className="shrink-0"
                    onClick={() => navigator.clipboard.writeText(secret)}
                    aria-label="Copy signing secret"
                    title="Copy signing secret"
                  >
                    <Copy className="size-4" />
                  </Button>
                </div>
              </div>
            )}
          </form>
        </CardContent>
      </Card>

      {isLoading ? (
        <Card className="border-memBorder-primary overflow-hidden">
          <TableSkeleton rows={5} columns={5} />
        </Card>
      ) : webhooks.length === 0 ? (
        <div className="py-1 text-center text-sm text-onSurface-default-secondary">
          No webhooks found.
        </div>
      ) : (
        <Card className="border-memBorder-primary overflow-hidden">
          <DataTable
            data={webhooks}
            columns={columns}
            getRowKey={(row) => row.id}
          />
        </Card>
      )}

      {webhooks.length > 0 && (
        <p className="text-xs text-onSurface-default-tertiary">
          New hooks are signed with HMAC SHA-256 in X-YiQiao-Signature. Created{" "}
          {format(new Date(webhooks[webhooks.length - 1].created_at), "PP")}.
        </p>
      )}
    </div>
  );
}
