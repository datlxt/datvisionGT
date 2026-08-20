type IconName =
  | "grid"
  | "plus"
  | "shield"
  | "download"
  | "search"
  | "bell"
  | "upload"
  | "video"
  | "images"
  | "plate"
  | "face"
  | "layers"
  | "camera"
  | "scan"
  | "check"
  | "clock"
  | "file"
  | "chevron"
  | "arrow"
  | "database"
  | "alert"
  | "eye"
  | "refresh"
  | "trash"
  | "scissors"
  | "motorcycle"
  | "car"
  | "star"
  | "x";

export function Icon({
  name,
  size = 20,
  strokeWidth = 1.8,
}: {
  name: IconName;
  size?: number;
  strokeWidth?: number;
}) {
  const common = {
    fill: "none",
    stroke: "currentColor",
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    strokeWidth,
  };

  const paths: Record<IconName, React.ReactNode> = {
    grid: (
      <>
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
      </>
    ),
    plus: (
      <>
        <path d="M12 5v14" />
        <path d="M5 12h14" />
      </>
    ),
    shield: (
      <>
        <path d="M12 3 4.5 6v5.5c0 4.8 3 8 7.5 9.5 4.5-1.5 7.5-4.7 7.5-9.5V6L12 3Z" />
        <path d="m9 12 2 2 4-4" />
      </>
    ),
    download: (
      <>
        <path d="M12 3v12" />
        <path d="m7.5 10.5 4.5 4.5 4.5-4.5" />
        <path d="M4 20h16" />
      </>
    ),
    search: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </>
    ),
    bell: (
      <>
        <path d="M18 8a6 6 0 0 0-12 0c0 7-3 7-3 9h18c0-2-3-2-3-9" />
        <path d="M10 21h4" />
      </>
    ),
    upload: (
      <>
        <path d="M12 16V4" />
        <path d="m7.5 8.5 4.5-4.5 4.5 4.5" />
        <path d="M4 15v5h16v-5" />
      </>
    ),
    video: (
      <>
        <rect x="3" y="4" width="14" height="16" rx="2" />
        <path d="m17 9 4-2v10l-4-2" />
        <path d="M7 4v16M13 4v16" />
      </>
    ),
    images: (
      <>
        <rect x="5" y="3" width="16" height="14" rx="2" />
        <path d="m5 14 4-4 3 3 3-3 6 6" />
        <path d="M3 7v12a2 2 0 0 0 2 2h12" />
      </>
    ),
    plate: (
      <>
        <rect x="3" y="6" width="18" height="12" rx="3" />
        <path d="M7 10h10M7 14h6" />
      </>
    ),
    face: (
      <>
        <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
        <circle cx="12" cy="11" r="4" />
      </>
    ),
    layers: (
      <>
        <path d="m12 3 9 5-9 5-9-5 9-5Z" />
        <path d="m3 12 9 5 9-5M3 16l9 5 9-5" />
      </>
    ),
    camera: (
      <>
        <path d="M4 8h3l2-3h6l2 3h3a2 2 0 0 1 2 2v8H2v-8a2 2 0 0 1 2-2Z" />
        <circle cx="12" cy="13" r="4" />
      </>
    ),
    scan: (
      <>
        <path d="M8 3H5a2 2 0 0 0-2 2v3M16 3h3a2 2 0 0 1 2 2v3M8 21H5a2 2 0 0 1-2-2v-3M16 21h3a2 2 0 0 0 2-2v-3" />
        <path d="M7 12h10" />
      </>
    ),
    check: <path d="m5 12 4 4L19 6" />,
    clock: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 7v6l4 2" />
      </>
    ),
    file: (
      <>
        <path d="M6 2h8l4 4v16H6V2Z" />
        <path d="M14 2v5h5M9 12h6M9 16h6" />
      </>
    ),
    chevron: <path d="m8 10 4 4 4-4" />,
    arrow: (
      <>
        <path d="M5 12h14" />
        <path d="m14 7 5 5-5 5" />
      </>
    ),
    database: (
      <>
        <ellipse cx="12" cy="5" rx="8" ry="3" />
        <path d="M4 5v7c0 1.7 3.6 3 8 3s8-1.3 8-3V5M4 12v7c0 1.7 3.6 3 8 3s8-1.3 8-3v-7" />
      </>
    ),
    alert: (
      <>
        <path d="M12 3 2.5 20h19L12 3Z" />
        <path d="M12 9v5M12 17h.01" />
      </>
    ),
    eye: (
      <>
        <path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12Z" />
        <circle cx="12" cy="12" r="2.5" />
      </>
    ),
    refresh: (
      <>
        <path d="M20 7v5h-5" />
        <path d="M18.5 16a8 8 0 1 1 .5-8l1 4" />
      </>
    ),
    x: (
      <>
        <path d="M6 6l12 12M18 6 6 18" />
      </>
    ),
    trash: (
      <>
        <path d="M4 7h16" />
        <path d="M9 7V4h6v3" />
        <path d="M6 7l1 13h10l1-13" />
        <path d="M10 11v6M14 11v6" />
      </>
    ),
    scissors: (
      <>
        <circle cx="6" cy="6" r="3" />
        <circle cx="6" cy="18" r="3" />
        <path d="M20 4 8.12 15.88" />
        <path d="M14.47 14.48 20 20" />
        <path d="M8.12 8.12 12 12" />
      </>
    ),
    motorcycle: (
      <>
        {/* rear + front wheels */}
        <circle cx="5.5" cy="16" r="3.2" />
        <circle cx="18.5" cy="16" r="3.2" />
        {/* seat + fuel tank profile: rear hub up over the top to the headstock */}
        <path d="M5.5 16 7.3 11H12.5l1.5-3" />
        {/* engine / lower frame down to the front */}
        <path d="M8 11 10.3 14h4.7" />
        {/* lower fork to front hub */}
        <path d="M15 14 18.5 16" />
        {/* handlebar grip */}
        <path d="M13.2 7.6 15.2 7H17.3" />
        {/* front fork from headstock to front wheel */}
        <path d="M15 7.4 18.5 16" />
      </>
    ),
    car: (
      <>
        <path d="M5 11l1.6-3.4A2 2 0 0 1 8.4 6.5h7.2a2 2 0 0 1 1.8 1.1L19 11" />
        <path d="M4 11h16a1 1 0 0 1 1 1v4a1 1 0 0 1-1 1h-1M6 17H4a1 1 0 0 1-1-1v-4" />
        <path d="M8 17h8" />
        <circle cx="7" cy="17" r="1.8" />
        <circle cx="17" cy="17" r="1.8" />
      </>
    ),
    star: (
      <path d="M12 3.5l2.6 5.27 5.82.85-4.21 4.1.99 5.79L12 16.77l-5.2 2.74.99-5.79-4.21-4.1 5.82-.85L12 3.5Z" />
    ),
  };

  return (
    <svg
      aria-hidden="true"
      height={size}
      viewBox="0 0 24 24"
      width={size}
      {...common}
    >
      {paths[name]}
    </svg>
  );
}
