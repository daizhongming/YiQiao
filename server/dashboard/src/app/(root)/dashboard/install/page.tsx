// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useState } from "react";
import { BookOpen, Check, Copy, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

type SdkMode = "sync" | "async";

const INSTALL_COMMAND = "python -m pip install yiqiao";

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

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  const { t } = useI18n();

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  const label = copied ? t("Copied") : t("Copy");

  return (
    <Button
      type="button"
      size="sm"
      className="h-8 shrink-0"
      onClick={copy}
      aria-label={label}
      title={label}
    >
      {copied ? (
        <Check className="mr-1.5 size-3.5" />
      ) : (
        <Copy className="mr-1.5 size-3.5" />
      )}
      {label}
    </Button>
  );
}

function CodeBlock({ code }: { code: string }) {
  return (
    <div className="flex min-w-0 items-start gap-3 rounded-md border border-memBorder-primary bg-surface-default-secondary p-3">
      <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre text-xs leading-5 text-onSurface-default-primary">
        <code>{code}</code>
      </pre>
      <CopyButton value={code} />
    </div>
  );
}

function SegmentedButton({
  active,
  children,
  onClick,
}: {
  active: boolean;
  children: React.ReactNode;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "min-h-9 rounded-md px-4 py-2 text-xs font-semibold transition-colors",
        active
          ? "bg-surface-default-primary text-onSurface-default-primary shadow-sm"
          : "text-onSurface-default-tertiary hover:text-onSurface-default-primary",
      )}
    >
      {children}
    </button>
  );
}

export default function InstallPage() {
  const [sdkMode, setSdkMode] = useState<SdkMode>("sync");
  const { t } = useI18n();
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8888";

  return (
    <div className="mx-auto w-full max-w-[870px] space-y-6 pb-8">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold font-fustat">
            {t("YiQiao SDK")}
          </h1>
          <p className="mt-1 text-sm text-onSurface-default-secondary">
            {t("Use the Python SDK in your application.")}
          </p>
        </div>
        <a
          href={`${apiUrl}/docs`}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-flex h-9 shrink-0 items-center gap-2 self-start rounded-md border border-memBorder-primary px-3 text-xs font-medium text-onSurface-default-primary transition-colors hover:bg-surface-default-secondary"
        >
          <BookOpen className="size-4" />
          {t("API Reference")}
          <ExternalLink className="size-3.5 text-onSurface-default-tertiary" />
        </a>
      </div>

      <div className="inline-flex max-w-full rounded-md bg-surface-default-secondary p-1">
        <SegmentedButton
          active={sdkMode === "sync"}
          onClick={() => setSdkMode("sync")}
        >
          {t("Synchronous")}
        </SegmentedButton>
        <SegmentedButton
          active={sdkMode === "async"}
          onClick={() => setSdkMode("async")}
        >
          {t("Asynchronous")}
        </SegmentedButton>
      </div>

      <div className="space-y-5 rounded-lg border border-memBorder-primary p-4 sm:p-5">
        <section className="space-y-2">
          <div>
            <h2 className="text-sm font-semibold">
              {t("Step 1: Install the SDK")}
            </h2>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t("Install the published YiQiao package from PyPI.")}
            </p>
          </div>
          <CodeBlock code={INSTALL_COMMAND} />
        </section>

        <section className="space-y-2">
          <div>
            <h2 className="text-sm font-semibold">
              {t("Step 2: Add and search memories")}
            </h2>
            <p className="mt-1 text-xs text-onSurface-default-tertiary">
              {t(
                "The SDK reads provider credentials such as OPENAI_API_KEY from your environment.",
              )}
            </p>
          </div>
          <CodeBlock code={SDK_EXAMPLES[sdkMode]} />
        </section>
      </div>
    </div>
  );
}
