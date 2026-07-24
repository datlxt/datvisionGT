import { Icon } from "../components/Icon";
import { EmptyState, PageHeader, ProgressBar, StatusBadge } from "../components/ui";
import {
  formatDate,
  isReadyForReview,
  statusLabel,
  statusTone,
} from "../lib/format";
import type { Job } from "../types";

export function ExportsPage({
  jobs,
  onOpen,
}: {
  jobs: Job[];
  onOpen: (job: Job) => void;
}) {
  const readyCount = jobs.filter(isReadyForReview).length;

  return (
    <section className="page exports-page">
      <PageHeader
        description={`${readyCount} job có kết quả model sẵn sàng tải xuống.`}
        title="Kết quả & Export"
      />

      <div className="export-notice card">
        <Icon name="database" size={22} />
        <div>
          <strong>Hiện backend hỗ trợ export kết quả model dạng CSV.</strong>
          <p>
            GT Draft/GT Final và lịch sử export chưa có API tương ứng nên không được giả lập trên
            giao diện.
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
                  <th>Kết quả model</th>
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
                          className="button button-secondary button-compact"
                          href={`/api/v1/jobs/${job.id}/export.csv`}
                        >
                          <Icon name="download" size={16} /> CSV
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
                      <button
                        className="button button-secondary button-compact"
                        disabled
                        title="Backend chưa có API GT Final"
                        type="button"
                      >
                        Chưa có API
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </section>
  );
}
