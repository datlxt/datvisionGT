import { useState } from "react";

import { api } from "../lib/api";
import type { GtCaseItem, GtCaseStatus, GtCompareResponse } from "../types";
import { Icon } from "./Icon";
import { StatusBadge } from "./ui";

const STATUS_LABEL: Record<GtCaseStatus, string> = {
  match: "Khớp",
  diff: "Lệch",
  extra: "Model thừa",
  missed: "GT thiếu",
};

const STATUS_TONE: Record<GtCaseStatus, "success" | "warning" | "danger" | "neutral"> = {
  match: "success",
  diff: "warning",
  extra: "danger",
  missed: "neutral",
};

function pct(value: number | undefined): string {
  return value === undefined ? "—" : `${Math.round(value * 1000) / 10}%`;
}

export function GtCompareDialog({
  jobId,
  onClose,
  onApplied,
}: {
  jobId: string;
  onClose: () => void;
  onApplied: () => void;
}) {
  const [result, setResult] = useState<GtCompareResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function upload(file: File) {
    setBusy(true);
    setError("");
    const form = new FormData();
    form.append("file", file);
    api<GtCompareResponse>(`/api/v1/jobs/${jobId}/gt-compare`, { method: "POST", body: form })
      .then(setResult)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không đọc được file GT."),
      )
      .finally(() => setBusy(false));
  }

  const appliable = (result?.items ?? []).filter(
    (item) => (item.status === "match" || item.status === "diff") && item.track_id && item.gt_plate,
  );
  const autoVerified = appliable.filter((item) => item.status === "match").length;

  function apply() {
    setBusy(true);
    setError("");
    const items = appliable.map((item) => ({
      track_id: item.track_id,
      gt_text: item.gt_plate,
      verify: item.status === "match",
    }));
    api(`/api/v1/jobs/${jobId}/gt-apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ items }),
    })
      .then(() => {
        onApplied();
        onClose();
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không áp dụng được."),
      )
      .finally(() => setBusy(false));
  }

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()} role="presentation">
      <div
        aria-label="Đối chiếu GT"
        aria-modal="true"
        className="modal-card gt-compare-card"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <header className="gt-compare-head">
          <h3>Đối chiếu file GT</h3>
          <button className="icon-button" onClick={onClose} title="Đóng" type="button">
            <Icon name="x" size={18} />
          </button>
        </header>

        {!result ? (
          <div className="gt-compare-upload">
            <p>
              Tải file GT gốc (.xlsx, cột <strong>From–To</strong> + <strong>License Plate
              expected</strong>). Hệ thống tự so khớp theo thời gian, điền GT và tính
              Precision/Recall.
            </p>
            <label className="button button-primary">
              <Icon name="upload" size={18} /> Chọn file GT
              <input
                accept=".xlsx,.xlsm"
                disabled={busy}
                hidden
                onChange={(event) => {
                  const file = event.target.files?.[0];
                  if (file) upload(file);
                }}
                type="file"
              />
            </label>
            {busy && <p className="backend-note">Đang đối chiếu…</p>}
          </div>
        ) : (
          <>
            <div className="gt-metrics">
              <div>
                <span>Precision</span>
                <strong>{pct(result.detection.precision)}</strong>
              </div>
              <div>
                <span>Recall</span>
                <strong>{pct(result.detection.recall)}</strong>
              </div>
              <div>
                <span>Đúng biển (khớp)</span>
                <strong>{pct(result.recognition.exact_accuracy_on_matched)}</strong>
              </div>
              <div>
                <span>CER</span>
                <strong>{pct(result.recognition.character_error_rate)}</strong>
              </div>
              <div>
                <span>GT / Model</span>
                <strong>
                  {result.gt_events} / {result.model_events}
                </strong>
              </div>
            </div>

            <div className="gt-compare-table-wrap">
              <table className="data-table gt-compare-table">
                <thead>
                  <tr>
                    <th>Trạng thái</th>
                    <th>Model đọc</th>
                    <th>GT</th>
                    <th>Phân loại</th>
                  </tr>
                </thead>
                <tbody>
                  {result.items.map((item: GtCaseItem, index) => (
                    <tr key={`${item.track_code ?? "gt"}-${index}`}>
                      <td>
                        <StatusBadge tone={STATUS_TONE[item.status]}>
                          {STATUS_LABEL[item.status]}
                        </StatusBadge>
                      </td>
                      <td>{item.model_plate || "—"}</td>
                      <td>{item.gt_plate ?? "—"}</td>
                      <td>{item.quality ?? "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {error && <p className="modal-error">{error}</p>}
            <div className="modal-actions">
              <button className="button button-secondary" disabled={busy} onClick={onClose} type="button">
                Đóng
              </button>
              <button
                className="button button-primary"
                disabled={busy || appliable.length === 0}
                onClick={apply}
                type="button"
              >
                {busy
                  ? "Đang áp dụng…"
                  : `Điền GT ${appliable.length} case · xác nhận ${autoVerified} khớp`}
              </button>
            </div>
          </>
        )}
        {!result && error && <p className="modal-error">{error}</p>}
      </div>
    </div>
  );
}
