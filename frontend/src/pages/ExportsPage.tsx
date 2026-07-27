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

  return (
    <section className="page exports-page">
      <PageHeader
        description={`${readyCount} job có kết quả model sẵn sàng tải xuống.`}
        title="Kết quả & Export"
      />

      <div className="export-notice card">
        <Icon name="database" size={22} />
        <div>
          <strong>Export Excel "Plate Report" — đúng format kiểm duyệt cho QC/tester.</strong>
          <p>
            Mỗi dòng một xe: ảnh crop biển số nhúng sẵn, biển model đọc, Start/End, Confidence,
            Frame # và cột GT Plate / Kết quả QA để reviewer đối chiếu. GT Final tổng hợp chưa có API.
          </p>
        </div>
      </div>

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
        ) : (
          <div className="data-table-wrap">
            <table className="data-table export-table">
              <thead>
                <tr>
                  <th>Job</th>
                  <th>Tiến độ</th>
                  <th>Trạng thái</th>
                  <th>Cập nhật</th>
                  <th>Export</th>
                  <th>GT Final</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => (
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
                          className="button button-primary button-compact"
                          href={`/api/v1/jobs/${job.id}/export.xlsx`}
                        >
                          <Icon name="download" size={16} /> Excel (Plate Report)
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
                      <div className="row-actions">
                        {isReadyForReview(job) ? (
                          <a
                            className="button button-secondary button-compact"
                            href={`/api/v1/jobs/${job.id}/export/final.xlsx`}
                            title="Chỉ gồm case đã VERIFIED"
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
