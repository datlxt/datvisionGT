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
import type { CondenseStatus, Job } from "../types";

const acceptedExtensions = [".mp4", ".mov", ".avi", ".mkv", ".m4v"];

export function CreateJobPage({
  initialCondensed = null,
  initialVehicleType = "motorcycle",
  onDraftSaved,
  onStarted,
}: {
  initialCondensed?: CondenseStatus | null;
  initialVehicleType?: "motorcycle" | "car";
  onDraftSaved: (job: Job) => void;
  onStarted: (job: Job) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [draft, setDraft] = useState<Job | null>(null);
  const [busyAction, setBusyAction] = useState<"draft" | "start" | null>(null);
  const [dragging, setDragging] = useState(false);
  const [vehicleType, setVehicleType] = useState<"motorcycle" | "car">(initialVehicleType);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);
  const roiVideoRef = useRef<HTMLVideoElement>(null);
  const [roi, setRoi] = useState<{ x1: number; y1: number; x2: number; y2: number } | null>(null);
  const [laneDirection, setLaneDirection] = useState<"" | "up" | "down" | "left" | "right">("");
  // Frames analysed per second of video. Editable (1–12), default 2 (the long-stable value the
  // pipeline actually runs). Clamped so a bad value can't reach the extractor (0 / negative / huge).
  const [sampleRate, setSampleRate] = useState(2);
  // Video source origin: a fresh upload from disk, or one of the user's already-condensed videos.
  const [sourceMode, setSourceMode] = useState<"new" | "condensed">("new");
  const [condensedPick, setCondensedPick] = useState<CondenseStatus | null>(null);
  const hasSource = Boolean(file) || Boolean(condensedPick);
  const roiDragStart = useRef<{ x: number; y: number } | null>(null);

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

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  // Handed a condensed video from the Cắt video page: open on the "Chọn video đã cắt" tab with it
  // already selected, so the reviewer lands straight on the ROI step. Runs once (App remounts this
  // page via key when the chosen video changes).
  useEffect(() => {
    if (!initialCondensed) return;
    setSourceMode("condensed");
    setCondensedPick(initialCondensed);
    setPreview(`/api/v1/condense/${initialCondensed.id}/download`);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  function setSelectedFile(selected: File | null) {
    if (preview?.startsWith("blob:")) URL.revokeObjectURL(preview);
    setFile(selected);
    setCondensedPick(null);
    setPreview(selected ? URL.createObjectURL(selected) : null);
    setDraft(null);
    setMessage(null);
    setRoi(null);
    setLaneDirection("");
  }

  // Pick one of the user's already-condensed videos as the source. The job is created lazily (on
  // Save/Start) via /jobs/from-condense so the current vehicle type applies; here we just load the
  // condensed video into the ROI preview so the reviewer can draw the lane on it.
  function pickCondensed(item: CondenseStatus) {
    if (preview?.startsWith("blob:")) URL.revokeObjectURL(preview);
    setFile(null);
    setCondensedPick(item);
    setPreview(`/api/v1/condense/${item.id}/download`);
    setDraft(null);
    setMessage(null);
    setRoi(null);
    setLaneDirection("");
  }

  function switchSource(mode: "new" | "condensed") {
    if (mode === sourceMode) return;
    setSourceMode(mode);
    setSelectedFile(null);
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

  async function createDraft() {
    if (condensedPick) {
      const importedDraft = await api<Job>(
        `/api/v1/jobs/from-condense/${condensedPick.id}?vehicle_type=${vehicleType}`,
        { method: "POST" },
      );
      setDraft(importedDraft);
      onDraftSaved(importedDraft);
      return importedDraft;
    }
    if (!file) throw new Error("Vui lòng chọn video trước.");
    const created = await api<Job>(`/api/v1/jobs?vehicle_type=${vehicleType}`, {
      method: "POST",
      body: file,
      headers: {
        "Content-Type": file.type || "application/octet-stream",
        // Percent-encode: HTTP headers must be ISO-8859-1, but filenames may have
        // Vietnamese/Unicode characters. Backend decodes it.
        "X-Filename": encodeURIComponent(file.name),
      },
    });
    setDraft(created);
    onDraftSaved(created);
    return created;
  }

  async function saveDraft() {
    if (!hasSource || busyAction) return;
    setBusyAction("draft");
    setMessage(null);
    try {
      const saved = draft ?? (await createDraft());
      setMessage({
        tone: "success",
        text: `Đã lưu bản nháp ${saved.job_code}. Video chưa được đưa vào hàng đợi xử lý.`,
      });
    } catch (reason) {
      setMessage({
        tone: "error",
        text: reason instanceof Error ? reason.message : "Không thể lưu bản nháp.",
      });
    } finally {
      setBusyAction(null);
    }
  }

  async function start() {
    if (!hasSource || busyAction) return;
    setBusyAction("start");
    setMessage(null);
    try {
      const currentDraft = draft ?? (await createDraft());
      const queued = await api<Job>(`/api/v1/jobs/${currentDraft.id}/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          roi: roi ? [roi.x1, roi.y1, roi.x2, roi.y2] : null,
          lane_direction: laneDirection || null,
          sample_rate: sampleRate,
        }),
      });
      onStarted(queued);
    } catch (reason) {
      setMessage({
        tone: "error",
        text: reason instanceof Error ? reason.message : "Không thể bắt đầu xử lý.",
      });
    } finally {
      setBusyAction(null);
    }
  }

  return (
    <section className="page create-page">
      <PageHeader
        description="Tải video lên để nhận diện phương tiện, biển số và tạo dữ liệu Ground Truth có thể truy vết."
        title="Tạo job xử lý mới"
      />

      <div className="create-grid">
        <div className="create-form">
          <section className="card form-card">
            <header className="section-heading">
              <span>1</span>
              <div>
                <h2>Dữ liệu đầu vào</h2>
                <p>Hiện hệ thống xử lý một video cho mỗi job.</p>
              </div>
            </header>

            <div className="source-origin" role="tablist">
              <button
                aria-selected={sourceMode === "new"}
                className={sourceMode === "new" ? "active" : ""}
                onClick={() => switchSource("new")}
                role="tab"
                type="button"
              >
                <Icon name="upload" size={16} /> Tải video mới
              </button>
              <button
                aria-selected={sourceMode === "condensed"}
                className={sourceMode === "condensed" ? "active" : ""}
                onClick={() => switchSource("condensed")}
                role="tab"
                type="button"
              >
                <Icon name="scissors" size={16} /> Chọn video đã cắt
              </button>
            </div>

            {sourceMode === "new" && (
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
                <input
                  accept="video/*,.mkv,.m4v"
                  onChange={chooseFile}
                  ref={inputRef}
                  type="file"
                />
                <span className="upload-symbol">
                  <Icon name="upload" size={28} />
                </span>
                <strong>Kéo thả video vào đây hoặc nhấn để chọn</strong>
                <p>MP4, AVI, MOV, MKV, M4V · Tối đa 2 GB</p>
              </div>
            )}

            {sourceMode === "condensed" && !condensedPick && (
              <CondensedList
                mode="pick"
                onPick={pickCondensed}
                picking={Boolean(busyAction)}
              />
            )}

            {hasSource && (
              <div className="selected-file">
                {preview && <video muted preload="metadata" src={preview} />}
                <div className="selected-file-copy">
                  <strong title={file ? file.name : condensedPick?.source_name ?? undefined}>
                    {file ? file.name : condensedPick?.source_name ?? "Video đã cắt"}
                  </strong>
                  <div>
                    {file ? (
                      <span>
                        <Icon name="file" size={15} /> {formatBytes(file.size)}
                      </span>
                    ) : (
                      <span>
                        <Icon name="scissors" size={15} /> Đã cắt · giữ{" "}
                        {formatTime(condensedPick?.condensed_duration_ms ?? 0)}
                      </span>
                    )}
                    {draft?.duration_ms !== null && draft && (
                      <span>
                        <Icon name="clock" size={15} /> {formatTime(draft.duration_ms)}
                      </span>
                    )}
                    {draft?.width && draft.height && (
                      <span>
                        {draft.width} × {draft.height}
                      </span>
                    )}
                    {draft?.fps && <span>{draft.fps.toFixed(1)} FPS</span>}
                  </div>
                  <StatusBadge tone={draft ? "success" : "info"}>
                    {draft
                      ? `Đã lưu ${draft.job_code}`
                      : file
                        ? "Sẵn sàng tải lên"
                        : "Sẵn sàng xử lý"}
                  </StatusBadge>
                </div>
                <button
                  className="button button-secondary button-compact"
                  disabled={Boolean(busyAction)}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (file) {
                      inputRef.current?.click();
                    } else {
                      if (preview?.startsWith("blob:")) URL.revokeObjectURL(preview);
                      setCondensedPick(null);
                      setPreview(null);
                      setDraft(null);
                      setRoi(null);
                    }
                  }}
                  type="button"
                >
                  {file ? "Đổi tệp khác" : "Chọn video khác"}
                </button>
              </div>
            )}
          </section>

          <section className="card form-card">
            <header className="section-heading">
              <span>2</span>
              <div>
                <h2>Phạm vi xử lý</h2>
                <p>Hiện hệ thống nhận diện biển số: phát hiện xe, đọc biển và OCR nhiều khung hình.</p>
              </div>
            </header>

            <div className="vehicle-type-block">
              <h3>Loại phương tiện</h3>
              <div className="selection-grid vehicle-type-grid" role="listbox">
                <button
                  aria-selected={vehicleType === "motorcycle"}
                  className={`selection-card ${vehicleType === "motorcycle" ? "selected" : ""}`}
                  onClick={() => setVehicleType("motorcycle")}
                  role="option"
                  type="button"
                >
                  <div className="selection-card-top">
                    <Icon name="motorcycle" size={24} />
                    <span>Mặc định</span>
                  </div>
                  <strong>Xe máy</strong>
                  <p>Biển 2 dòng, camera sau trạm/bãi.</p>
                </button>
                <button
                  aria-selected={vehicleType === "car"}
                  className={`selection-card ${vehicleType === "car" ? "selected" : ""}`}
                  onClick={() => setVehicleType("car")}
                  role="option"
                  type="button"
                >
                  <div className="selection-card-top">
                    <Icon name="car" size={24} />
                    <span>Mới</span>
                  </div>
                  <strong>Ô tô con</strong>
                  <p>Biển 1 dòng. Cùng model, đổi lớp nhận diện xe.</p>
                </button>
              </div>
            </div>

            <div className="roi-config">
              <div className="roi-config-head">
                <h3>Vùng quét (ROI) &amp; hướng làn</h3>
                <p>Khoanh đúng làn để pipeline không bắt nhầm sang làn khác / nền / đèn.</p>
              </div>
              {preview ? (
                <>
                  <div
                    className="roi-stage"
                    onPointerDown={roiDown}
                    onPointerMove={roiMove}
                    onPointerUp={roiUp}
                    onPointerLeave={roiUp}
                  >
                    <video muted playsInline preload="metadata" ref={roiVideoRef} src={preview} />
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
                    <span className="roi-hint">Kéo chuột để khoanh vùng quét</span>
                  </div>
                  <label className="roi-seek">
                    <span>Thời điểm ảnh nền</span>
                    <input
                      aria-label="Thời điểm ảnh nền"
                      defaultValue={0}
                      max={100}
                      min={0}
                      onChange={(event) => {
                        const video = roiVideoRef.current;
                        if (video && video.duration) {
                          video.currentTime = (Number(event.target.value) / 100) * video.duration;
                        }
                      }}
                      type="range"
                    />
                  </label>
                  <div className="roi-controls">
                    <label>
                      <span>Hướng làn</span>
                      <select
                        onChange={(event) =>
                          setLaneDirection(event.target.value as typeof laneDirection)
                        }
                        value={laneDirection}
                      >
                        <option value="">Không đặt · quét cả khung</option>
                        <option value="down">Xe đi xuống (vào từ mép trên)</option>
                        <option value="up">Xe đi lên (vào từ mép dưới)</option>
                        <option value="right">Xe đi sang phải</option>
                        <option value="left">Xe đi sang trái</option>
                      </select>
                    </label>
                    <div className="roi-readout">
                      <span>ROI</span>
                      {roi ? (
                        <code>
                          {roi.x1.toFixed(3)}, {roi.y1.toFixed(3)}, {roi.x2.toFixed(3)},{" "}
                          {roi.y2.toFixed(3)}
                        </code>
                      ) : (
                        <em>chưa vẽ — quét cả khung</em>
                      )}
                      {roi && (
                        <button
                          className="button button-secondary button-compact"
                          onClick={() => setRoi(null)}
                          type="button"
                        >
                          Xoá
                        </button>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <p className="roi-empty">Chọn video ở bước 1 để vẽ vùng quét.</p>
              )}
            </div>
          </section>

          <section className="card form-card">
            <header className="section-heading">
              <span>3</span>
              <div>
                <h2>Cấu hình xử lý</h2>
                <p>Hiện hệ thống chạy chế độ cân bằng giữa độ chính xác và tốc độ.</p>
              </div>
            </header>

            <div className="sample-rate-row">
              <label>
                <span>Tần suất lấy mẫu</span>
                <div>
                  <input
                    aria-label="Tần suất lấy mẫu"
                    max={12}
                    min={1}
                    onBlur={(event) => {
                      // Snap an out-of-range / empty entry back into [1, 12] when focus leaves.
                      const value = Math.round(Number(event.target.value));
                      setSampleRate(Number.isFinite(value) ? Math.max(1, Math.min(12, value)) : 2);
                    }}
                    onChange={(event) => setSampleRate(Number(event.target.value))}
                    step={1}
                    type="number"
                    value={sampleRate}
                  />
                  <small>khung hình / giây</small>
                </div>
              </label>
            </div>
          </section>

          <section className="card action-footer">
            <p>
              Bước này chỉ tạo kết quả model kèm bằng chứng. GT chính thức cần qua bước kiểm duyệt
              trước khi xuất.
            </p>
            <div>
              <button
                className="button button-secondary"
                disabled={!hasSource || Boolean(busyAction)}
                onClick={saveDraft}
                type="button"
              >
                {busyAction === "draft" ? "Đang lưu…" : "Lưu nháp"}
              </button>
              <button
                className="button button-primary"
                disabled={!hasSource || Boolean(busyAction)}
                onClick={start}
                type="button"
              >
                {busyAction === "start" ? "Đang tải video…" : "Bắt đầu xử lý"}
                <Icon name="arrow" size={18} />
              </button>
            </div>
          </section>

          {message && (
            <div className={`toast-inline toast-${message.tone}`} role="status">
              {message.tone === "success" ? (
                <Icon name="check" size={18} />
              ) : (
                <Icon name="alert" size={18} />
              )}
              {message.text}
            </div>
          )}
        </div>

        <aside className="card job-summary">
          <h2>Tóm tắt job</h2>
          <dl>
            <div>
              <dt>
                <Icon name="file" size={18} /> File nguồn
              </dt>
              <dd>{file?.name ?? "Chưa chọn video"}</dd>
            </div>
            <div>
              <dt>
                <Icon name="plate" size={18} /> Loại xử lý
              </dt>
              <dd>Biển số · {vehicleType === "car" ? "Ô tô con" : "Xe máy"}</dd>
            </div>
            <div>
              <dt>
                <Icon name="scan" size={18} /> Vùng quét
              </dt>
              <dd>
                {roi ? "ROI tuỳ chỉnh" : "Cả khung"}
                {laneDirection &&
                  ` · ${
                    { down: "đi xuống", up: "đi lên", right: "sang phải", left: "sang trái" }[
                      laneDirection
                    ]
                  }`}
              </dd>
            </div>
            <div>
              <dt>
                <Icon name="layers" size={18} /> Chế độ
              </dt>
              <dd>
                <StatusBadge tone="success">Cân bằng · Chuẩn</StatusBadge>
              </dd>
            </div>
            <div>
              <dt>
                <Icon name="clock" size={18} /> Tần suất lấy mẫu
              </dt>
              <dd>{sampleRate} khung/giây</dd>
            </div>
          </dl>

          <div className="summary-notice">
            <Icon name="shield" size={20} />
            <p>
              <strong>Không bằng chứng, không ghi nhận.</strong>
              Mọi lượt xe đều phải truy xuất được về khung hình gốc, mốc thời gian, vùng khoanh
              và ảnh crop biển số.
            </p>
          </div>
        </aside>
      </div>
    </section>
  );
}
