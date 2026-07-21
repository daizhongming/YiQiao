// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";

import { translateText } from "./i18n";

describe("YiQiao integration translations", () => {
  it("provides Chinese labels for platform-specific setup steps", () => {
    expect(translateText("Step 1: Verify Node.js", "zh")).toBe(
      "第 1 步：验证 Node.js",
    );
    expect(translateText("Step 2: Check YiQiao health", "zh")).toBe(
      "第 2 步：检查 YiQiao 运行状态",
    );
  });
});
