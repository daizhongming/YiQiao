// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export default function ThemeAwareLogo({
  width = 120,
  height = 40,
  compact = false,
}: {
  width?: number;
  height?: number;
  compact?: boolean;
}) {
  return (
    <div
      className="flex items-center justify-center gap-2"
      style={{ width, height }}
    >
      <span
        role="img"
        aria-label="YiQiao"
        className="shrink-0 bg-contain bg-center bg-no-repeat"
        style={{
          width: height,
          height,
          backgroundImage: "url('/favicon.svg')",
        }}
      />
      {!compact && (
        <span className="yiqiao-display text-lg font-semibold text-onSurface-default-primary">
          YiQiao
        </span>
      )}
    </div>
  );
}
