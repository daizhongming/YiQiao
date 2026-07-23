// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export const fieldLabelClassName =
  "font-fustat font-semibold text-xs leading-4 tracking-normal text-onSurface-default-tertiary";

export const inputVariants = {
  default:
    "flex h-9 w-full rounded-md border border-memBorder-primary bg-surface-default-primary px-3 py-2 text-sm text-onSurface-default-primary transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:border-onSurface-danger-primary aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
  textField:
    "peer flex h-[38px] w-full min-w-0 rounded-lg border border-memBorder-primary bg-surface-default-primary px-3 py-2.5 font-fustat text-sm text-onSurface-default-primary transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:border-onSurface-danger-primary aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
  nestedInput:
    "peer flex h-[38px] w-full min-w-0 rounded-lg border-0 bg-transparent px-0 py-2.5 font-fustat text-sm text-onSurface-default-primary transition-colors file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
} as const;

export const selectTriggerVariants = {
  default:
    "relative flex h-10 w-full items-center justify-between rounded-md border border-memBorder-primary bg-surface-default-primary px-3 py-2 pr-9 text-sm text-onSurface-default-primary transition-colors data-[placeholder]:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:border-onSurface-danger-primary aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
  dropdown:
    "relative flex h-[38px] w-full min-w-0 items-center justify-between rounded-lg border border-memBorder-primary bg-surface-default-primary py-2.5 pl-3 pr-10 font-fustat text-sm text-onSurface-default-primary transition-colors data-[placeholder]:text-onSurface-default-tertiary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-1 focus-visible:ring-offset-background aria-[invalid=true]:border-onSurface-danger-primary aria-[invalid=true]:ring-onSurface-danger-primary disabled:cursor-not-allowed disabled:opacity-50 motion-reduce:transition-none",
} as const;
