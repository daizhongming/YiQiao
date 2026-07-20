"use client";

import { useEffect, useMemo, useState } from "react";
import { Check, Copy, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { getActiveProjectId } from "@/utils/api";

type SdkLanguage = "python" | "node" | "curl";

interface Step {
  label: string;
  code: string;
}

function CopyButton({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);

  const copy = async () => {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1400);
  };

  return (
    <Button
      type="button"
      size="sm"
      className="h-8 shrink-0"
      onClick={copy}
      aria-label={copied ? "Copied" : "Copy command"}
      title={copied ? "Copied" : "Copy command"}
    >
      {copied ? (
        <Check className="mr-1.5 size-3.5" />
      ) : (
        <Copy className="mr-1.5 size-3.5" />
      )}
      {copied ? "Copied" : "Copy"}
    </Button>
  );
}

function CommandStep({ label, code }: Step) {
  return (
    <div className="space-y-2">
      <p className="text-xs text-onSurface-default-tertiary">{label}</p>
      <div className="flex min-w-0 items-start gap-3 rounded-md border border-memBorder-primary bg-surface-default-secondary p-3">
        <pre className="min-w-0 flex-1 overflow-x-auto whitespace-pre text-xs leading-5 text-onSurface-default-primary">
          <code>{code}</code>
        </pre>
        <CopyButton value={code} />
      </div>
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
  const [projectId, setProjectId] = useState("default-project");
  const [sdkLanguage, setSdkLanguage] = useState<SdkLanguage>("python");
  const apiUrl = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8888";

  useEffect(() => {
    setProjectId(getActiveProjectId());
  }, []);

  const sdkSteps = useMemo<Record<SdkLanguage, Step[]>>(
    () => ({
      python: [
        { label: "Step 1: Install", code: "python -m pip install requests" },
        {
          label: "Step 2: Initialize",
          code: `import requests\n\nAPI_URL = "${apiUrl}"\nHEADERS = {\n    "X-API-Key": "<your-api-key>",\n    "X-Project-ID": "${projectId}",\n    "Content-Type": "application/json",\n}`,
        },
        {
          label: "Step 3: Add a memory",
          code: `response = requests.post(\n    f"{API_URL}/memories",\n    headers=HEADERS,\n    json={\n        "messages": [{"role": "user", "content": "I prefer concise answers."}],\n        "user_id": "alice",\n    },\n)\nresponse.raise_for_status()\nprint(response.json())`,
        },
        {
          label: "Step 4: Retrieve memories",
          code: `response = requests.post(\n    f"{API_URL}/search",\n    headers=HEADERS,\n    json={\n        "query": "How should I answer Alice?",\n        "filters": {"user_id": "alice"},\n    },\n)\nresponse.raise_for_status()\nprint(response.json())`,
        },
      ],
      node: [
        {
          label: "Step 1: Verify Node.js",
          code: "node --version",
        },
        {
          label: "Step 2: Initialize",
          code: `const apiUrl = "${apiUrl}";\nconst headers = {\n  "X-API-Key": "<your-api-key>",\n  "X-Project-ID": "${projectId}",\n  "Content-Type": "application/json",\n};`,
        },
        {
          label: "Step 3: Add a memory",
          code: `const added = await fetch(\`${apiUrl}/memories\`, {\n  method: "POST",\n  headers,\n  body: JSON.stringify({\n    messages: [{ role: "user", content: "I prefer concise answers." }],\n    user_id: "alice",\n  }),\n});\nif (!added.ok) throw new Error(await added.text());\nconsole.log(await added.json());`,
        },
        {
          label: "Step 4: Retrieve memories",
          code: `const found = await fetch(\`${apiUrl}/search\`, {\n  method: "POST",\n  headers,\n  body: JSON.stringify({\n    query: "How should I answer Alice?",\n    filters: { user_id: "alice" },\n  }),\n});\nif (!found.ok) throw new Error(await found.text());\nconsole.log(await found.json());`,
        },
      ],
      curl: [
        {
          label: "Step 1: Set credentials",
          code: `API_URL="${apiUrl}"\nAPI_KEY="<your-api-key>"\nPROJECT_ID="${projectId}"`,
        },
        {
          label: "Step 2: Verify the API",
          code: `curl --fail-with-body "$API_URL/v1/ping/" \\\n+  -H "X-API-Key: $API_KEY" \\\n+  -H "X-Project-ID: $PROJECT_ID"`,
        },
        {
          label: "Step 3: Add a memory",
          code: `curl --fail-with-body -X POST "$API_URL/memories" \\\n+  -H "X-API-Key: $API_KEY" \\\n+  -H "X-Project-ID: $PROJECT_ID" \\\n+  -H "Content-Type: application/json" \\\n+  -d '{"messages":[{"role":"user","content":"I prefer concise answers."}],"user_id":"alice"}'`,
        },
        {
          label: "Step 4: Retrieve memories",
          code: `curl --fail-with-body -X POST "$API_URL/search" \\\n+  -H "X-API-Key: $API_KEY" \\\n+  -H "X-Project-ID: $PROJECT_ID" \\\n+  -H "Content-Type: application/json" \\\n+  -d '{"query":"How should I answer Alice?","filters":{"user_id":"alice"}}'`,
        },
      ],
    }),
    [apiUrl, projectId],
  );

  return (
    <div className="mx-auto w-full max-w-[870px] space-y-6 pb-8">
      <h1 className="text-2xl font-semibold font-fustat">Install YiQiao</h1>

      <div className="inline-flex max-w-full rounded-md bg-surface-default-secondary p-1">
        {(["python", "node", "curl"] as const).map((language) => (
          <SegmentedButton
            key={language}
            active={sdkLanguage === language}
            onClick={() => setSdkLanguage(language)}
          >
            {language === "node"
              ? "Node"
              : language === "curl"
                ? "CURL"
                : "Python"}
          </SegmentedButton>
        ))}
      </div>

      <div className="rounded-lg border border-memBorder-primary p-4 sm:p-5">
        <div className="mb-5 flex flex-col gap-1 text-sm text-onSurface-default-secondary sm:flex-row sm:items-center sm:justify-between">
          <span>
            Project: <code className="text-xs">{projectId}</code>
          </span>
          <a
            href={`${apiUrl}/docs`}
            target="_blank"
            rel="noreferrer"
            className="inline-flex h-8 items-center gap-1.5 self-start rounded-md border border-memBorder-primary px-3 text-xs font-medium text-onSurface-default-primary hover:bg-surface-default-secondary"
          >
            API Docs <ExternalLink className="size-3.5" />
          </a>
        </div>
        <div className="space-y-4">
          {sdkSteps[sdkLanguage].map((step) => (
            <CommandStep key={step.label} {...step} />
          ))}
        </div>
      </div>
    </div>
  );
}
