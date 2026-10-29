// Small inline stroke icons (no icon font, no network).
const PATHS = {
  check: "M3.5 8.5l3 3 6-7",
  x: "M4 4l8 8M12 4l-8 8",
  alert: "M8 2.5l6 11H2zM8 6.5v3.2M8 11.6v.1",
  info: "M8 14A6 6 0 108 2a6 6 0 000 12zM8 7.2v4M8 4.9v.1",
  dot: "M8 10a2 2 0 100-4 2 2 0 000 4z",
  clock: "M8 14A6 6 0 108 2a6 6 0 000 12zM8 4.8V8l2.2 1.6",
  spinner: "M8 2a6 6 0 016 6",
  inbox: "M2 9l2-6h8l2 6v4H2zM2 9h3.5l1 1.5h3l1-1.5H14",
  overview: "M2.5 2.5h4.5v4.5H2.5zM9 2.5h4.5v4.5H9zM2.5 9h4.5v4.5H2.5zM9 9h4.5v4.5H9z",
  cube: "M8 1.8l5.5 3v6.4L8 14.2l-5.5-3V4.8zM2.5 4.8L8 7.8l5.5-3M8 7.8v6.4",
  wire: "M2 12c3 0 3-8 6-8s3 8 6 8M2 12h1M13 12h1",
  bolt: "M9 1.5L3.5 9H8l-1 5.5L12.5 7H8z",
  chip: "M4.5 4.5h7v7h-7zM6.5 2v2.5M9.5 2v2.5M6.5 11.5V14M9.5 11.5V14M2 6.5h2.5M2 9.5h2.5M11.5 6.5H14M11.5 9.5H14",
  code: "M5.5 4.5L2 8l3.5 3.5M10.5 4.5L14 8l-3.5 3.5",
  play: "M5 3.2v9.6L12.5 8z",
  pause: "M5 3.5v9M11 3.5v9",
  pulse: "M1.5 8h3l1.5-4 3 8 1.5-4h4",
  stethoscope: "M8 14A6 6 0 108 2a6 6 0 000 12zM5.2 8.5l2 2 3.6-4.5",
  sparkle: "M8 1.5l1.6 4.9L14.5 8l-4.9 1.6L8 14.5l-1.6-4.9L1.5 8l4.9-1.6z",
  rocket: "M9.5 2.5c2 0 4 0 4 0s0 2 0 4L8.5 11.5 4.5 7.5zM4.5 7.5L2 8l1 3M8.5 11.5L8 14l-3-1M3 13l1.6-1.6",
  gear: "M8 10.2A2.2 2.2 0 108 5.8a2.2 2.2 0 000 4.4zM8 1.5v2M8 12.5v2M1.5 8h2M12.5 8h2M3.4 3.4l1.4 1.4M11.2 11.2l1.4 1.4M3.4 12.6l1.4-1.4M11.2 4.8l1.4-1.4",
  folder: "M1.8 4h4.5l1.4 1.5h6.5v7.5H1.8z",
  plus: "M8 3v10M3 8h10",
  sun: "M8 11a3 3 0 100-6 3 3 0 000 6zM8 1v1.6M8 13.4V15M1 8h1.6M13.4 8H15M3 3l1.1 1.1M11.9 11.9L13 13M3 13l1.1-1.1M11.9 4.1L13 3",
  moon: "M13 9.5A5.5 5.5 0 016.5 3a5.5 5.5 0 106.5 6.5z",
  external: "M9.5 2.5h4v4M13.5 2.5L7.5 8.5M12 9.5V13.5H2.5V4H6.5",
  refresh: "M13 4.5A5.5 5.5 0 102.5 8M13 1.5v3h-3",
  download: "M8 2v8.5M4.5 7L8 10.5 11.5 7M2.5 13.5h11",
  target: "M8 14A6 6 0 108 2a6 6 0 000 12zM8 11a3 3 0 100-6 3 3 0 000 6z",
  stop: "M4 4h8v8H4z",
  menu: "M2.5 4h11M2.5 8h11M2.5 12h11",
  chevron: "M4.5 6.5L8 10l3.5-3.5",
  search: "M7 12a5 5 0 100-10 5 5 0 000 10zM10.6 10.6L14 14",
} as const;

export type IconName = keyof typeof PATHS;

export function Icon({ name, size = 16, className }: { name: IconName; size?: number; className?: string }) {
  return (
    <svg width={size} height={size} viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth={1.6}
         strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
         className={name === "spinner" ? `spin ${className ?? ""}` : className}>
      <path d={PATHS[name]} />
    </svg>
  );
}
