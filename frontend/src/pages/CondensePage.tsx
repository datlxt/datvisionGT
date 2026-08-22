import {
  type ChangeEvent,
  type DragEvent,
  type PointerEvent,
  useEffect,
  useRef,
  useState,
} from "react";

import { CondensedList } from "../components/CondensedList";
import { Icon } from "../components/Icon";
import { PageHeader, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { formatBytes, formatTime } from "../lib/format";
import type { CondenseStatus } from "../types";

const acceptedExtensions = [".mp4", ".mov", ".avi", ".mkv", ".m4v"];
const terminal = ["completed", "empty", "failed"];

const stageLabels: Record<string, string> = {
  queued: "Đang chờ trong hàng đợi…",
  scanning: "Đang quét tìm xe trong video…",
  rendering: "Đang cắt và ghép video…",
};

export function CondensePage({
  onOpenInCreate,
}: {
  onOpenInCreate: (item: CondenseStatus) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [minGap, setMinGap] = useState(15);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [status, setStatus] = useState<CondenseStatus | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const [listReload, setListReload] = useState(0);
  // Optional lane box (normalized 0-1) so the cut ignores activity in adjacent lanes.
  const roiVideoRef = useRef<HTMLVideoElement>(null);
  const roiDragStart = useRef<{ x: number; y: number } | null>(null);
  const [roi, setRoi] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  useEffect(() => {
    if (!status || terminal.includes(status.status)) return;
    const timer = setInterval(async () => {
      try {
        setStatus(await api<CondenseStatus>(`/api/v1/condense/${status.id}`));
      } catch {
        /* keep the last known status; next tick retries */
      }
    }, 1500);
    return () => clearInterval(timer);
  }, [status]);

  // Refresh the "video đã cắt" list the moment a cut finishes so it appears without a page reload.
  useEffect(() => {
    if (status?.status === "completed") setListReload((value) => value + 1);
  }, [status?.status]);

  function setSelectedFile(selected: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(selected);
    setPreview(selected ? URL.createObjectURL(selected) : null);
    setStatus(null);
    setMessage(null);
    setRoi(null);
  }

  function roiPoint(event: PointerEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width)),
      y: Math.min(1, Math.max(0, (event.clientY - rect.top) / rect.height)),
    };
  }

  function roiDown(event: PointerEvent<HTMLDivElement>) {
    const point = roiPoint(event);
    roiDragStart.current = point;
    setRoi({ x1: point.x, y1: point.y, x2: point.x, y2: point.y });
  }

  function roiMove(event: PointerEvent<HTMLDivElement>) {
    if (!roiDragStart.current) return;
    const point = roiPoint(event);
    const start = roiDragStart.current;
    setRoi({
      x1: Math.min(start.x, point.x),
      y1: Math.min(start.y, point.y),
      x2: Math.max(start.x, point.x),
      y2: Math.max(start.y, point.y),
    });
  }

  function roiUp() {
    roiDragStart.current = null;
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    setSelectedFile(event.target.files?.[0] ?? null);
  }

  function dropFile(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setDragging(false);
    const selected = event.dataTransfer.files[0] ?? null;
    if (!selected) return;
    const extension = `.${selected.name.split(".").pop()?.toLowerCase() ?? ""}`;
    if (!acceptedExtensions.includes(extension)) {
      setMessage({ tone: "error", text: "Định dạng video này chưa được hỗ trợ." });
      return;
    }
    setSelectedFile(selected);
  }

  async function startCondense() {
    if (!file || busy) return;
    setBusy(true);
    setMessage(null);
    setStatus(null);
    try {
      const roiParam = roi
        ? `&roi=${roi.x1.toFixed(4)},${roi.y1.toFixed(4)},${roi.x2.toFixed(4)},${roi.y2.toFixed(4)}`
        : "";
      const created = await api<CondenseStatus>(
        `/api/v1/condense?min_gap_seconds=${minGap}${roiParam}`,
        {
          method: "POST",
          body: file,
          headers: {
            "Content-Type": file.type || "application/octet-stream",
            "X-Filename": encodeURIComponent(file.name),
          },
        },
      );
      setStatus(created);
    } catch (reason) {
      setMessage({
        tone: "error",
        text: reason instanceof Error ? reason.message : "Không thể bắt đầu cắt video.",
      });
    } finally {
      setBusy(false);
    }
  }

  async function cancelCut() {
    if (!status || cancelling) return;
    setCancelling(true);
    // DELETE doubles as cancel: it stops the running worker job and removes the partial files.
    try {
      await fetch(`/api/v1/condense/${status.id}`, { method: "DELETE" });
    } catch {
      /* ignore — reset the UI regardless so the user isn't stuck */
    }
    setStatus(null);
    setCancelling(false);
    setMessage({ tone: "success", text: "Đã huỷ cắt video. Bạn có thể chỉnh và cắt lại." });
  }

  const processing = Boolean(status) && !terminal.includes(status!.status);
  const done = status?.status === "completed";
  const progressPercent = Math.round((status?.progress ?? 0) * 100);

  return (
    <section className="page condense-page">
      <PageHeader
        description="Loại bỏ các khoảng thời gian không có xe, dồn các lượt xe thành một video ngắn để đưa vào tạo GT."
        title="Cắt video – bỏ thời gian chết"
      />

      <div className="card condense-setup">
        {/* Step 1 — choose the video (full width, so neither column starts out empty). */}
        <header className="section-heading">
          <span>1</span>
          <div>
            <h2>Chọn video gốc</h2>
            <p>Video chưa cắt, còn nhiều khoảng trống không có xe.</p>
          </div>
        </header>

        {!file ? (
          <div
            className={`upload-dropzone ${dragging ? "dragging" : ""}`}
            onClick={() => inputRef.current?.click()}
            onDragEnter={(event) => {
              event.preventDefault();
              setDragging(true);
            }}
            onDragLeave={() => setDragging(false)}
            onDragOver={(event) => event.preventDefault()}
            onDrop={dropFile}
            role="button"
            tabIndex={0}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") inputRef.current?.click();
            }}
          >
            <input accept="video/*,.mkv,.m4v" onChange={chooseFile} ref={inputRef} type="file" />
            <span className="upload-symbol">
              <Icon name="upload" size={22} />
            </span>
            <strong>Kéo thả video vào đây hoặc nhấn để chọn</strong>
            <p>MP4, AVI, MOV, MKV, M4V · Tối đa 2 GB</p>
          </div>
        ) : (
          <div className="selected-file">
            {preview && <video muted preload="metadata" src={preview} />}
            <div className="selected-file-copy">
              <strong title={file.name}>{file.name}</strong>
              <div>
                <span>
                  <Icon name="file" size={15} /> {formatBytes(file.size)}
                </span>
              </div>
              <StatusBadge tone="info">Sẵn sàng cắt</StatusBadge>
            </div>
            <button
              className="button button-secondary button-compact selected-file-swap"
              disabled={busy || processing}
              onClick={(event) => {
                event.stopPropagation();
                inputRef.current?.click();
              }}
              type="button"
            >
              Đổi tệp khác
            </button>
            <input accept="video/*,.mkv,.m4v" onChange={chooseFile} ref={inputRef} type="file" />
          </div>
        )}

        {/* Step 2 — only once a video is picked: lane ROI (left) + cut level & action (right). */}
        {file && (
          <div className="condense-config">
            <section className="condense-roi-col">
              <div className="condense-roi-head">
                <strong>Khoanh làn (tùy chọn)</strong>
                <p>Kéo chuột để khoanh làn cần giữ; hệ thống sẽ bỏ qua các làn khác.</p>
              </div>
              <div
                className="roi-stage"
                onPointerDown={roiDown}
                onPointerLeave={roiUp}
                onPointerMove={roiMove}
                onPointerUp={roiUp}
              >
                {preview && (
                  <video muted playsInline preload="metadata" ref={roiVideoRef} src={preview} />
                )}
                {roi && (
                  <div
                    className="roi-rect"
                    style={{
                      left: `${roi.x1 * 100}%`,
                      top: `${roi.y1 * 100}%`,
                      width: `${(roi.x2 - roi.x1) * 100}%`,
                      height: `${(roi.y2 - roi.y1) * 100}%`,
                    }}
                  />
                )}
                <span className="roi-hint">Kéo chuột để khoanh làn</span>
              </div>
              <div className="condense-roi-foot">
                {roi ? (
                  <button
                    className="button button-secondary button-compact"
                    onClick={() => setRoi(null)}
                    type="button"
                  >
                    Xoá vùng
                  </button>
                ) : (
                  <em>Chưa khoanh vùng — sẽ quét toàn khung hình</em>
                )}
              </div>
            </section>

            <section className="condense-cut-col">
              <header className="section-heading">
                <span>2</span>
                <div>
                  <h2>Mức cắt</h2>
                  <p>Chỉ cắt các khoảng trống dài hơn ngưỡng; đoạn xe dừng ngắn hơn được giữ nguyên.</p>
                </div>
              </header>

              <div className="condense-gap">
                <label htmlFor="min-gap">
                  Bỏ khoảng trống dài hơn <strong>{minGap} giây</strong>
                </label>
                <input
                  id="min-gap"
                  max={60}
                  min={3}
                  onChange={(event) => setMinGap(Number(event.target.value))}
                  step={1}
                  type="range"
                  value={minGap}
                  disabled={busy || processing}
                />
                <div className="condense-gap-ticks">
                  <span>3s</span>
                  <span>60s</span>
                </div>
                <small>
                  Với video thực tế (xe cách nhau 1–2 phút), nên đặt khoảng 15–20 giây. Đoạn xe dừng
                  ngắn hơn ngưỡng vẫn được giữ nguyên.
                </small>
              </div>

              <div className="condense-actions">
                <button
                  className="button button-primary button-block"
                  disabled={!file || busy || processing}
                  onClick={startCondense}
                  type="button"
                >
                  <Icon name="scissors" size={18} />
                  {busy ? "Đang tải video…" : "Bắt đầu cắt"}
                </button>
              </div>
            </section>
          </div>
        )}
      </div>

      {processing && (
        <div className="card condense-progress-card">
          <div className="condense-progress-head">
            <span className="condense-spinner">
              <Icon name="refresh" size={18} />
            </span>
            <div className="condense-progress-copy">
              <strong>Đang xử lý…</strong>
              <small>{stageLabels[status!.status] ?? "Đang xử lý…"}</small>
            </div>
            <b className="condense-progress-pct">{progressPercent}%</b>
          </div>
          <div className="condense-progress-track">
            <span style={{ width: `${Math.max(progressPercent, 4)}%` }} />
          </div>
          <div className="condense-progress-foot">
            <button
              className="button button-secondary button-compact"
              disabled={cancelling}
              onClick={cancelCut}
              type="button"
            >
              <Icon name="x" size={16} /> {cancelling ? "Đang huỷ…" : "Huỷ cắt"}
            </button>
          </div>
        </div>
      )}

      {status?.status === "empty" && (
        <div className="toast-inline toast-error" role="status">
          <Icon name="alert" size={18} />
          Không phát hiện xe nào trong video — không có gì để cắt.
        </div>
      )}

      {status?.status === "failed" && (
        <div className="toast-inline toast-error" role="status">
          <Icon name="alert" size={18} />
          Cắt video thất bại. Vui lòng thử lại.
        </div>
      )}

      {done && (
        <div className="card condense-result">
          <header className="section-heading">
            <span className="section-heading-done">
              <Icon name="check" size={18} />
            </span>
            <div>
              <h2>Đã cắt xong</h2>
              <p>Giữ lại {status!.segment_count} đoạn có xe, ghép thành một video liền mạch.</p>
            </div>
          </header>

          <div className="condense-stats">
            <div>
              <span>Video gốc</span>
              <strong>{formatTime(status!.source_duration_ms ?? 0)}</strong>
            </div>
            <div>
              <span>Sau khi cắt</span>
              <strong>{formatTime(status!.condensed_duration_ms ?? 0)}</strong>
            </div>
            <div className="condense-stat-highlight">
              <span>Đã bỏ đi</span>
              <strong>{formatTime(status!.cut_ms ?? 0)}</strong>
            </div>
            <div>
              <span>Số đoạn giữ</span>
              <strong>{status!.segment_count}</strong>
            </div>
          </div>

          <video
            className="condense-preview"
            controls
            preload="metadata"
            src={`/api/v1/condense/${status!.id}/download`}
          />

          <div className="condense-actions">
            <a
              className="button button-secondary"
              href={`/api/v1/condense/${status!.id}/download`}
            >
              <Icon name="download" size={18} /> Tải video về
            </a>
            <button
              className="button button-primary"
              onClick={() => status && onOpenInCreate(status)}
              type="button"
            >
              Chuyển sang tạo GT
              <Icon name="arrow" size={18} />
            </button>
          </div>
        </div>
      )}

      {message && (
        <div className={`toast-inline toast-${message.tone}`} role="status">
          <Icon name={message.tone === "success" ? "check" : "alert"} size={18} />
          {message.text}
        </div>
      )}

      <div className="card condense-library">
        <header className="section-heading">
          <span className="section-heading-plain">
            <Icon name="video" size={18} />
          </span>
          <div>
            <h2>Video đã cắt</h2>
            <p>Bấm để xem lại video + chi tiết đã cắt. Xoá những bản không cần nữa.</p>
          </div>
        </header>
        <CondensedList
          mode="manage"
          onDeleted={(id) => {
            if (status?.id === id) setStatus(null);
          }}
          onSendToGt={onOpenInCreate}
          reloadKey={listReload}
        />
      </div>
    </section>
  );
}
