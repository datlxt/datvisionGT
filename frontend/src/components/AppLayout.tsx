import type { ReactNode } from "react";

import type { Health, Job, View } from "../types";
import { isReadyForReview } from "../lib/format";
import { Icon } from "./Icon";

const viewLabels: Record<View, string> = {
  overview: "Tổng quan",
  create: "Tạo phiên xử lý",
  processing: "Đang xử lý",
  review: "Kiểm duyệt GT",
  exports: "Kết quả & Xuất GT",
  condense: "Cắt video",
};

function Brand() {
  return (
    <div className="brand" aria-label="DatVision GT">
      <img
        alt="DatVision GT"
        className="brand-logo brand-logo-full"
        src="/brand/datvisiongt-wordmark-original.png?v=4"
      />
      <img
        alt=""
        aria-hidden="true"
        className="brand-logo brand-logo-compact"
        src="/brand/datvisiongt-symbol-original.png?v=4"
      />
    </div>
  );
}

type NavItem = {
  view: Exclude<View, "processing">;
  label: string;
  icon: "grid" | "plus" | "shield" | "download" | "scissors";
};

const navigation: NavItem[] = [
  { view: "overview", label: "Tổng quan", icon: "grid" },
  { view: "create", label: "Tạo phiên xử lý", icon: "plus" },
  { view: "condense", label: "Cắt video", icon: "scissors" },
  { view: "review", label: "Kiểm duyệt GT", icon: "shield" },
  { view: "exports", label: "Kết quả & Xuất GT", icon: "download" },
];

export function AppLayout({
  view,
  job,
  health,
  onNavigate,
  children,
}: {
  view: View;
  job: Job | null;
  health: Health | null;
  onNavigate: (view: Exclude<View, "processing">) => void;
  children: ReactNode;
}) {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <Brand />
        <nav aria-label="Điều hướng chính">
          {navigation.map((item) => {
            const disabled = item.view === "review" && (!job || !isReadyForReview(job));
            return (
              <button
                className={view === item.view ? "active" : ""}
                disabled={disabled}
                key={item.view}
                onClick={() => onNavigate(item.view)}
                type="button"
              >
                <Icon name={item.icon} size={21} />
                <span>{item.label}</span>
              </button>
            );
          })}
        </nav>
        <div className="sidebar-status">
          <span className={`service-dot ${health?.status === "ok" ? "online" : ""}`} />
          <div>
            <strong>{health?.status === "ok" ? "Hệ thống sẵn sàng" : "Đang kiểm tra hệ thống"}</strong>
          </div>
        </div>
      </aside>

      <div className="workspace">
        <header className="topbar">
          <div className="topbar-breadcrumb">
            <span>DatVision GT</span>
            <b>/</b>
            <strong>{viewLabels[view]}</strong>
          </div>
        </header>
        <main>{children}</main>
      </div>
    </div>
  );
}
