// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Lock } from "lucide-react";

interface LockedPageProps {
  title: string;
  description: string;
  previewContent: React.ReactNode;
  utmMedium: string;
}

export function LockedPage({
  title,
  description,
  previewContent,
  utmMedium,
}: LockedPageProps) {
  const utm = `utm_source=oss&utm_medium=${utmMedium}`;
  const dashboardUrl = `/dashboard?${utm}`;
  const settingsUrl = `/dashboard/settings?${utm}`;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold font-fustat flex items-center gap-2">
          {title}
          <Lock className="size-4 text-onSurface-default-tertiary" />
        </h1>
        <p className="text-sm text-onSurface-default-secondary mt-1">
          {description}
        </p>
      </div>

      <div className="opacity-60 pointer-events-none select-none">
        {previewContent}
      </div>

      <Card className="border-memBorder-primary">
        <CardContent className="flex flex-col sm:flex-row items-center gap-4 py-6">
          <div className="flex-1">
            <p className="text-sm font-medium">
              This YiQiao feature is available in this self-hosted dashboard.
            </p>
          </div>
          <div className="flex gap-3">
            <Button variant="default" asChild>
              <a href={dashboardUrl}>Open dashboard</a>
            </Button>
            <Button variant="outline" asChild>
              <a href={settingsUrl}>Settings</a>
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}
