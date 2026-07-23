// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import Image from "next/image";
import { useTheme } from "next-themes";

interface EmptyStateProps {
  title: string;
  description?: string;
  image?: "memories" | "requests";
  children?: React.ReactNode;
}

export function EmptyState({
  title,
  description,
  image = "memories",
  children,
}: EmptyStateProps) {
  const { resolvedTheme } = useTheme();
  const src =
    resolvedTheme === "dark"
      ? `/images/no-${image}-dark.svg`
      : `/images/no-${image}.svg`;

  return (
    <div className="flex min-h-64 flex-col items-center justify-center px-4 py-12 text-center sm:py-16">
      <Image
        src={src}
        alt=""
        width={120}
        height={72}
        priority
        className="mb-4 opacity-80"
      />
      <p className="text-sm font-semibold text-onSurface-default-primary">
        {title}
      </p>
      {description && (
        <p className="mt-1 max-w-sm text-xs leading-relaxed text-onSurface-default-tertiary">
          {description}
        </p>
      )}
      {children && (
        <div className="mt-5 flex flex-wrap items-center justify-center gap-2">
          {children}
        </div>
      )}
    </div>
  );
}
