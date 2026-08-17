import { useEffect, useRef, useState } from "react";

import { Icon } from "../components/Icon";
import { ConfirmDialog, PageHeader, ProgressBar, StatusBadge } from "../components/ui";
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
  const [cancelling, setCancelling] = useState(false);
  const [showCancel, setShowCancel] = useState(false);

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
  // Actively working: draft/queued/processing — the only states a cancel makes sense in.
  const running = !["WAITING_FOR_REVIEW", "COMPLETED", "FAILED", "CANCELLED"].includes(job.status);

  const stageIndex =
    job.current_stage === "RESULTS_READY" || ready
      ? 4
      : job.current_stage === "CROSS_CHECKING"
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

  async function cancel() {
    setCancelling(true);
    setRetryError("");
    try {
      onUpdate(await api<Job>(`/api/v1/jobs/${job.id}/cancel`, { method: "POST" }));
      setShowCancel(false);
    } catch (reason) {
      setRetryError(reason instanceof Error ? reason.message : "Không thể hủy job.");
    } finally {
      setCancelling(false);
    }
  }

  return (
    <section className="page processing-page">
      <PageHeader
        action={
          <StatusBadge tone={statusTone(job.status)}>{statusLabel(job.status)}</StatusBadge>
        }
        description={`${job.vehicle_type === "car" ? "Ô tô" : "Xe máy"} · ${new Date(
          job.created_at,
        ).toLocaleDateString("vi-VN", {
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })}`}
        title={job.source_name}
      />

      <div className="processing-grid card">
        <section className="video-card">
          {running ? (
            <LiveView
              jobId={job.id}
              processed={job.processed_frames}
              progress={job.progress}
              total={job.total_frames ?? 0}
            />
          ) : (
            <video controls preload="metadata" src={`/api/v1/jobs/${job.id}/source`} />
          )}
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

        <aside className="pipeline-card">
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
              ["Detect & OCR", "Xe, biển số và OCR"],
              ["Tracking & vote", "Gộp nhiều frame thành một lượt xe"],
              ["Đối chiếu AI", "2 model AI đọc lại + tự duyệt case khớp"],
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
          ) : running ? (
            <button
              className="button button-danger button-block"
              disabled={cancelling}
              onClick={() => setShowCancel(true)}
              type="button"
            >
              <Icon name="x" size={18} />
              {cancelling ? "Đang hủy…" : "Hủy job"}
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

      <ConfirmDialog
        busy={cancelling}
        cancelLabel="Không, tiếp tục chạy"
        confirmLabel="Hủy job"
        description={
          <>
            Dừng xử lý <strong>{job.source_name}</strong>? Tiến độ hiện tại (
            {Math.round(job.progress)}%) sẽ dừng lại. Bạn có thể chạy lại sau bằng nút &quot;Tiếp
            tục&quot;.
          </>
        }
        onCancel={() => setShowCancel(false)}
        onConfirm={cancel}
        open={showCancel}
        title="Hủy job đang chạy?"
      />
    </section>
  );
}

type LiveBox = { k: "v" | "p"; x1: number; y1: number; x2: number; y2: number; t?: string };
type LiveFrame = {
  img: string;
  w: number;
  h: number;
  boxes: LiveBox[];
  progress: number;
  roi?: number[];
};
// Live processing preview: subscribes to the worker's SSE snapshot stream and draws detection boxes
// on a canvas over the streamed frame. Smooth because only a tiny (480px) image + box COORDINATES
// travel — the browser does the drawing, nothing is re-rendered on the server.
function LiveView({
  jobId,
  progress,
  processed,
  total,
}: {
  jobId: string;
  progress: number;
  processed: number;
  total: number;
}) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [frame, setFrame] = useState<LiveFrame | null>(null);

  useEffect(() => {
    const source = new EventSource(`/api/v1/jobs/${jobId}/live`);
    source.onmessage = (event) => {
      try {
        setFrame(JSON.parse(event.data) as LiveFrame);
      } catch {
        /* ignore a malformed frame */
      }
    };
    source.addEventListener("done", () => source.close());
    return () => source.close();
  }, [jobId]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !frame) return;
    canvas.width = frame.w;
    canvas.height = frame.h;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    ctx.clearRect(0, 0, frame.w, frame.h);
    // Draw the ROI outline first (dashed amber) so detection boxes sit on top of it.
    if (frame.roi && frame.roi.length === 4) {
      ctx.setLineDash([6, 4]);
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#2482ef"; // blue — matches the ROI drawn on the create screen (--blue)
      ctx.strokeRect(
        frame.roi[0],
        frame.roi[1],
        frame.roi[2] - frame.roi[0],
        frame.roi[3] - frame.roi[1],
      );
      ctx.setLineDash([]);
    }
    for (const box of frame.boxes ?? []) {
      const isPlate = box.k === "p";
      ctx.lineWidth = 2;
      ctx.strokeStyle = isPlate ? "#22c55e" : "#3b82f6";
      ctx.strokeRect(box.x1, box.y1, box.x2 - box.x1, box.y2 - box.y1);
      if (isPlate && box.t) {
        ctx.font = "bold 12px sans-serif";
        const width = ctx.measureText(box.t).width + 6;
        ctx.fillStyle = "#22c55e";
        ctx.fillRect(box.x1, Math.max(0, box.y1 - 15), width, 15);
        ctx.fillStyle = "#fff";
        ctx.fillText(box.t, box.x1 + 3, Math.max(11, box.y1 - 4));
      }
    }
  }, [frame]);

  return (
    <div className="live-view">
      <div className="live-stage">
        {frame ? (
          <>
            <img alt="Xem trực tiếp" src={`data:image/jpeg;base64,${frame.img}`} />
            <canvas className="live-canvas" ref={canvasRef} />
          </>
        ) : (
          <div className="live-waiting">
            <Icon name="clock" size={18} />
            {progress > 0 && progress < 22 ? (
              <span>
                Đang trích frame… {processed.toLocaleString("vi-VN")}
                {total > 0 ? ` / ${total.toLocaleString("vi-VN")}` : ""} ({Math.round(progress)}%)
              </span>
            ) : (
              <span>Đang khởi động detect… khung bao sẽ hiện ngay.</span>
            )}
          </div>
        )}
        <span className="live-badge">
          ● LIVE{frame ? ` · ${Math.round(frame.progress)}%` : ""}
        </span>
      </div>
    </div>
  );
}
