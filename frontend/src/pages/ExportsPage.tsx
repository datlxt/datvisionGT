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
const PER_PAGE = 8;

function isFailed(job: Job): boolean {
  return job.status === "FAILED" || job.status === "CANCELLED";
}

export function ExportsPage({
  jobs,
  onDelete,
  onOpen,
}: {
  jobs: Job[];
  onDelete: (job: Job) => Promise<void>;
  onOpen: (job: Job) => void;
}) {
  const readyCount = jobs.filter(isReadyForReview).length;
  const deletion = useJobDeletion(onDelete);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<ExportFilter>("ALL");
  const [page, setPage] = useState(1);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return jobs.filter((job) => {
      const matchesQuery =
        !needle ||
        job.source_name.toLowerCase().includes(needle) ||
        job.job_code.toLowerCase().includes(needle);
      if (!matchesQuery) return false;
      if (filter === "READY") return isReadyForReview(job);
      if (filter === "FAILED") return isFailed(job);
      if (filter === "PROCESSING") return !isReadyForReview(job) && !isFailed(job);
      return true;
    });
  }, [jobs, query, filter]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PER_PAGE));
  // Keep the current page valid when the filter/search shrinks the result set.
  useEffect(() => {
    if (page > totalPages) setPage(1);
  }, [page, totalPages]);
  const pageJobs = filtered.slice((page - 1) * PER_PAGE, page * PER_PAGE);

  return (
    <section className="page exports-page">
      <PageHeader
        description={`${readyCount} job có kết quả model sẵn sàng tải xuống.`}
        title="Kết quả & Export"
      />

      <div className="export-notice card">
        <Icon name="database" size={22} />
        <div>
          <strong>2 loại file — đừng nhầm:</strong>
          <p>
            🟢 <strong>Plate Report (bản nháp)</strong>: TẤT CẢ case model + Confidence + ảnh crop —
            để <strong>soát / đối chiếu</strong>.{"  "}
            🔵 <strong>GT Final (bản chốt)</strong>: CHỈ case đã <strong>VERIFIED</strong>, layout
            benchmark + Precision/Recall tự tính — <strong>bộ GT bàn giao</strong> để chấm model khác.
          </p>
        </div>
      </div>

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
          <div className="filter-tabs">
            {(
              [
                ["ALL", "Tất cả", jobs.length],
                ["READY", "Sẵn sàng", readyCount],
                ["PROCESSING", "Đang xử lý", jobs.filter((j) => !isReadyForReview(j) && !isFailed(j)).length],
                ["FAILED", "Lỗi", jobs.filter(isFailed).length],
              ] as [ExportFilter, string, number][]
            ).map(([value, label, count]) => (
              <button
                className={filter === value ? "active" : ""}
                key={value}
                onClick={() => setFilter(value)}
                type="button"
              >
                {label} <span>{count}</span>
              </button>
            ))}
          </div>
        </div>
      )}

      <section className="card table-card">
        <header>
          <div>
            <h2>Danh sách job</h2>
            <p>Trạng thái và tiến độ lấy từ processing jobs.</p>
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
                  <th>Tiến độ</th>
                  <th>Trạng thái</th>
                  <th>Cập nhật</th>
                  <th>Bản nháp — để soát</th>
                  <th>GT cuối — bàn giao</th>
                  <th aria-label="Xóa" />
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
                          title="Bản nháp: MỌI case (trừ đã Loại), GT tạm = biển model đọc. Cùng bố cục với GT Final."
                        >
                          <Icon name="download" size={16} /> GT (đang làm)
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
                    <td>
                      {isReadyForReview(job) ? (
                        <a
                          className="button button-blue button-compact export-btn"
                          href={`/api/v1/jobs/${job.id}/export/final.xlsx`}
                          title="Bản chốt: CHỈ case đã VERIFIED — bộ GT chuẩn để bàn giao / chấm model khác"
                        >
                          <Icon name="download" size={16} /> GT Final
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
              </tbody>
            </table>
          </div>
        )}
        {filtered.length > PER_PAGE && (
          <div className="pagination">
            <span className="pagination-info">
              {(page - 1) * PER_PAGE + 1}–{Math.min(page * PER_PAGE, filtered.length)} /{" "}
              {filtered.length} job
            </span>
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
