// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { proxyPublicConnectorRequest } from "@/lib/public-connector-proxy";

export const dynamic = "force-dynamic";

export function POST(request: Request) {
  return proxyPublicConnectorRequest(request, "/search", "POST");
}
