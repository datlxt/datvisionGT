import {
  type ChangeEvent,
  type DragEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react";

import { Icon } from "../components/Icon";
import { PageHeader, StatusBadge } from "../components/ui";
import { api } from "../lib/api";
import { formatBytes, formatTime } from "../lib/format";
import type { Job } from "../types";

const acceptedExtensions = [".mp4", ".mov", ".avi", ".mkv", ".m4v"];

function SelectionCard({
  icon,
  title,
  description,
  selected = false,
  disabled = false,
  badge,
}: {
  icon?: ReactNode;
  title: string;
  description: string;
  selected?: boolean;
  disabled?: boolean;
  badge?: string;
}) {
  return (
    <div
      aria-disabled={disabled}
      aria-selected={selected}
      className={`selection-card ${selected ? "selected" : ""} ${disabled ? "disabled" : ""}`}
      role="option"
    >
      <div className="selection-card-top">
        {icon}
        {badge && <span>{badge}</span>}
      </div>
      <strong>{title}</strong>
      <p>{description}</p>
    </div>
  );
}

export function CreateJobPage({
  onDraftSaved,
  onStarted,
}: {
  onDraftSaved: (job: Job) => void;
  onStarted: (job: Job) => void;
}) {
  const inputRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [draft, setDraft] = useState<Job | null>(null);
  const [busyAction, setBusyAction] = useState<"draft" | "start" | null>(null);
  const [dragging, setDragging] = useState(false);
  const [message, setMessage] = useState<{ tone: "success" | "error"; text: string } | null>(null);

  useEffect(
    () => () => {
      if (preview) URL.revokeObjectURL(preview);
    },
    [preview],
  );

  function setSelectedFile(selected: File | null) {
    if (preview) URL.revokeObjectURL(preview);
    setFile(selected);
    setPreview(selected ? URL.createObjectURL(selected) : null);
    setDraft(null);
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

  async function createDraft() {
    if (!file) throw new Error("Vui lòng chọn video trước.");
    const created = await api<Job>("/api/v1/jobs", {
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
    if (!file || busyAction) return;
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
    if (!file || busyAction) return;
    setBusyAction("start");
    setMessage(null);
    try {
      const currentDraft = draft ?? (await createDraft());
      const queued = await api<Job>(`/api/v1/jobs/${currentDraft.id}/start`, {
        method: "POST",
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
        description="Đưa video xe máy qua trạm vào pipeline Ground Truth có thể truy vết."
        title="Tạo job xử lý mới"
      />

      <div className="create-grid">
        <div className="create-form">
          <section className="card form-card">
            <header className="section-heading">
              <span>1</span>
              <div>
                <h2>Dữ liệu đầu vào</h2>
                <p>Backend hiện hỗ trợ một video cho mỗi job.</p>
              </div>
            </header>

            <div className="source-tabs" role="tablist">
              <button aria-selected="true" className="active" role="tab" type="button">
                <Icon name="video" size={18} /> Video
              </button>
              <button
                aria-selected="false"
                disabled
                role="tab"
                title="Backend chưa hỗ trợ IMAGE_SET"
                type="button"
              >
                <Icon name="images" size={18} /> Tập ảnh
                <small>Chưa hỗ trợ</small>
              </button>
            </div>

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
                    {draft ? `Đã lưu ${draft.job_code}` : "Sẵn sàng tải lên"}
                  </StatusBadge>
                </div>
                <button
                  className="button button-secondary button-compact"
                  disabled={Boolean(busyAction)}
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
          </section>

          <section className="card form-card">
            <header className="section-heading">
              <span>2</span>
              <div>
                <h2>Phạm vi xử lý</h2>
                <p>Chỉ bật chức năng backend đang triển khai thực tế.</p>
              </div>
            </header>

            <div className="selection-grid scope-grid" role="listbox">
              <SelectionCard
                badge="Đang sử dụng"
                description="Phát hiện xe máy, biển số và OCR nhiều frame."
                icon={<Icon name="plate" size={24} />}
                selected
                title="Biển số xe máy"
              />
              <SelectionCard
                badge="Chưa triển khai"
                description="Không hoạt động trong MVP hiện tại."
                disabled
                icon={<Icon name="face" size={24} />}
                title="Khuôn mặt"
              />
              <SelectionCard
                badge="Chưa triển khai"
                description="Không thể chạy hai pipeline cùng lúc."
                disabled
                icon={<Icon name="layers" size={24} />}
                title="Khuôn mặt và biển số"
              />
            </div>

            <div className="readonly-fields">
              <label>
                <span>Camera preset</span>
                <div className="readonly-control">
                  <Icon name="camera" size={18} />
                  <strong>Camera sau · trạm vé</strong>
                  <small>Cấu hình cố định phía server</small>
                </div>
              </label>
              <label>
                <span>Vùng quét</span>
                <div className="readonly-control">
                  <Icon name="scan" size={18} />
                  <strong>ROI cố định của pipeline</strong>
                  <small>Chưa có API chọn ROI</small>
                </div>
              </label>
            </div>
          </section>

          <section className="card form-card">
            <header className="section-heading">
              <span>3</span>
              <div>
                <h2>Cấu hình xử lý</h2>
                <p>Giá trị khớp với DTO và cấu hình job đang được backend lưu.</p>
              </div>
            </header>

            <div className="selection-grid processing-modes" role="listbox">
              <SelectionCard
                badge="Chưa khả dụng"
                description="Backend chưa nhận lựa chọn này."
                disabled
                title="High Recall"
              />
              <SelectionCard
                badge="Chế độ chuẩn"
                description="Cấu hình BALANCED đang chạy ổn định."
                selected
                title="Balanced"
              />
              <SelectionCard
                badge="Chưa khả dụng"
                description="Backend chưa nhận lựa chọn này."
                disabled
                title="Fast"
              />
            </div>

            <div className="sample-rate-row">
              <label>
                <span>Sample rate</span>
                <div>
                  <input aria-label="Sample rate" readOnly value="4" />
                  <small>frames / giây</small>
                </div>
              </label>
              <p>Được lưu vào trường <code>sample_rate</code> của job.</p>
            </div>

            <details className="advanced-config">
              <summary>
                Cấu hình nâng cao
                <Icon name="chevron" size={17} />
              </summary>
              <p>
                Model và ngưỡng nhận diện do worker quản lý. UI hiện không có endpoint để thay đổi
                các giá trị này.
              </p>
            </details>
          </section>

          <section className="card action-footer">
            <p>
              Tool chỉ sinh kết quả model và evidence. GT Final cần API kiểm duyệt của backend trước
              khi có thể xuất.
            </p>
            <div>
              <button
                className="button button-secondary"
                disabled={!file || Boolean(busyAction)}
                onClick={saveDraft}
                type="button"
              >
                {busyAction === "draft" ? "Đang lưu…" : "Lưu nháp"}
              </button>
              <button
                className="button button-primary"
                disabled={!file || Boolean(busyAction)}
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
                <Icon name="camera" size={18} /> Camera
              </dt>
              <dd>Camera sau · trạm vé</dd>
            </div>
            <div>
              <dt>
                <Icon name="plate" size={18} /> Loại xử lý
              </dt>
              <dd>Biển số xe máy</dd>
            </div>
            <div>
              <dt>
                <Icon name="scan" size={18} /> Vùng quét
              </dt>
              <dd>ROI cố định</dd>
            </div>
            <div>
              <dt>
                <Icon name="layers" size={18} /> Chế độ
              </dt>
              <dd>
                <StatusBadge tone="success">Balanced · Chuẩn</StatusBadge>
              </dd>
            </div>
            <div>
              <dt>
                <Icon name="clock" size={18} /> Sample rate
              </dt>
              <dd>4 FPS</dd>
            </div>
          </dl>

          <div className="summary-notice">
            <Icon name="shield" size={20} />
            <p>
              <strong>No evidence, no record.</strong>
              Mọi case đều phải có full frame, timestamp, bounding box và crop truy vết được.
            </p>
          </div>
        </aside>
      </div>
    </section>
  );
}
