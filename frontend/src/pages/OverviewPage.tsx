import { Icon } from "../components/Icon";
import {
  ConfirmDialog,
  EmptyState,
  PageHeader,
  ProgressBar,
  StatusBadge,
} from "../components/ui";
import { formatDate, statusLabel, statusTone } from "../lib/format";
import { useJobDeletion } from "../lib/useJobDeletion";
import type { Job } from "../types";

export function OverviewPage({
  jobs,
  onCreate,
  onDelete,
  onOpen,
}: {
  jobs: Job[];
  onCreate: () => void;
  onDelete: (job: Job) => Promise<void>;
  onOpen: (job: Job) => void;
}) {
  const deletion = useJobDeletion(onDelete);

  const processing = jobs.filter((job) =>
    ["PENDING", "QUEUED", "PROCESSING"].includes(job.status),
  ).length;
  const review = jobs.filter((job) => job.status === "WAITING_FOR_REVIEW").length;
  const completed = jobs.filter((job) => job.status === "COMPLETED").length;
  const failed = jobs.filter((job) => job.status === "FAILED").length;

  const kpis = [
    { label: "Tổng job", value: jobs.length, icon: "layers" as const, tone: "blue" },
    { label: "Đang xử lý", value: processing, icon: "clock" as const, tone: "blue" },
    { label: "Chờ kiểm duyệt", value: review, icon: "shield" as const, tone: "orange" },
    { label: "Đã hoàn thành", value: completed, icon: "check" as const, tone: "green" },
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

      <div className="kpi-grid">
        {kpis.map((item) => (
          <article className="card kpi-card" key={item.label}>
            <span className={`kpi-icon kpi-${item.tone}`}>
              <Icon name={item.icon} size={25} />
            </span>
            <div>
              <p>{item.label}</p>
              <strong>{item.value.toLocaleString("vi-VN")}</strong>
            </div>
          </article>
        ))}
      </div>

      <div className="overview-grid">
        <section className="card table-card">
          <header>
            <div>
              <h2>Job gần đây</h2>
              <p>Dữ liệu được lấy trực tiếp từ API processing jobs.</p>
            </div>
          </header>
          {jobs.length === 0 ? (
            <EmptyState
              action={
                <button className="button button-primary" onClick={onCreate} type="button">
                  Tạo job đầu tiên
                </button>
              }
              description="Chọn một video xe máy qua trạm để bắt đầu."
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
                  {jobs.slice(0, 8).map((job) => (
                    <tr key={job.id}>
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
                      <td>
                        <div className="row-actions">
                          <button
                            aria-label={`Mở ${job.source_name}`}
                            className="icon-button"
                            onClick={() => onOpen(job)}
                            title="Mở job"
                            type="button"
                          >
                            <Icon name="arrow" size={18} />
                          </button>
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
