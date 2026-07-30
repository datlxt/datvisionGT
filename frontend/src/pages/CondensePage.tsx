import {
  type ChangeEvent,
  type DragEvent,
  useEffect,
  useRef,
  useState,
} from "react";

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

export function CondensePage({ onSentToJob }: { onSentToJob: (jobId: string) => void }) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [minGap, setMinGap] = useState(15);
  const [dragging, setDragging] = useState(false);
  const [busy, setBusy] = useState(false);
  const [sendingJob, setSendingJob] = useState(false);
  const [status, setStatus] = useState<CondenseStatus | null>(null);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

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

  function setSelectedFile(selected: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(selected);
    setPreview(selected ? URL.createObjectURL(selected) : null);
    setStatus(null);
    setMessage(null);
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
      setMessage({ tone: "error", text: "Định dạng video chưa được backend hỗ trợ." });
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
      const created = await api<CondenseStatus>(`/api/v1/condense?min_gap_seconds=${minGap}`, {
        method: "POST",
        body: file,
        headers: {
          "Content-Type": file.type || "application/octet-stream",
          "X-Filename": encodeURIComponent(file.name),
        },
      });
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

  async function sendToJob() {
    if (!status || sendingJob) return;
    setSendingJob(true);
    setMessage(null);
    try {
      const res = await api<{ job_id: string }>(`/api/v1/condense/${status.id}/to-job`, {
        method: "POST",
      });
      onSentToJob(res.job_id);
    } catch (reason) {
      setMessage({
        tone: "error",
        text: reason instanceof Error ? reason.message : "Không thể đưa vào xử lý.",
      });
      setSendingJob(false);
    }
  }

  const processing = Boolean(status) && !terminal.includes(status!.status);
  const done = status?.status === "completed";
  const progressPercent = Math.round((status?.progress ?? 0) * 100);

  return (
    <section className="page condense-page">
      <PageHeader
        description="Bỏ những khoảng thời gian không có xe, dồn các lượt xe lại thành một video ngắn để đưa vào tạo GT."
        title="Cắt video – bỏ thời gian chết"
      />

      <div className="card form-card">
        <header className="section-heading">
          <span>1</span>
          <div>
            <h2>Video gốc</h2>
            <p>Video chưa cắt, còn nhiều khoảng trống không có xe.</p>
          </div>
        </header>

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
            <Icon name="upload" size={28} />
          </span>
          <strong>Kéo thả video vào đây hoặc bấm để chọn</strong>
          <p>MP4, AVI, MOV, MKV, M4V · Tối đa 2 GB</p>
        </div>

        {file && (
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
              className="button button-secondary button-compact"
              disabled={busy || processing}
              onClick={(event) => {
                event.stopPropagation();
                inputRef.current?.click();
              }}
              type="button"
            >
              Thay file
            </button>
          </div>
        )}
      </div>

      <div className="card form-card">
        <header className="section-heading">
          <span>2</span>
          <div>
            <h2>Mức cắt</h2>
            <p>Chỉ cắt những khoảng trống dài hơn ngưỡng này, để không cắt nhầm lúc xe dừng lại.</p>
          </div>
        </header>

        <div className="condense-gap">
          <label htmlFor="min-gap">
            Bỏ khoảng trống dài hơn: <strong>{minGap} giây</strong>
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
          <small>
            Video thật (xe cách nhau 1–2 phút) nên để ~15–20s. Đoạn xe dừng ngắn hơn ngưỡng vẫn được
            giữ nguyên.
          </small>
        </div>

        <div className="condense-actions">
          <button
            className="button button-primary"
            disabled={!file || busy || processing}
            onClick={startCondense}
            type="button"
          >
            <Icon name="scissors" size={18} />
            {busy ? "Đang tải video…" : "Bắt đầu cắt"}
          </button>
        </div>
      </div>

      {status && (
        <div className="card condense-result">
          {processing && (
            <>
              <header className="section-heading">
                <span>
                  <Icon name="refresh" size={18} />
                </span>
                <div>
                  <h2>Đang xử lý…</h2>
                  <p>{stageLabels[status.status] ?? "Đang xử lý…"}</p>
                </div>
              </header>
              <div className="condense-progress">
                <div className="condense-progress-bar">
                  <span style={{ width: `${progressPercent}%` }} />
                </div>
                <strong>{progressPercent}%</strong>
              </div>
            </>
          )}

          {status.status === "empty" && (
            <div className="toast-inline toast-error" role="status">
              <Icon name="alert" size={18} />
              Không phát hiện xe nào trong video — không có gì để cắt.
            </div>
          )}

          {status.status === "failed" && (
            <div className="toast-inline toast-error" role="status">
              <Icon name="alert" size={18} />
              Cắt video thất bại. Vui lòng thử lại.
            </div>
          )}

          {done && (
            <>
              <header className="section-heading">
                <span>
                  <Icon name="check" size={18} />
                </span>
                <div>
                  <h2>Đã cắt xong</h2>
                  <p>
                    Giữ lại {status.segment_count} đoạn có xe, ghép thành một video liền mạch.
                  </p>
                </div>
              </header>

              <div className="condense-stats">
                <div>
                  <span>Video gốc</span>
                  <strong>{formatTime(status.source_duration_ms ?? 0)}</strong>
                </div>
                <div>
                  <span>Sau khi cắt</span>
                  <strong>{formatTime(status.condensed_duration_ms ?? 0)}</strong>
                </div>
                <div className="condense-stat-highlight">
                  <span>Đã bỏ đi</span>
                  <strong>{formatTime(status.cut_ms ?? 0)}</strong>
                </div>
                <div>
                  <span>Số đoạn giữ</span>
                  <strong>{status.segment_count}</strong>
                </div>
              </div>

              <video
                className="condense-preview"
                controls
                preload="metadata"
                src={`/api/v1/condense/${status.id}/download`}
              />

              <div className="condense-actions">
                <a
                  className="button button-secondary"
                  href={`/api/v1/condense/${status.id}/download`}
                >
                  <Icon name="download" size={18} /> Tải video về
                </a>
                <button
                  className="button button-primary"
                  disabled={sendingJob}
                  onClick={sendToJob}
                  type="button"
                >
                  {sendingJob ? "Đang gửi…" : "Đưa vào làm GT"}
                  <Icon name="arrow" size={18} />
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {message && (
        <div className={`toast-inline toast-${message.tone}`} role="status">
          <Icon name={message.tone === "success" ? "check" : "alert"} size={18} />
          {message.text}
        </div>
      )}
    </section>
  );
}
