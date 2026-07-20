"use client";

import { Languages } from "lucide-react";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";

export function LanguageToggle({ compact = false }: { compact?: boolean }) {
  const { language, setLanguage, t } = useI18n();
  const nextLanguage = language === "zh" ? "en" : "zh";
  const actionLabel = t(
    language === "zh" ? "Switch to English" : "Switch to Chinese",
  );

  return (
    <Button
      type="button"
      variant="outline"
      size={compact ? "sm" : "default"}
      onClick={() => setLanguage(nextLanguage)}
      className={compact ? "h-8 gap-1.5 px-2" : "gap-2"}
      aria-label={actionLabel}
      title={actionLabel}
    >
      <Languages className="size-4" />
      <span className="text-xs">{language === "zh" ? "EN" : "中文"}</span>
    </Button>
  );
}
