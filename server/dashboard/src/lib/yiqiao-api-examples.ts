// This file was modified in 2026 by YiQiao contributors. See NOTICE.

interface CurlRequestOptions {
  apiUrl: string;
  apiKey: string;
  projectId: string;
}

export function buildHealthCheckCurl(apiUrl: string): string {
  return `curl --fail-with-body "${apiUrl}/api/health"`;
}

export function buildAddMemoryCurl({
  apiUrl,
  apiKey,
  projectId,
}: CurlRequestOptions): string {
  return [
    `curl --fail-with-body -X POST "${apiUrl}/memories" \\`,
    `  -H "X-API-Key: ${apiKey}" \\`,
    `  -H "X-Project-ID: ${projectId}" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '${JSON.stringify({
      messages: [{ role: "user", content: "I prefer concise answers." }],
      user_id: "alice",
    })}'`,
  ].join("\n");
}

export function buildSearchMemoriesCurl({
  apiUrl,
  apiKey,
  projectId,
}: CurlRequestOptions): string {
  return [
    `curl --fail-with-body -X POST "${apiUrl}/search" \\`,
    `  -H "X-API-Key: ${apiKey}" \\`,
    `  -H "X-Project-ID: ${projectId}" \\`,
    '  -H "Content-Type: application/json" \\',
    `  -d '${JSON.stringify({
      query: "How should I answer Alice?",
      filters: { user_id: "alice" },
    })}'`,
  ].join("\n");
}
