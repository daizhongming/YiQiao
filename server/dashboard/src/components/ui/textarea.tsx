// This file was modified in 2026 by YiQiao contributors. See NOTICE.

import * as React from "react";

import { cn } from "@/lib/utils";
import { Label } from "@/components/ui/label";
import { fieldLabelClassName } from "@/constants/ui-components";
import { TextareaProps } from "@/types/ui-components";

const Textarea = React.forwardRef<HTMLTextAreaElement, TextareaProps>(
  (
    {
      label,
      containerClassName,
      textareaClassName,
      labelClassName,
      id: idProp,
      className,
      ...props
    },
    ref,
  ) => {
    const generatedId = React.useId();
    const id = idProp ?? generatedId;

    const textarea = (
      <textarea
        id={id}
        className={cn(
          "flex h-[129px] w-full min-w-0 resize-none rounded-lg border border-memBorder-primary bg-surface-default-primary px-3 py-2.5 font-fustat text-sm text-onSurface-default-primary transition-colors placeholder:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:border-onSurface-danger-primary aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
          textareaClassName,
          className,
        )}
        ref={ref}
        {...props}
      />
    );

    if (label) {
      return (
        <div className={cn("flex flex-col gap-1.5", containerClassName)}>
          <Label
            htmlFor={id}
            className={cn(fieldLabelClassName, labelClassName)}
          >
            {label}
          </Label>
          {textarea}
        </div>
      );
    }

    return textarea;
  },
);
Textarea.displayName = "Textarea";

export { Textarea };
