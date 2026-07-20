export type Language = "en" | "zh";

export const LANGUAGE_PREFERENCE_KEY = "yiqiao_language";

export function normalizeLanguage(value: string | null | undefined): Language {
  return value === "en" ? "en" : "zh";
}
