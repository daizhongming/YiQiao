// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useEffect, useState } from "react";
import {
  Binary,
  BrainCircuit,
  CheckCircle2,
  CircleAlert,
  ListFilter,
  Loader2,
  Save,
  TestTube2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { toast } from "@/components/ui/use-toast";
import { getErrorMessage } from "@/lib/error-message";
import { api } from "@/utils/api";
import { MEMORY_ENDPOINTS } from "@/utils/api-endpoints";
import {
  buildProviderConfig,
  buildRerankerConfig,
  getEffectiveConfig,
  getProviderBaseUrl,
  hasConfiguredApiKey,
  type ProviderConfig,
} from "@/utils/self-hosted-config";
import { useAuth } from "@/hooks/use-auth";
import { useApiQuery } from "@/hooks/use-api-query";

type ModelKind = "llm" | "embedder" | "reranker";

type BundledProviders = {
  llm: string[];
  embedder: string[];
  reranker: string[];
};

type ModelTestResponse = {
  status: "ok";
  kind: ModelKind;
  latency_ms: number;
  preview?: string;
  dimensions?: number;
  results?: Array<{ id?: string; score?: number }>;
};

type TestResult = {
  status: "success" | "error";
  message: string;
};

const positiveInteger = (value: string) => {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : undefined;
};

function TestStatus({ result }: { result?: TestResult }) {
  if (!result) return null;

  const Icon = result.status === "success" ? CheckCircle2 : CircleAlert;
  return (
    <div
      className={`flex min-h-5 items-center gap-2 text-xs ${
        result.status === "success"
          ? "text-emerald-700 dark:text-emerald-400"
          : "text-onSurface-danger-primary"
      }`}
      role="status"
    >
      <Icon className="size-4 shrink-0" />
      <span className="break-words">{result.message}</span>
    </div>
  );
}

export default function ConfigurationPage() {
  const { isAdmin } = useAuth();
  const [isSaving, setIsSaving] = useState(false);
  const [testingKind, setTestingKind] = useState<ModelKind | null>(null);
  const [testResults, setTestResults] = useState<
    Partial<Record<ModelKind, TestResult>>
  >({});

  const [llmProvider, setLlmProvider] = useState("openai");
  const [llmModel, setLlmModel] = useState("");
  const [llmBaseUrl, setLlmBaseUrl] = useState("");
  const [llmApiKey, setLlmApiKey] = useState("");

  const [embedderProvider, setEmbedderProvider] = useState("openai");
  const [embedderModel, setEmbedderModel] = useState("");
  const [embedderBaseUrl, setEmbedderBaseUrl] = useState("");
  const [embedderApiKey, setEmbedderApiKey] = useState("");
  const [embedderDimensions, setEmbedderDimensions] = useState("1024");

  const [rerankerLlmProvider, setRerankerLlmProvider] = useState("openai");
  const [rerankerModel, setRerankerModel] = useState("");
  const [rerankerBaseUrl, setRerankerBaseUrl] = useState("");
  const [rerankerApiKey, setRerankerApiKey] = useState("");
  const [rerankerTopK, setRerankerTopK] = useState("5");

  const {
    data: config,
    isLoading: isPrefilling,
    refetch: refetchConfig,
  } = useApiQuery(
    async () => {
      const res = await api.get(MEMORY_ENDPOINTS.CONFIGURE);
      return getEffectiveConfig(res.data);
    },
    { errorToast: "Failed to load server configuration" },
  );

  const { data: providers } = useApiQuery<BundledProviders>(
    async () => {
      const res = await api.get<BundledProviders>(
        MEMORY_ENDPOINTS.CONFIGURE_PROVIDERS,
      );
      return res.data;
    },
    { errorToast: "Failed to load bundled providers" },
  );

  useEffect(() => {
    if (!config) return;

    setLlmProvider(config.llm?.provider || "openai");
    setLlmModel(config.llm?.config?.model || "");
    setLlmBaseUrl(getProviderBaseUrl(config.llm));

    setEmbedderProvider(config.embedder?.provider || "openai");
    setEmbedderModel(config.embedder?.config?.model || "");
    setEmbedderBaseUrl(getProviderBaseUrl(config.embedder));
    setEmbedderDimensions(
      String(
        config.embedder?.config?.embedding_dims ??
          config.vector_store?.config?.embedding_model_dims ??
          1024,
      ),
    );

    const rerankerLlm = config.reranker?.config?.llm;
    setRerankerLlmProvider(rerankerLlm?.provider || "openai");
    setRerankerModel(
      config.reranker?.config?.model || rerankerLlm?.config?.model || "",
    );
    setRerankerBaseUrl(getProviderBaseUrl(rerankerLlm));
    setRerankerTopK(String(config.reranker?.config?.top_k ?? 5));
  }, [config]);

  const configuredLlmKey = hasConfiguredApiKey(config?.llm);
  const configuredEmbedderKey = hasConfiguredApiKey(config?.embedder);
  const configuredRerankerKey =
    hasConfiguredApiKey(config?.reranker) ||
    hasConfiguredApiKey(config?.reranker?.config?.llm);

  const buildSection = (kind: ModelKind): ProviderConfig | undefined => {
    if (kind === "llm") {
      return buildProviderConfig({
        provider: llmProvider,
        model: llmModel,
        apiKey: llmApiKey,
        baseUrl: llmBaseUrl,
      });
    }
    if (kind === "embedder") {
      return buildProviderConfig({
        provider: embedderProvider,
        model: embedderModel,
        apiKey: embedderApiKey,
        baseUrl: embedderBaseUrl,
        dimensions: positiveInteger(embedderDimensions),
      });
    }
    return buildRerankerConfig({
      llmProvider: rerankerLlmProvider,
      model: rerankerModel,
      apiKey: rerankerApiKey,
      baseUrl: rerankerBaseUrl,
      topK: positiveInteger(rerankerTopK),
    });
  };

  const handleTest = async (kind: ModelKind) => {
    const section = buildSection(kind);
    if (!section?.provider || !section.config?.model) {
      toast({
        title: "Model ID is required",
        variant: "destructive",
      });
      return;
    }

    if (kind === "embedder" && !positiveInteger(embedderDimensions)) {
      toast({ title: "Dimensions must be positive", variant: "destructive" });
      return;
    }
    if (kind === "reranker" && !positiveInteger(rerankerTopK)) {
      toast({ title: "Top K must be positive", variant: "destructive" });
      return;
    }

    setTestingKind(kind);
    setTestResults((current) => ({ ...current, [kind]: undefined }));
    try {
      const response = await api.post<ModelTestResponse>(
        MEMORY_ENDPOINTS.CONFIGURE_TEST,
        {
          kind,
          provider: section.provider,
          config: section.config,
        },
      );
      const data = response.data;
      const detail =
        kind === "llm"
          ? data.preview || "Connected"
          : kind === "embedder"
            ? `${data.dimensions} dimensions`
            : `${data.results?.length || 0} results`;
      const message = `${detail}, ${data.latency_ms} ms`;
      setTestResults((current) => ({
        ...current,
        [kind]: { status: "success", message },
      }));
      toast({
        title: "Model test passed",
        description: message,
        variant: "success",
      });
    } catch (error) {
      const message = getErrorMessage(error, "Model test failed");
      setTestResults((current) => ({
        ...current,
        [kind]: { status: "error", message },
      }));
      toast({
        title: "Model test failed",
        description: message,
        variant: "destructive",
      });
    } finally {
      setTestingKind(null);
    }
  };

  const handleSave = async () => {
    const dimensions = positiveInteger(embedderDimensions);
    const topK = positiveInteger(rerankerTopK);
    if (!dimensions) {
      toast({ title: "Dimensions must be positive", variant: "destructive" });
      return;
    }
    if (rerankerModel && !topK) {
      toast({ title: "Top K must be positive", variant: "destructive" });
      return;
    }

    const llm = buildSection("llm");
    const embedder = buildSection("embedder");
    const reranker = rerankerModel.trim()
      ? buildSection("reranker")
      : undefined;
    if (!llm?.config?.model || !embedder?.config?.model) {
      toast({
        title: "LLM and embedding model IDs are required",
        variant: "destructive",
      });
      return;
    }

    setIsSaving(true);
    try {
      const newConfig: Record<string, unknown> = {
        version: "v1.1",
        llm,
        embedder,
        vector_store: {
          config: { embedding_model_dims: dimensions },
        },
      };
      if (reranker) newConfig.reranker = reranker;

      await api.post(MEMORY_ENDPOINTS.CONFIGURE, newConfig);
      setLlmApiKey("");
      setEmbedderApiKey("");
      setRerankerApiKey("");
      await refetchConfig();
      toast({ title: "Configuration saved", variant: "success" });
    } catch (error) {
      toast({
        title: "Failed to save configuration",
        description: getErrorMessage(error),
        variant: "destructive",
      });
    } finally {
      setIsSaving(false);
    }
  };

  const testButton = (kind: ModelKind) => (
    <Button
      type="button"
      variant="outline"
      size="sm"
      onClick={() => handleTest(kind)}
      disabled={!isAdmin || testingKind !== null}
    >
      {testingKind === kind ? (
        <Loader2 className="mr-2 size-4 animate-spin" />
      ) : (
        <TestTube2 className="mr-2 size-4" />
      )}
      {testingKind === kind ? "Testing..." : "Test"}
    </Button>
  );

  return (
    <div className="space-y-5 pb-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold font-fustat">Configuration</h1>
        {isPrefilling && (
          <p className="text-sm text-onSurface-default-tertiary">
            Loading effective server configuration...
          </p>
        )}
      </div>

      <Card className="rounded-lg border-memBorder-primary">
        <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 p-5">
          <CardTitle className="flex items-center gap-2 text-sm">
            <BrainCircuit className="size-4" />
            LLM
          </CardTitle>
          {testButton("llm")}
        </CardHeader>
        <CardContent className="space-y-4 px-5 pb-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
            <div className="space-y-1.5">
              <Label className="text-xs">Provider</Label>
              <Select
                value={llmProvider}
                onValueChange={setLlmProvider}
                disabled={!isAdmin || !providers}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select provider" />
                </SelectTrigger>
                <SelectContent>
                  {providers?.llm.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Model ID</Label>
              <Input
                value={llmModel}
                onChange={(event) => setLlmModel(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Base URL</Label>
              <Input
                value={llmBaseUrl}
                onChange={(event) => setLlmBaseUrl(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">API Key</Label>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  configuredLlmKey
                    ? "Configured - leave blank to keep"
                    : "API key"
                }
                value={llmApiKey}
                onChange={(event) => setLlmApiKey(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
          </div>
          <TestStatus result={testResults.llm} />
        </CardContent>
      </Card>

      <Card className="rounded-lg border-memBorder-primary">
        <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 p-5">
          <CardTitle className="flex items-center gap-2 text-sm">
            <Binary className="size-4" />
            Embedding
          </CardTitle>
          {testButton("embedder")}
        </CardHeader>
        <CardContent className="space-y-4 px-5 pb-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Provider</Label>
              <Select
                value={embedderProvider}
                onValueChange={setEmbedderProvider}
                disabled={!isAdmin || !providers}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select provider" />
                </SelectTrigger>
                <SelectContent>
                  {providers?.embedder.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Model ID</Label>
              <Input
                value={embedderModel}
                onChange={(event) => setEmbedderModel(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Dimensions</Label>
              <Input
                type="number"
                min={1}
                step={1}
                value={embedderDimensions}
                onChange={(event) => setEmbedderDimensions(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5 xl:col-span-2">
              <Label className="text-xs">Base URL</Label>
              <Input
                value={embedderBaseUrl}
                onChange={(event) => setEmbedderBaseUrl(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">API Key</Label>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  configuredEmbedderKey
                    ? "Configured - leave blank to keep"
                    : "API key"
                }
                value={embedderApiKey}
                onChange={(event) => setEmbedderApiKey(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
          </div>
          <TestStatus result={testResults.embedder} />
        </CardContent>
      </Card>

      <Card className="rounded-lg border-memBorder-primary">
        <CardHeader className="flex flex-row items-center justify-between gap-3 space-y-0 p-5">
          <CardTitle className="flex items-center gap-2 text-sm">
            <ListFilter className="size-4" />
            Rerank
          </CardTitle>
          {testButton("reranker")}
        </CardHeader>
        <CardContent className="space-y-4 px-5 pb-5">
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 xl:grid-cols-3">
            <div className="space-y-1.5">
              <Label className="text-xs">Provider</Label>
              <Select
                value={rerankerLlmProvider}
                onValueChange={setRerankerLlmProvider}
                disabled={!isAdmin || !providers}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select provider" />
                </SelectTrigger>
                <SelectContent>
                  {providers?.llm.map((name) => (
                    <SelectItem key={name} value={name}>
                      {name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Model ID</Label>
              <Input
                value={rerankerModel}
                onChange={(event) => setRerankerModel(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">Top K</Label>
              <Input
                type="number"
                min={1}
                step={1}
                value={rerankerTopK}
                onChange={(event) => setRerankerTopK(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5 xl:col-span-2">
              <Label className="text-xs">Base URL</Label>
              <Input
                value={rerankerBaseUrl}
                onChange={(event) => setRerankerBaseUrl(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
            <div className="space-y-1.5">
              <Label className="text-xs">API Key</Label>
              <Input
                type="password"
                autoComplete="new-password"
                placeholder={
                  configuredRerankerKey
                    ? "Configured - leave blank to keep"
                    : "API key"
                }
                value={rerankerApiKey}
                onChange={(event) => setRerankerApiKey(event.target.value)}
                disabled={!isAdmin}
              />
            </div>
          </div>
          <TestStatus result={testResults.reranker} />
        </CardContent>
      </Card>

      {isAdmin && (
        <div className="flex justify-end">
          <Button
            onClick={handleSave}
            disabled={isSaving || testingKind !== null}
          >
            {isSaving ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : (
              <Save className="mr-2 size-4" />
            )}
            {isSaving ? "Saving..." : "Save Configuration"}
          </Button>
        </div>
      )}
    </div>
  );
}
