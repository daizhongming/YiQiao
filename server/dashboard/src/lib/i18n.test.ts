// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import { describe, expect, it } from "vitest";

import { translateText } from "./i18n";

describe("YiQiao SDK translations", () => {
  it("provides Chinese labels for the SDK quick start", () => {
    expect(translateText("Integrations", "zh")).toBe("集成");
    expect(translateText("Step 1: Install the SDK", "zh")).toBe(
      "第 1 步：安装 SDK",
    );
    expect(translateText("Step 2: Add and search memories", "zh")).toBe(
      "第 2 步：添加并检索记忆",
    );
    expect(translateText("API Reference", "zh")).toBe("接口文档");
  });
});
