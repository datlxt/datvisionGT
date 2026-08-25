import { useEffect, useState } from "react";

import { CondensedList } from "../components/CondensedList";
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

export function OverviewPage({
  jobs,
  onCreate,
  onDelete,
  onOpen,
  onSendCondenseToCreate,
}: {
  jobs: Job[];
  onCreate: (scope: Scope) => void;
  onDelete: (job: Job) => Promise<void>;
  onOpen: (job: Job) => void;
  onSendCondenseToCreate: (item: CondenseStatus) => void;
}) {
  const deletion = useJobDeletion(onDelete);
  const [scope, setScope] = useState<Scope>("video");
  const [condenseList, setCondenseList] = useState<CondenseStatus[]>([]);
  const [condenseReload, setCondenseReload] = useState(0);
  const [page, setPage] = useState(1);
  const PER_PAGE = 4;
  // Reset to the first page whenever the user switches Video / Xe máy / Ô tô.
  useEffect(() => setPage(1), [scope]);

  useEffect(() => {
    api<CondenseStatus[]>("/api/v1/condense")
      .then(setCondenseList)
      .catch(() => setCondenseList([]));
  }, [condenseReload]);

  const isVideo = scope === "video";
  const scoped = isVideo ? jobs : jobs.filter((job) => job.vehicle_type === scope);

  const processing = scoped.filter((job) =>
    ["PENDING", "QUEUED", "PROCESSING"].includes(job.status),
  ).length;
  const review = scoped.filter((job) => job.status === "WAITING_FOR_REVIEW").length;
  const completed = scoped.filter((job) => job.status === "COMPLETED").length;

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
        { label: "Tổng số phiên", value: scoped.length, icon: "layers", tone: "blue" },
        { label: "Đang xử lý", value: processing, icon: "clock", tone: "blue" },
        { label: "Chờ kiểm duyệt", value: review, icon: "shield", tone: "orange" },
        { label: "Đã hoàn thành", value: completed, icon: "check", tone: "green" },
      ];

  // Pagination here is only for the job tables (Xe máy / Ô tô); the Video tab uses CondensedList,
  // which paginates itself.
  const jobPageCount = Math.max(1, Math.ceil(scoped.length / PER_PAGE));
  const pagedJobs = scoped.slice((page - 1) * PER_PAGE, page * PER_PAGE);
  useEffect(() => {
    if (page > jobPageCount) setPage(jobPageCount);
  }, [page, jobPageCount]);

  const pagination = (pageCount: number) => (
      <div className="pagination pagination-end">
        <div className="pagination-controls">
          <button
            className="pagination-btn"
            disabled={page <= 1}
            onClick={() => setPage((value) => Math.max(1, value - 1))}
            type="button"
          >
            <span className="pagination-flip">
              <Icon name="arrow" size={15} />
            </span>
            Trước
          </button>
          <span className="pagination-page">
            Trang {page} / {pageCount}
          </span>
          <button
            className="pagination-btn"
            disabled={page >= pageCount}
            onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
            type="button"
          >
            Sau
            <Icon name="arrow" size={15} />
          </button>
        </div>
      </div>
    );

  return (
    <section className="page overview-page">
      <PageHeader
        action={
          <button
            className="button button-primary"
            onClick={() => onCreate(scope)}
            type="button"
          >
            <Icon name={isVideo ? "scissors" : "plus"} size={19} />{" "}
            {isVideo ? "Cắt video mới" : "Tạo phiên mới"}
          </button>
        }
        description="Theo dõi tiến độ xử lý video và các phiên đang chờ kiểm duyệt."
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
                description="Nhấn “Cắt video mới” ở góc trên để bắt đầu."
                title="Chưa có video đã cắt"
              />
            ) : (
              <div className="overview-condensed">
                {/* Reuse the same list as the Cắt video page → clicking a row opens the detail
                    popup (xem lại video, tải, xoá, chuyển sang tạo GT), not just a page jump. */}
                <CondensedList
                  mode="manage"
                  onDeleted={() => setCondenseReload((value) => value + 1)}
                  onSendToGt={onSendCondenseToCreate}
                  reloadKey={condenseReload}
                />
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
            </div>
            <p className="backend-note">
              Video đã cắt có thể tải về hoặc đưa thẳng vào làm GT ở tab Cắt video.
            </p>
          </aside>
        </div>
      ) : (
        <div className="overview-grid overview-grid-full">
          <section className="card table-card">
            <header>
              <div>
                <h2>Phiên gần đây</h2>
                <p>Dữ liệu cập nhật trực tiếp từ hệ thống xử lý.</p>
              </div>
            </header>
            {scoped.length === 0 ? (
              <EmptyState
                description="Nhấn “Tạo phiên mới” ở góc trên để bắt đầu."
                title="Chưa có phiên xử lý"
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
                    {pagedJobs.map((job) => (
                      <tr className="clickable-row" key={job.id} onClick={() => onOpen(job)}>
                        <td>
                          <div className="file-cell">
                            <Icon name="file" size={18} />
                            <div>
                              <strong title={job.source_name}>{job.source_name}</strong>
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
                            title="Xóa phiên"
                            type="button"
                          >
                            <Icon name="trash" size={17} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
                {pagination(jobPageCount)}
              </div>
            )}
          </section>
        </div>
      )}

      <ConfirmDialog
        busy={deletion.busy}
        description={
          <>
            Xóa phiên <strong>{deletion.pending?.source_name}</strong>? Toàn bộ dữ liệu và bằng
            chứng sẽ bị xóa vĩnh viễn, không thể hoàn tác.
            {deletion.error && <span className="modal-error">{deletion.error}</span>}
          </>
        }
        onCancel={deletion.cancel}
        onConfirm={deletion.confirm}
        open={deletion.pending !== null}
        title="Xóa phiên này?"
      />
    </section>
  );
}
