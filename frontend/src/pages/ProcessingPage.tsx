import { useEffect, useState } from "react";

import { Icon } from "../components/Icon";
import { PageHeader, ProgressBar, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import {
  formatBytes,
  formatTime,
  isReadyForReview,
  statusLabel,
  statusTone,
} from "../lib/format";
import type { Job } from "../types";

export function ProcessingPage({
  job,
  onUpdate,
  onReview,
}: {
  job: Job;
  onUpdate: (job: Job) => void;
  onReview: () => void;
}) {
  const [retrying, setRetrying] = useState(false);
  const [retryError, setRetryError] = useState("");

  useEffect(() => {
    if (["WAITING_FOR_REVIEW", "COMPLETED", "FAILED", "CANCELLED"].includes(job.status)) {
      return;
    }
    const timer = window.setInterval(() => {
      api<Job>(`/api/v1/jobs/${job.id}`).then(onUpdate).catch(() => undefined);
    }, 2000);
    return () => window.clearInterval(timer);
  }, [job.id, job.status, onUpdate]);

  const ready = isReadyForReview(job);
  const canRetry = ["FAILED", "CANCELLED"].includes(job.status);
  const stageIndex =
    job.current_stage === "RESULTS_READY" || ready
      ? 3
      : job.progress >= 70
        ? 2
        : job.progress > 0
          ? 1
          : 0;

  async function retry() {
    setRetrying(true);
    setRetryError("");
    try {
      onUpdate(await api<Job>(`/api/v1/jobs/${job.id}/retry`, { method: "POST" }));
    } catch (reason) {
      setRetryError(reason instanceof Error ? reason.message : "Không thể chạy lại job.");
    } finally {
      setRetrying(false);
    }
  }

  return (
    <section className="page processing-page">
      <PageHeader
        action={
          <StatusBadge tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusBadge>
        }
        description={job.job_code}
        title={job.source_name}
      />

      <div className="processing-grid">
        <section className="card video-card">
          <video controls preload="metadata" src={`/api/v1/jobs/${job.id}/source`} />
          <div className="video-meta">
            <div>
              <span>File nguồn</span>
              <strong>{job.source_name}</strong>
            </div>
            <div>
              <span>Thời lượng</span>
              <strong>{formatTime(job.duration_ms)}</strong>
            </div>
            <div>
              <span>Kích thước</span>
              <strong>
                {job.width && job.height ? `${job.width} × ${job.height}` : "Chưa có dữ liệu"}
              </strong>
            </div>
            <div>
              <span>Dung lượng</span>
              <strong>{formatBytes(job.source_size_bytes)}</strong>
            </div>
          </div>
        </section>

        <aside className="card pipeline-card">
          <div className="pipeline-progress">
            <div>
              <span>Tiến độ pipeline</span>
              <strong>{Math.round(job.progress)}%</strong>
            </div>
            <ProgressBar value={job.progress} />
            <p>
              {job.processed_frames.toLocaleString("vi-VN")} /{" "}
              {(job.total_frames ?? 0).toLocaleString("vi-VN")} frame nguồn
            </p>
          </div>

          <ol className="pipeline-list">
            {[
              ["Evidence", "PTS, SHA-256 và full frame"],
              ["Detect & OCR", "Xe máy, biển số và OCR"],
              ["Tracking & vote", "Gộp nhiều frame thành một lượt xe"],
            ].map(([title, description], index) => (
              <li className={stageIndex > index ? "done" : stageIndex === index ? "active" : ""} key={title}>
                <span>{stageIndex > index ? <Icon name="check" size={16} /> : index + 1}</span>
                <div>
                  <strong>{title}</strong>
                  <small>{description}</small>
                </div>
              </li>
            ))}
          </ol>

          <dl className="pipeline-facts">
            <div>
              <dt>Stage hiện tại</dt>
              <dd>{job.current_stage ?? "Chưa có dữ liệu"}</dd>
            </div>
            <div>
              <dt>Chế độ</dt>
              <dd>{job.processing_mode}</dd>
            </div>
            <div>
              <dt>Sample rate</dt>
              <dd>{job.sample_rate} FPS</dd>
            </div>
          </dl>

          {(job.error_message || retryError) && (
            <div className="inline-alert">
              <Icon name="alert" size={19} />
              <p>{retryError || job.error_message}</p>
            </div>
          )}

          {canRetry ? (
            <button
              className="button button-primary button-block"
              disabled={retrying}
              onClick={retry}
              type="button"
            >
              <Icon name="refresh" size={18} />
              {retrying ? "Đang đưa lại vào hàng đợi…" : "Tiếp tục job bị gián đoạn"}
            </button>
          ) : (
            <button
              className="button button-primary button-block"
              disabled={!ready}
              onClick={onReview}
              type="button"
            >
              Mở không gian kiểm duyệt
              <Icon name="arrow" size={18} />
            </button>
          )}
        </aside>
      </div>
    </section>
  );
}
