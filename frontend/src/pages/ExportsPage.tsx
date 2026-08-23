import { useEffect, useMemo, useState } from "react";

import { Icon } from "../components/Icon";
import {
  ConfirmDialog,
  EmptyState,
  PageHeader,
  ProgressBar,
  StatusBadge,
} from "../components/ui";
import {
  formatDate,
  isReadyForReview,
  statusLabel,
  statusTone,
} from "../lib/format";
import { useJobDeletion } from "../lib/useJobDeletion";
import type { Job } from "../types";

type ExportFilter = "ALL" | "READY" | "PROCESSING" | "FAILED";
const PER_PAGE = 4;

function isFailed(job: Job): boolean {
  return job.status === "FAILED" || job.status === "CANCELLED";
}

export function ExportsPage({
  jobs,
  onDelete,
  onFlag,
  onOpen,
}: {
  jobs: Job[];
  onDelete: (job: Job) => Promise<void>;
  onFlag: (job: Job, flagged: boolean) => Promise<void>;
  onOpen: (job: Job) => void;
}) {
  const deletion = useJobDeletion(onDelete);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ExportFilter>("ALL");
  const [vehicleFilter, setVehicleFilter] = useState<"all" | "motorcycle" | "car">("all");
  const [page, setPage] = useState(1);

  // Shared predicates so the filter-button counts stay CONSISTENT with the cross-filter that's on:
  // each group counts within what the OTHER group (+ search) already narrowed to.
  const needle = query.trim().toLowerCase();
  const okQuery = (job: Job) =>
    !needle ||
    job.source_name.toLowerCase().includes(needle) ||
    job.job_code.toLowerCase().includes(needle);
  const okStatus = (job: Job, f: ExportFilter) => {
    if (f === "READY") return isReadyForReview(job);
    if (f === "FAILED") return isFailed(job);
    if (f === "PROCESSING") return !isReadyForReview(job) && !isFailed(job);
    return true;
  };
  const okVehicle = (job: Job, v: "all" | "motorcycle" | "car") =>
    v === "all" || job.vehicle_type === v;

  const filtered = useMemo(
    () => jobs.filter((job) => okQuery(job) && okStatus(job, filter) && okVehicle(job, vehicleFilter)),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [jobs, query, filter, vehicleFilter],
  );
  // Status counts respect the vehicle filter; vehicle counts respect the status filter.
  const statusCount = (f: ExportFilter) =>
    jobs.filter((job) => okQuery(job) && okVehicle(job, vehicleFilter) && okStatus(job, f)).length;
  const vehicleCount = (v: "motorcycle" | "car") =>
    jobs.filter((job) => okQuery(job) && okStatus(job, filter) && okVehicle(job, v)).length;

  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  // Keep the current page valid when the filter/search shrinks the result set.
  useEffect(() => {
    if (page > totalPages) setPage(1);
  }, [page, totalPages]);
  const pageJobs = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

  return (
    <section className="page exports-page">
      <PageHeader
        description={`${jobs.filter(isReadyForReview).length} job có kết quả model sẵn sàng tải xuống.`}
        title="Kết quả & Xuất GT"
      />

      {jobs.length > 0 && (
        <div className="review-toolbar card">
          <label>
            <Icon name="search" size={18} />
            <input
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Tìm tên job hoặc mã job…"
              value={query}
            />
          </label>
          <div className="filter-groups">
            <div className="filter-tabs">
              {(
                [
                  ["ALL", "Tất cả"],
                  ["READY", "Sẵn sàng"],
                  ["PROCESSING", "Đang xử lý"],
                  ["FAILED", "Lỗi"],
                ] as [ExportFilter, string][]
              ).map(([value, label]) => (
                <button
                  className={filter === value ? "active" : ""}
                  key={value}
                  onClick={() => setFilter(value)}
                  type="button"
                >
                  {label} <span>{statusCount(value)}</span>
                </button>
              ))}
            </div>
            <div className="filter-tabs">
              {(
                [
                  ["motorcycle", "Xe máy"],
                  ["car", "Ô tô"],
                ] as ["motorcycle" | "car", string][]
              ).map(([value, label]) => (
                <button
                  className={vehicleFilter === value ? "active" : ""}
                  key={value}
                  onClick={() =>
                    setVehicleFilter((current) => (current === value ? "all" : value))
                  }
                  type="button"
                >
                  {label} <span>{vehicleCount(value)}</span>
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      <section className="card table-card">
        <header>
          <div>
            <h2>Danh sách kết quả</h2>
            <p>Trạng thái, tiến độ và nút xuất GT của từng job.</p>
          </div>
        </header>
        {jobs.length === 0 ? (
          <EmptyState
            description="Kết quả sẽ xuất hiện sau khi một job được tạo."
            title="Chưa có kết quả"
          />
        ) : filtered.length === 0 ? (
          <EmptyState
            description="Thử đổi bộ lọc hoặc từ khóa tìm kiếm khác."
            title="Không có job khớp"
          />
        ) : (
          <div className="data-table-wrap">
            <table className="data-table export-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Loại xe</th>
                  <th>Tiến độ</th>
                  <th>Trạng thái</th>
                  <th>Cập nhật</th>
                  <th>Xuất GT</th>
                  <th className="th-center">Đánh dấu</th>
                  <th className="th-center">Xóa</th>
                </tr>
              </thead>
              <tbody>
                {pageJobs.map((job) => (
                  <tr key={job.id}>
                    <td>
                      <button className="table-link" onClick={() => onOpen(job)} type="button">
                        <strong>{job.source_name}</strong>
                        <small>{job.job_code}</small>
                      </button>
                    </td>
                    <td>
                      <StatusBadge tone={job.vehicle_type === "car" ? "info" : "neutral"}>
                        {job.vehicle_type === "car" ? "Ô tô" : "Xe máy"}
                      </StatusBadge>
                    </td>
                    <td>
                      <div className="table-progress">
                        <span>{Math.round(job.progress)}%</span>
                        <ProgressBar value={job.progress} />
                      </div>
                    </td>
                    <td>
                      <StatusBadge tone={statusTone(job.status)}>
                        {statusLabel(job.status)}
                      </StatusBadge>
                    </td>
                    <td>{formatDate(job.updated_at)}</td>
                    <td>
                      {isReadyForReview(job) ? (
                        <a
                          className="button button-primary button-compact export-btn"
                          href={`/api/v1/jobs/${job.id}/export.xlsx`}
                          title="Xuất trạng thái GT hiện tại của job (mọi lượt xe, kèm GT + mức độ nhận diện đã điền tới giờ)"
                        >
                          <Icon name="download" size={16} /> Xuất Excel
                        </a>
                      ) : (
                        <button
                          className="button button-secondary button-compact"
                          disabled
                          type="button"
                        >
                          Chưa sẵn sàng
                        </button>
                      )}
                    </td>
                    <td className="export-flag-cell">
                      <button
                        aria-label={
                          job.flagged
                            ? `Bỏ đánh dấu ${job.source_name}`
                            : `Đánh dấu quan trọng ${job.source_name}`
                        }
                        aria-pressed={job.flagged}
                        className={`flag-btn${job.flagged ? " is-flagged" : ""}`}
                        onClick={() => onFlag(job, !job.flagged).catch(() => undefined)}
                        title={
                          job.flagged
                            ? "Đã đánh dấu quan trọng — bấm để bỏ"
                            : "Đánh dấu quan trọng để ghi nhớ"
                        }
                        type="button"
                      >
                        <Icon name="star" size={18} />
                      </button>
                    </td>
                    <td className="export-delete-cell">
                      <div className="row-actions">
                        <button
                          aria-label={`Xóa ${job.source_name}`}
                          className="icon-button icon-button-danger"
                          onClick={() => deletion.request(job)}
                          title="Xóa job"
                          type="button"
                        >
                          <Icon name="trash" size={17} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))}
                {Array.from({ length: PER_PAGE - pageJobs.length }).map((_, index) => (
                  <tr aria-hidden="true" className="spacer-row" key={`sp-${index}`}>
                    <td colSpan={8} />
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {filtered.length > PER_PAGE && (
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
                Trang {page} / {totalPages}
              </span>
              <button
                className="pagination-btn"
                disabled={page >= totalPages}
                onClick={() => setPage((value) => Math.min(totalPages, value + 1))}
                type="button"
              >
                Sau
                <Icon name="arrow" size={15} />
              </button>
            </div>
          </div>
        )}
      </section>

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
