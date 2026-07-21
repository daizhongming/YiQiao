// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";

import {
  buildAddMemoryCurl,
  buildHealthCheckCurl,
  buildSearchMemoriesCurl,
} from "./yiqiao-api-examples";

const options = {
  apiUrl: "$YIQIAO_API_URL",
  apiKey: "$YIQIAO_API_KEY",
  projectId: "$YIQIAO_PROJECT_ID",
};

describe("YiQiao curl examples", () => {
  it("uses the native health endpoint", () => {
    const command = buildHealthCheckCurl(options.apiUrl);

    expect(command).toBe('curl --fail-with-body "$YIQIAO_API_URL/api/health"');
    expect(command).not.toContain("/v1/ping/");
  });

  it.each([buildAddMemoryCurl, buildSearchMemoriesCurl])(
    "builds a project-scoped command without patch markers",
    (buildCommand) => {
      const command = buildCommand(options);

      expect(command).toContain("X-API-Key: $YIQIAO_API_KEY");
      expect(command).toContain("X-Project-ID: $YIQIAO_PROJECT_ID");
      expect(command).not.toMatch(/(?:^|\n)\+/);
      expect(command).not.toMatch(/mem0/i);
    },
  );
});
