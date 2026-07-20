// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export type ProviderConfig = {
  provider?: string;
  config?: {
    model?: string;
    api_key?: string;
    openai_base_url?: string;
    anthropic_base_url?: string;
    embedding_dims?: number;
    top_k?: number;
    temperature?: number;
    max_tokens?: number;
    llm?: ProviderConfig;
  };
};

export type EffectiveConfig = {
  llm?: ProviderConfig;
  embedder?: ProviderConfig;
  reranker?: ProviderConfig;
  vector_store?: {
    provider?: string;
    config?: {
      embedding_model_dims?: number;
    };
  };
};

export const getEffectiveConfig = (data: unknown): EffectiveConfig | null => {
  if (!data || typeof data !== "object") {
    return null;
  }

  const record = data as Record<string, unknown>;
  return (
    (record.effective_config as EffectiveConfig) ||
    (record.config as EffectiveConfig) ||
    (record as EffectiveConfig)
  );
};

export const buildProviderConfig = ({
  provider,
  model,
  apiKey,
  baseUrl,
  dimensions,
}: {
  provider: string;
  model: string;
  apiKey?: string;
  baseUrl?: string;
  dimensions?: number;
}) => {
  if (!provider) {
    return undefined;
  }

  const normalizedBaseUrl = baseUrl?.trim();
  const providerBaseUrl =
    provider === "anthropic"
      ? { anthropic_base_url: normalizedBaseUrl }
      : { openai_base_url: normalizedBaseUrl };

  return {
    provider,
    config: {
      model: model || undefined,
      api_key: apiKey?.trim() || undefined,
      ...providerBaseUrl,
      embedding_dims: dimensions,
    },
  };
};

export const buildRerankerConfig = ({
  llmProvider,
  model,
  apiKey,
  baseUrl,
  topK,
}: {
  llmProvider: string;
  model: string;
  apiKey?: string;
  baseUrl?: string;
  topK?: number;
}) => {
  const llm = buildProviderConfig({
    provider: llmProvider,
    model,
    apiKey,
    baseUrl,
  });
  if (!llm) {
    return undefined;
  }

  return {
    provider: "llm_reranker",
    config: {
      model: model || undefined,
      api_key: apiKey?.trim() || undefined,
      top_k: topK,
      temperature: 0,
      max_tokens: 20,
      llm,
    },
  };
};

export const getProviderBaseUrl = (provider?: ProviderConfig) =>
  provider?.config?.openai_base_url ||
  provider?.config?.anthropic_base_url ||
  "";

export const hasConfiguredApiKey = (provider?: ProviderConfig) =>
  Boolean(provider?.config?.api_key);
