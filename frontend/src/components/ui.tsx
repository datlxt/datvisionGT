import type { ReactNode } from "react";

import { Icon } from "./Icon";

export function StatusBadge({
  children,
  tone = "neutral",
}: {
  children: ReactNode;
  tone?: "neutral" | "info" | "success" | "warning" | "danger" | "duplicate";
}) {
  return <span className={`status-badge status-${tone}`}>{children}</span>;
}

export function ProgressBar({ value }: { value: number }) {
  const safeValue = Math.max(0, Math.min(100, value));
  return (
    <div
      aria-label={`Tiến độ ${Math.round(safeValue)}%`}
      aria-valuemax={100}
      aria-valuemin={0}
      aria-valuenow={safeValue}
      className="progress-bar"
      role="progressbar"
    >
      <span style={{ width: `${safeValue}%` }} />
    </div>
  );
}

export function LoadingState({ label = "Đang tải dữ liệu…" }: { label?: string }) {
  return (
    <div className="state-box" role="status">
      <span className="spinner" />
      <strong>{label}</strong>
    </div>
  );
}

export function ErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry?: () => void;
}) {
  return (
    <div className="state-box state-error" role="alert">
      <Icon name="alert" size={25} />
      <strong>Không thể tải dữ liệu</strong>
      <p>{message}</p>
      {onRetry && (
        <button className="button button-secondary" onClick={onRetry} type="button">
          <Icon name="refresh" size={17} /> Thử lại
        </button>
      )}
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-box">
      <span className="empty-icon">
        <Icon name="file" size={25} />
      </span>
      <strong>{title}</strong>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow?: string;
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        {eyebrow && <p className="eyebrow">{eyebrow}</p>}
        <h1>{title}</h1>
        {description && <p className="page-description">{description}</p>}
      </div>
      {action}
    </header>
  );
}
