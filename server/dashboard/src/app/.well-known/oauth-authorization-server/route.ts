// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { proxyPublicConnectorRequest } from "@/lib/public-connector-proxy";

export const dynamic = "force-dynamic";

export function GET(request: Request) {
  return proxyPublicConnectorRequest(
    request,
    "/.well-known/oauth-authorization-server",
    "GET",
  );
}
