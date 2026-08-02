import { useEffect, useState } from "react";

import { Icon } from "../components/Icon";
import {
  ConfirmDialog,
  EmptyState,
  PageHeader,
  ProgressBar,
  StatusBadge,
} from "../components/ui";
import { api } from "../lib/api";
import { formatDate, formatTime, statusLabel, statusTone } from "../lib/format";
import { useJobDeletion } from "../lib/useJobDeletion";
import type { CondenseStatus, Job } from "../types";

type Scope = "video" | "motorcycle" | "car";

const scopeOptions: { key: Scope; label: string; icon: "video" | "motorcycle" | "car" }[] = [
  { key: "video", label: "Video", icon: "video" },
  { key: "motorcycle", label: "Xe máy", icon: "motorcycle" },
  { key: "car", label: "Ô tô", icon: "car" },
];

const condenseLabels: Record<string, string> = {
  completed: "Đã cắt",
  processing: "Đang cắt",
  scanning: "Đang cắt",
  rendering: "Đang cắt",
  queued: "Trong hàng đợi",
  empty: "Không có xe",
  failed: "Thất bại",
};

function condenseTone(status: string): "success" | "info" | "warning" | "danger" {
  if (status === "completed") return "success";
  if (status === "empty") return "warning";
  if (status === "failed") return "danger";
  return "info";
}

export function OverviewPage({
  jobs,
  onCreate,
  onDelete,
  onOpen,
}: {
  jobs: Job[];
  onCreate: (scope: Scope) => void;
  onDelete: (job: Job) => Promise<void>;
  onOpen: (job: Job) => void;
}) {
  const deletion = useJobDeletion(onDelete);
  const [scope, setScope] = useState<Scope>("video");
  const [condenseList, setCondenseList] = useState<CondenseStatus[]>([]);

  useEffect(() => {
    api<CondenseStatus[]>("/api/v1/condense")
      .then(setCondenseList)
      .catch(() => setCondenseList([]));
  }, []);

  const isVideo = scope === "video";
  const scoped = isVideo ? jobs : jobs.filter((job) => job.vehicle_type === scope);

  const processing = scoped.filter((job) =>
    ["PENDING", "QUEUED", "PROCESSING"].includes(job.status),
  ).length;
  const review = scoped.filter((job) => job.status === "WAITING_FOR_REVIEW").length;
  const completed = scoped.filter((job) => job.status === "COMPLETED").length;
  const failed = scoped.filter((job) => job.status === "FAILED").length;

  const condenseDone = condenseList.filter((item) => item.status === "completed");
  const condenseBusy = condenseList.filter((item) =>
    ["queued", "scanning", "rendering", "processing"].includes(item.status),
  ).length;
  const totalCutMs = condenseDone.reduce((sum, item) => sum + (item.cut_ms ?? 0), 0);
  const totalOriginalMs = condenseDone.reduce(
    (sum, item) => sum + (item.source_duration_ms ?? 0),
    0,
  );
  const totalCondensedMs = condenseDone.reduce(
    (sum, item) => sum + (item.condensed_duration_ms ?? 0),
    0,
  );

  const kpis: { label: string; value: number | string; icon: "video" | "scissors" | "clock" | "check" | "layers" | "shield"; tone: string }[] = isVideo
    ? [
        { label: "Tổng video cắt", value: condenseList.length, icon: "video", tone: "blue" },
        { label: "Đã cắt xong", value: condenseDone.length, icon: "scissors", tone: "blue" },
        { label: "Đang cắt", value: condenseBusy, icon: "clock", tone: "orange" },
        { label: "Thời gian đã bỏ", value: formatTime(totalCutMs), icon: "check", tone: "green" },
      ]
    : [
        { label: "Tổng job", value: scoped.length, icon: "layers", tone: "blue" },
        { label: "Đang xử lý", value: processing, icon: "clock", tone: "blue" },
        { label: "Chờ kiểm duyệt", value: review, icon: "shield", tone: "orange" },
        { label: "Đã hoàn thành", value: completed, icon: "check", tone: "green" },
      ];

  return (
    <section className="page overview-page">
      <PageHeader
        action={
          <button className="button button-primary" onClick={onCreate} type="button">
            <Icon name="plus" size={19} /> Tạo job mới
          </button>
        }
        description="Theo dõi tiến độ xử lý video và các job đang chờ kiểm duyệt."
        title="Tổng quan Ground Truth"
      />

      <div className="overview-scope-row">
        <div className="overview-scope" role="tablist">
          {scopeOptions.map((option) => (
            <button
              aria-selected={scope === option.key}
              className={scope === option.key ? "active" : ""}
              key={option.key}
              onClick={() => setScope(option.key)}
              role="tab"
              type="button"
            >
              <Icon name={option.icon} size={17} /> {option.label}
            </button>
          ))}
        </div>
      </div>

      <div className="kpi-grid">
        {kpis.map((item) => (
          <article className="card kpi-card" key={item.label}>
            <span className={`kpi-icon kpi-${item.tone}`}>
              <Icon name={item.icon} size={25} />
            </span>
            <div>
              <p>{item.label}</p>
              <strong>
                {typeof item.value === "number" ? item.value.toLocaleString("vi-VN") : item.value}
              </strong>
            </div>
          </article>
        ))}
      </div>

      {isVideo ? (
        <div className="overview-grid">
          <section className="card table-card">
            <header>
              <div>
                <h2>Video đã cắt</h2>
                <p>Các video bỏ thời gian chết, tạo ở tab Cắt video.</p>
              </div>
            </header>
            {condenseList.length === 0 ? (
              <EmptyState
                description="Vào tab Cắt video để bỏ thời gian không có xe và dồn các lượt xe lại."
                title="Chưa có video cắt"
              />
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tên video</th>
                      <th>Gốc → Sau</th>
                      <th>Đã bỏ</th>
                      <th>Số đoạn</th>
                      <th>Trạng thái</th>
                      <th aria-label="Thao tác" />
                    </tr>
                  </thead>
                  <tbody>
                    {condenseList.slice(0, 8).map((item) => (
                      <tr key={item.id}>
                        <td>
                          <div className="file-cell">
                            <Icon name="scissors" size={18} />
                            <div>
                              <strong title={item.source_name ?? ""}>
                                {item.source_name ?? "—"}
                              </strong>
                              <small>Bỏ trống &gt; {item.min_gap_seconds ?? "?"}s</small>
                            </div>
                          </div>
                        </td>
                        <td>
                          {item.source_duration_ms != null && item.condensed_duration_ms != null
                            ? `${formatTime(item.source_duration_ms)} → ${formatTime(item.condensed_duration_ms)}`
                            : "—"}
                        </td>
                        <td>{item.cut_ms != null ? formatTime(item.cut_ms) : "—"}</td>
                        <td>{item.segment_count ?? "—"}</td>
                        <td>
                          <StatusBadge tone={condenseTone(item.status)}>
                            {condenseLabels[item.status] ?? item.status}
                          </StatusBadge>
                        </td>
                        <td className="row-action-cell">
                          {item.status === "completed" && (
                            <a
                              aria-label={`Tải ${item.source_name ?? "video"}`}
                              className="icon-button"
                              href={`/api/v1/condense/${item.id}/download`}
                              title="Tải video đã cắt"
                            >
                              <Icon name="download" size={17} />
                            </a>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <aside className="card attention-card">
            <h2>Tổng quan cắt</h2>
            <div className="attention-list">
              <div>
                <span className="attention-icon attention-blue">
                  <Icon name="clock" size={22} />
                </span>
                <div>
                  <strong>{formatTime(totalOriginalMs)}</strong>
                  <p>tổng thời lượng gốc</p>
                </div>
              </div>
              <div>
                <span className="attention-icon attention-blue">
                  <Icon name="video" size={22} />
                </span>
                <div>
                  <strong>{formatTime(totalCondensedMs)}</strong>
                  <p>tổng sau khi cắt</p>
                </div>
              </div>
              <div>
                <span className="attention-icon attention-orange">
                  <Icon name="scissors" size={22} />
                </span>
                <div>
                  <strong>{formatTime(totalCutMs)}</strong>
                  <p>tổng thời gian đã bỏ</p>
                </div>
              </div>
            </div>
            <p className="backend-note">
              Video đã cắt có thể tải về hoặc đưa thẳng vào làm GT ở tab Cắt video.
            </p>
          </aside>
        </div>
      ) : (
        <div className="overview-grid">
          <section className="card table-card">
            <header>
              <div>
                <h2>Job gần đây</h2>
                <p>Dữ liệu được lấy trực tiếp từ API processing jobs.</p>
              </div>
            </header>
            {scoped.length === 0 ? (
              <EmptyState
                action={
                  <button className="button button-primary" onClick={onCreate} type="button">
                    Tạo job đầu tiên
                  </button>
                }
                description="Chọn một video qua trạm để bắt đầu."
                title="Chưa có job"
              />
            ) : (
              <div className="data-table-wrap">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tên dữ liệu</th>
                      <th>Tiến độ</th>
                      <th>Chế độ</th>
                      <th>Trạng thái</th>
                      <th>Cập nhật</th>
                      <th aria-label="Thao tác" />
                    </tr>
                  </thead>
                  <tbody>
                    {scoped.slice(0, 8).map((job) => (
                      <tr className="clickable-row" key={job.id} onClick={() => onOpen(job)}>
                        <td>
                          <div className="file-cell">
                            <Icon name="file" size={18} />
                            <div>
                              <strong title={job.source_name}>{job.source_name}</strong>
                              <small>{job.job_code}</small>
                            </div>
                          </div>
                        </td>
                        <td>
                          <div className="table-progress">
                            <span>{Math.round(job.progress)}%</span>
                            <ProgressBar value={job.progress} />
                          </div>
                        </td>
                        <td>{job.processing_mode}</td>
                        <td>
                          <StatusBadge tone={statusTone(job.status)}>
                            {statusLabel(job.status)}
                          </StatusBadge>
                        </td>
                        <td>{formatDate(job.updated_at)}</td>
                        <td className="row-action-cell">
                          <button
                            aria-label={`Xóa ${job.source_name}`}
                            className="icon-button icon-button-danger"
                            onClick={(event) => {
                              event.stopPropagation();
                              deletion.request(job);
                            }}
                            title="Xóa job"
                            type="button"
                          >
                            <Icon name="trash" size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <aside className="card attention-card">
            <h2>Việc cần xử lý</h2>
            <div className="attention-list">
              <div>
                <span className="attention-icon attention-blue">
                  <Icon name="shield" size={22} />
                </span>
                <div>
                  <strong>{review}</strong>
                  <p>job chờ kiểm duyệt</p>
                </div>
              </div>
              <div>
                <span className="attention-icon attention-orange">
                  <Icon name="clock" size={22} />
                </span>
                <div>
                  <strong>{processing}</strong>
                  <p>job đang xử lý</p>
                </div>
              </div>
              <div>
                <span className="attention-icon attention-red">
                  <Icon name="alert" size={22} />
                </span>
                <div>
                  <strong>{failed}</strong>
                  <p>job thất bại cần kiểm tra</p>
                </div>
              </div>
            </div>
            <p className="backend-note">
              Thống kê record confidence thấp và nghi trùng chưa có endpoint tổng hợp nên không hiển
              thị tại đây.
            </p>
          </aside>
        </div>
      )}

      <ConfirmDialog
        busy={deletion.busy}
        description={
          <>
            Xóa job <strong>{deletion.pending?.source_name}</strong>? Toàn bộ dữ liệu và bằng
            chứng sẽ bị xóa vĩnh viễn, không thể hoàn tác.
            {deletion.error && <span className="modal-error">{deletion.error}</span>}
          </>
        }
        onCancel={deletion.cancel}
        onConfirm={deletion.confirm}
        open={deletion.pending !== null}
        title="Xóa job này?"
      />
    </section>
  );
}
