// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import Link from "next/link";
import { useState } from "react";
import {
  BookOpen,
  Bot,
  Check,
  Code2,
  Copy,
  ExternalLink,
  KeyRound,
  Network,
  ShieldCheck,
  SquareTerminal,
  Workflow,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useI18n } from "@/lib/i18n";
import {
  DEFAULT_MCP_ENDPOINT,
  buildMcpClientConfig,
  isValidMcpEndpoint,
  normalizeMcpEndpoint,
  type McpClient,
} from "./mcp-config";

type SdkMode = "sync" | "async";

const INSTALL_COMMAND = "python -m pip install yiqiao";
const MCP_START_COMMAND =
  "docker compose --env-file server/.env -f server/docker-compose.yaml up -d yiqiao-mcp";
const MCP_GUIDE_BASE_URL =
  "https://github.com/daizhongming/YiQiao/blob/main/docs/yiqiao";

const SDK_EXAMPLES: Record<SdkMode, string> = {
  sync: `from yiqiao import Memory

memory = Memory()
user_id = "alice"

memory.add(
    [{"role": "user", "content": "I prefer concise answers."}],
    user_id=user_id,
)

memories = memory.search(
    "How should I answer Alice?",
    filters={"user_id": user_id},
)
print(memories["results"])`,
  async: `import asyncio

from yiqiao import AsyncMemory


async def main():
    memory = AsyncMemory()
    user_id = "alice"

    await memory.add(
        [{"role": "user", "content": "I prefer concise answers."}],
        user_id=user_id,
    )

    memories = await memory.search(
        "How should I answer Alice?",
        filters={"user_id": user_id},
    )
    print(memories["results"])


asyncio.run(main())`,
};

type McpClientDetails = {
  label: string;
  description: string;
  icon: LucideIcon;
  target: string;
  verify: string;
};

const MCP_CLIENTS: Record<McpClient, McpClientDetails> = {
  codex: {
    label: "Codex",
    description: "Add YiQiao to the Codex MCP server configuration.",
    icon: SquareTerminal,
    target: "~/.codex/config.toml",
    verify: "codex mcp get yiqiao",
  },
  claude: {
    label: "Claude Code",
    description: "Add YiQiao to your Claude Code project configuration.",
    icon: Code2,
    target: ".mcp.json",
    verify: "claude mcp get yiqiao",
  },
  openclaw: {
    label: "OpenClaw",
    description: "Register YiQiao in OpenClaw's standard MCP registry.",
    icon: Network,
    target: "~/.openclaw/openclaw.json",
    verify: "openclaw mcp doctor yiqiao --probe",
  },
  hermes: {
    label: "Hermes",
    description: "Add YiQiao to the Hermes MCP server list.",
    icon: Bot,
    target: "~/.hermes/config.yaml",
    verify: "hermes mcp test yiqiao",
  },
};

function CopyButton({
  value,
  label,
  className,
  disabled = false,
}: {
  value: string;
  label: string;
  className?: string;
  disabled?: boolean;
}) {
  const [status, setStatus] = useState<"idle" | "copied" | "failed">("idle");
  const { t } = useI18n();

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setStatus("copied");
    } catch {
      setStatus("failed");
    }
    window.setTimeout(() => setStatus("idle"), 1600);
  };

  const statusLabel =
    status === "copied"
      ? t("Copied")
      : status === "failed"
        ? t("Copy failed")
        : label;

  return (
    <Button
      type="button"
      variant="outline"
      size="icon"
      className={className}
      onClick={copy}
      disabled={disabled}
      aria-label={statusLabel}
      title={statusLabel}
    >
      {status === "copied" ? (
        <Check className="size-3.5" />
      ) : (
        <Copy className="size-3.5" />
      )}
      <span className="sr-only" aria-live="polite">
        {status === "idle" ? "" : statusLabel}
      </span>
    </Button>
  );
}

function CodeBlock({
  code,
  copyLabel,
  testId,
  copyDisabled = false,
}: {
  code: string;
  copyLabel: string;
  testId?: string;
  copyDisabled?: boolean;
}) {
  return (
    <div className="flex min-w-0 items-start rounded-md border border-memBorder-primary bg-surface-default-secondary">
      <pre
        className="min-w-0 flex-1 overflow-x-auto whitespace-pre p-3 text-xs leading-5 text-onSurface-default-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-inset focus-visible:ring-ring"
        data-testid={testId}
        tabIndex={0}
      >
        <code>{code}</code>
      </pre>
      <CopyButton
        value={code}
        label={copyLabel}
        disabled={copyDisabled}
        className="m-2 ml-0 size-8 shrink-0"
      />
    </div>
  );
}

function SdkQuickStart() {
  const [sdkMode, setSdkMode] = useState<SdkMode>("sync");
  const { t } = useI18n();

  return (
    <div className="space-y-5">
      <div>
        <h2 className="font-fustat text-xl font-semibold">{t("YiQiao SDK")}</h2>
        <p className="mt-1 text-sm text-onSurface-default-secondary">
          {t("Use the Python SDK in your application.")}
        </p>
      </div>

      <div
        className="inline-flex max-w-full rounded-md bg-surface-default-secondary p-1"
        role="group"
        aria-label={t("SDK mode")}
      >
        {(["sync", "async"] as const).map((mode) => (
          <button
            key={mode}
            type="button"
            aria-pressed={sdkMode === mode}
            onClick={() => setSdkMode(mode)}
            className={`min-h-9 rounded-md px-4 py-2 text-xs font-semibold transition-colors ${
              sdkMode === mode
                ? "bg-surface-default-primary text-onSurface-default-primary shadow-sm"
                : "text-onSurface-default-tertiary hover:text-onSurface-default-primary"
            }`}
          >
            {t(mode === "sync" ? "Synchronous" : "Asynchronous")}
          </button>
        ))}
      </div>

      <div className="overflow-hidden rounded-lg border border-memBorder-primary">
        <section className="space-y-2 p-4 sm:p-5">
          <div>
            <h3 className="text-sm font-semibold">
              {t("Step 1: Install the SDK")}
            </h3>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t("Install the published YiQiao package from PyPI.")}
            </p>
          </div>
          <CodeBlock
            code={INSTALL_COMMAND}
            copyLabel={t("Copy install command")}
          />
        </section>

        <section className="space-y-2 border-t border-memBorder-primary p-4 sm:p-5">
          <div>
            <h3 className="text-sm font-semibold">
              {t("Step 2: Add and search memories")}
            </h3>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t(
                "The SDK reads provider credentials such as OPENAI_API_KEY from your environment.",
              )}
            </p>
          </div>
          <CodeBlock
            code={SDK_EXAMPLES[sdkMode]}
            copyLabel={t("Copy SDK example")}
          />
        </section>
      </div>
    </div>
  );
}

function McpClientPanel({
  client,
  endpoint,
  endpointValid,
}: {
  client: McpClient;
  endpoint: string;
  endpointValid: boolean;
}) {
  const { t } = useI18n();
  const details = MCP_CLIENTS[client];
  const Icon = details.icon;
  const config = buildMcpClientConfig(client, endpoint);

  return (
    <TabsContent value={client} className="mt-4 space-y-4">
      <div className="flex items-start gap-3">
        <div className="flex size-9 shrink-0 items-center justify-center rounded-md border border-memBorder-primary bg-surface-default-secondary">
          <Icon className="size-4" />
        </div>
        <div className="min-w-0">
          <h4 className="text-sm font-semibold">{details.label}</h4>
          <p className="mt-0.5 text-xs text-onSurface-default-tertiary">
            {t(details.description)}
          </p>
        </div>
      </div>

      <div className="space-y-1">
        <p className="text-xs font-medium text-onSurface-default-secondary">
          {t("Merge into configuration")}
        </p>
        <code className="block break-all text-xs text-onSurface-default-primary">
          {details.target}
        </code>
        <p className="text-xs text-onSurface-default-tertiary">
          {t(
            "Merge the YiQiao entry with existing settings; do not replace the whole file.",
          )}
        </p>
        {client === "openclaw" ? (
          <p className="text-xs text-onSurface-default-tertiary">
            {t("Run openclaw config file to print the active path.")}
          </p>
        ) : null}
      </div>

      <CodeBlock
        code={config}
        copyLabel={t("Copy client configuration")}
        testId="mcp-client-config"
        copyDisabled={!endpointValid}
      />

      <div className="space-y-2">
        <p className="text-xs font-medium text-onSurface-default-secondary">
          {t("Verify connection")}
        </p>
        <CodeBlock
          code={details.verify}
          copyLabel={t("Copy verification command")}
        />
      </div>

      {client === "openclaw" ? (
        <p className="flex items-start gap-2 text-xs text-onSurface-default-tertiary">
          <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
          {t(
            "This uses OpenClaw's standard MCP registry, not a native plugin or OAuth flow.",
          )}
        </p>
      ) : null}
    </TabsContent>
  );
}

function McpQuickStart() {
  const [endpoint, setEndpoint] = useState(DEFAULT_MCP_ENDPOINT);
  const [effectiveEndpoint, setEffectiveEndpoint] =
    useState(DEFAULT_MCP_ENDPOINT);
  const { t } = useI18n();
  const endpointValid = isValidMcpEndpoint(endpoint);

  const updateEndpoint = (value: string) => {
    setEndpoint(value);
    if (isValidMcpEndpoint(value)) {
      setEffectiveEndpoint(normalizeMcpEndpoint(value));
    }
  };

  return (
    <div className="space-y-5">
      <div>
        <h2 className="font-fustat text-xl font-semibold">{t("YiQiao MCP")}</h2>
        <p className="mt-1 text-sm text-onSurface-default-secondary">
          {t(
            "Connect coding agents and agent runtimes through the Model Context Protocol.",
          )}
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          {["Streamable HTTP", "Memory profile", "Project-scoped key"].map(
            (label) => (
              <span
                key={label}
                className="rounded-md border border-memBorder-primary bg-surface-default-secondary px-2 py-1 text-[11px] font-medium text-onSurface-default-secondary"
              >
                {t(label)}
              </span>
            ),
          )}
        </div>
      </div>

      <div className="overflow-hidden rounded-lg border border-memBorder-primary">
        <section className="space-y-2 p-4 sm:p-5">
          <div>
            <h3 className="text-sm font-semibold">
              {t("Step 1: Start the MCP service")}
            </h3>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t(
                "Start the Streamable HTTP companion from the YiQiao repository.",
              )}
            </p>
          </div>
          <CodeBlock
            code={MCP_START_COMMAND}
            copyLabel={t("Copy start command")}
          />
        </section>

        <section className="border-t border-memBorder-primary p-4 sm:p-5">
          <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
            <div>
              <h3 className="text-sm font-semibold">
                {t("Step 2: Create a project API key")}
              </h3>
              <p className="mt-1 max-w-2xl text-xs text-onSurface-default-tertiary">
                {t(
                  "Create a scoped project key, then expose it as YIQIAO_API_KEY to the client process.",
                )}
              </p>
              <p className="mt-1 text-xs text-onSurface-default-tertiary">
                {t(
                  "Use memory:read and memory:write for the default memory profile.",
                )}
              </p>
            </div>
            <Button asChild variant="outline" size="sm" className="shrink-0">
              <Link href="/dashboard/api-keys">
                <KeyRound className="mr-2 size-3.5" />
                {t("Open API Keys")}
              </Link>
            </Button>
          </div>
        </section>

        <section className="space-y-4 border-t border-memBorder-primary p-4 sm:p-5">
          <div>
            <h3 className="text-sm font-semibold">
              {t("Step 3: Connect your client")}
            </h3>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t(
                "Change the endpoint if the agent runs on another host or network.",
              )}
            </p>
          </div>

          <div className="space-y-1.5">
            <label
              htmlFor="mcp-endpoint"
              className="text-xs font-medium text-onSurface-default-secondary"
            >
              {t("MCP endpoint")}
            </label>
            <div className="relative">
              <Input
                id="mcp-endpoint"
                type="url"
                value={endpoint}
                onChange={(event) => updateEndpoint(event.target.value)}
                className="h-9 pr-11 font-mono text-xs"
                aria-invalid={!endpointValid}
                aria-describedby={
                  endpointValid ? undefined : "mcp-endpoint-error"
                }
                inputMode="url"
                spellCheck={false}
              />
              <CopyButton
                value={effectiveEndpoint}
                label={t("Copy MCP endpoint")}
                disabled={!endpointValid}
                className="absolute right-1 top-1 size-7"
              />
            </div>
            {!endpointValid ? (
              <p
                id="mcp-endpoint-error"
                role="alert"
                className="text-xs text-destructive"
              >
                {t(
                  "Enter an absolute HTTP(S) URL without credentials, query parameters, or a fragment. Configurations keep the last valid endpoint.",
                )}
              </p>
            ) : null}
          </div>

          <Tabs defaultValue="codex">
            <TabsList
              aria-label={t("MCP client")}
              className="grid h-auto w-full grid-cols-2 gap-1 sm:grid-cols-4"
            >
              {(Object.keys(MCP_CLIENTS) as McpClient[]).map((client) => {
                const details = MCP_CLIENTS[client];
                const Icon = details.icon;
                return (
                  <TabsTrigger
                    key={client}
                    value={client}
                    className="min-h-9 gap-2 px-2 text-xs"
                  >
                    <Icon className="size-3.5 shrink-0" />
                    {details.label}
                  </TabsTrigger>
                );
              })}
            </TabsList>

            {(Object.keys(MCP_CLIENTS) as McpClient[]).map((client) => (
              <McpClientPanel
                key={client}
                client={client}
                endpoint={effectiveEndpoint}
                endpointValid={endpointValid}
              />
            ))}
          </Tabs>

          <p className="flex items-start gap-2 text-xs text-onSurface-default-tertiary">
            <ShieldCheck className="mt-0.5 size-3.5 shrink-0" />
            {t(
              "Keep project keys in environment or secret storage, never in committed configuration.",
            )}
          </p>
        </section>
      </div>
    </div>
  );
}

export default function InstallPage() {
  const { language, t } = useI18n();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8888";
  const mcpGuideUrl = `${MCP_GUIDE_BASE_URL}/${
    language === "zh" ? "MCP.zh-CN.md" : "MCP.md"
  }`;

  return (
    <div className="mx-auto w-full max-w-[960px] space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="font-fustat text-2xl font-semibold">
            {t("Integrations")}
          </h1>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            {t("Connect YiQiao to applications and agent clients.")}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <a
            href={`${apiUrl}/docs`}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border border-memBorder-primary px-3 text-xs font-medium text-onSurface-default-primary transition-colors hover:bg-surface-default-secondary"
          >
            <BookOpen className="size-4" />
            {t("API Reference")}
            <ExternalLink className="size-3.5 text-onSurface-default-tertiary" />
          </a>
          <a
            href={mcpGuideUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex h-9 shrink-0 items-center gap-2 rounded-md border border-memBorder-primary px-3 text-xs font-medium text-onSurface-default-primary transition-colors hover:bg-surface-default-secondary"
          >
            <Workflow className="size-4" />
            {t("MCP Guide")}
            <ExternalLink className="size-3.5 text-onSurface-default-tertiary" />
          </a>
        </div>
      </div>

      <Tabs defaultValue="sdk">
        <TabsList
          aria-label={t("Integration type")}
          className="grid h-auto w-full grid-cols-2 gap-1 sm:w-[360px]"
        >
          <TabsTrigger value="sdk" className="min-h-10 gap-2 text-xs">
            <Code2 className="size-4" />
            {t("Python SDK")}
          </TabsTrigger>
          <TabsTrigger value="mcp" className="min-h-10 gap-2 text-xs">
            <Workflow className="size-4" />
            {t("MCP Agents")}
          </TabsTrigger>
        </TabsList>
        <TabsContent value="sdk" className="mt-6">
          <SdkQuickStart />
        </TabsContent>
        <TabsContent value="mcp" className="mt-6">
          <McpQuickStart />
        </TabsContent>
      </Tabs>
    </div>
  );
}
