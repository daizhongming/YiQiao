// This file was modified in 2026 by YiQiao contributors. See NOTICE.

export default function ThemeAwareLogo({
  width = 120,
  height = 40,
}: {
  width?: number;
  height?: number;
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
      <span className="text-lg font-semibold text-onSurface-default-primary">
        YiQiao
      </span>
    </div>
  );
}
