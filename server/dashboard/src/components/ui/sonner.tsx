// This file was modified in 2026 by YiQiao contributors. See NOTICE.

"use client";

import { useTheme } from "next-themes";
import { Toaster as Sonner } from "sonner";

type ToasterProps = React.ComponentProps<typeof Sonner>;

const Toaster = ({ ...props }: ToasterProps) => {
  const { theme = "system" } = useTheme();

  return (
    <Sonner
      theme={theme as ToasterProps["theme"]}
      className="toaster group"
      position="bottom-right"
      closeButton
      style={{ zIndex: 10000 }}
      toastOptions={{
        classNames: {
          toast:
            "group toast group-[.toaster]:pointer-events-auto group-[.toaster]:max-w-[calc(100vw-2rem)] group-[.toaster]:rounded-lg group-[.toaster]:border-memBorder-primary group-[.toaster]:bg-surface-default-primary group-[.toaster]:text-onSurface-default-primary group-[.toaster]:shadow-lg",
          title: "group-[.toast]:text-sm group-[.toast]:font-semibold",
          description:
            "group-[.toast]:text-muted-foreground group-[.toast]:text-sm",
          icon: "hidden",
          success:
            "group-[.toaster]:border-l-4 group-[.toaster]:border-l-[var(--yiqiao-semantic-success)]",
          error:
            "group-[.toaster]:border-l-4 group-[.toaster]:border-l-[var(--yiqiao-semantic-error)]",
          actionButton:
            "group-[.toast]:rounded-md group-[.toast]:bg-primary group-[.toast]:text-primary-foreground group-[.toast]:focus-visible:outline-none group-[.toast]:focus-visible:ring-2 group-[.toast]:focus-visible:ring-ring",
          cancelButton:
            "group-[.toast]:rounded-md group-[.toast]:bg-muted group-[.toast]:text-muted-foreground group-[.toast]:focus-visible:outline-none group-[.toast]:focus-visible:ring-2 group-[.toast]:focus-visible:ring-ring",
          closeButton:
            "group-[.toast]:rounded-md group-[.toast]:border-memBorder-primary group-[.toast]:bg-surface-default-primary group-[.toast]:text-onSurface-default-secondary group-[.toast]:focus-visible:outline-none group-[.toast]:focus-visible:ring-2 group-[.toast]:focus-visible:ring-ring",
        },
      }}
      {...props}
    />
  );
};

export { Toaster };
