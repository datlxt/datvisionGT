import type { EventResult, Job, JobStatus } from "../types";

export function formatBytes(value: number | null) {
  if (value === null) return "Chưa có dữ liệu";
  return `${(value / 1024 / 1024).toLocaleString("vi-VN", {
    maximumFractionDigits: 1,
  })} MB`;
}

export function formatTime(value: number | null) {
  if (value === null) return "Chưa có dữ liệu";
  const hours = Math.floor(value / 3_600_000);
  const minutes = Math.floor((value % 3_600_000) / 60_000);
  const seconds = Math.floor((value % 60_000) / 1000);
  const milliseconds = value % 1000;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(
    seconds,
  ).padStart(2, "0")}.${String(milliseconds).padStart(3, "0")}`;
}

export function formatDate(value: string) {
  return new Intl.DateTimeFormat("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function isReadyForReview(job: Job) {
  return job.status === "WAITING_FOR_REVIEW" || job.status === "COMPLETED";
}

export function statusLabel(status: JobStatus) {
  const labels: Record<JobStatus, string> = {
    DRAFT: "Bản nháp",
    PENDING: "Đang chờ",
    QUEUED: "Trong hàng đợi",
    PROCESSING: "Đang xử lý",
    WAITING_FOR_REVIEW: "Chờ kiểm duyệt",
    COMPLETED: "Đã hoàn thành",
    FAILED: "Thất bại",
    CANCELLED: "Đã hủy",
  };
  return labels[status];
}

export function statusTone(status: JobStatus) {
  if (status === "COMPLETED") return "success";
  if (status === "FAILED" || status === "CANCELLED") return "danger";
  if (status === "WAITING_FOR_REVIEW") return "warning";
  if (status === "PROCESSING" || status === "QUEUED" || status === "PENDING") return "info";
  return "neutral";
}

export function isSuspectedNoPlate(event: EventResult) {
  return event.quality_flags.some((flag) =>
    [
      "MOTION_ONLY_NO_PLATE_CANDIDATE",
      "INSUFFICIENT_NO_PLATE_EVIDENCE",
      "INSUFFICIENT_VEHICLE_OBSERVATIONS_FOR_NO_PLATE",
    ].includes(flag),
  );
}

export function resultLabel(event: EventResult) {
  if (event.classification === "NO_PLATE") return "XE KHÔNG BIỂN";
  if (isSuspectedNoPlate(event)) return "NGHI XE KHÔNG BIỂN";
  return event.normalized_plate ?? "KHÔNG ĐỌC ĐƯỢC";
}
