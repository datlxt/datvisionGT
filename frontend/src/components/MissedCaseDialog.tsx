import { useState } from "react";

import { api } from "../lib/api";
import { Icon } from "./Icon";

function parseTimestamp(value: string): number | null {
  const parts = value.trim().split(":").map((part) => Number(part));
  if (parts.some((part) => Number.isNaN(part))) return null;
  if (parts.length === 2) return (parts[0] * 60 + parts[1]) * 1000;
  if (parts.length === 1) return parts[0] * 1000;
  if (parts.length === 3) return (parts[0] * 3600 + parts[1] * 60 + parts[2]) * 1000;
  return null;
}

export function MissedCaseDialog({
  jobId,
  defaultTimestamp = "",
  onClose,
  onAdded,
}: {
  jobId: string;
  defaultTimestamp?: string;
  onClose: () => void;
  onAdded: () => void;
}) {
  const [noPlate, setNoPlate] = useState(false);
  const [plate, setPlate] = useState("");
  const [timestamp, setTimestamp] = useState(defaultTimestamp);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  function submit() {
    const ms = parseTimestamp(timestamp);
    if (ms === null) {
      setError("Thời điểm phải dạng mm:ss (vd 6:59).");
      return;
    }
    if (!noPlate && !plate.trim()) {
      setError("Nhập biển số, hoặc chọn Xe không biển.");
      return;
    }
    setBusy(true);
    setError("");
    api(`/api/v1/jobs/${jobId}/ground-truth/manual`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        timestamp_ms: ms,
        gt_text: noPlate ? "" : plate.trim(),
        no_plate: noPlate,
        note: note.trim() || null,
      }),
    })
      .then(() => {
        onAdded();
        onClose();
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không thêm được case."),
      )
      .finally(() => setBusy(false));
  }

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()} role="presentation">
      <div
        aria-label="Bổ sung case bỏ sót"
        aria-modal="true"
        className="modal-card"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <span className="modal-icon">
          <Icon name="plus" size={22} />
        </span>
        <h3>Bổ sung case bỏ sót</h3>
        <p>
          Thêm 1 lượt xe model bỏ sót. Thời điểm điền sẵn theo vị trí video đang tua; case được
          neo vào frame evidence thật gần thời điểm đó nhất.
        </p>

        <div className="missed-form">
          <label className="missed-check">
            <input
              checked={noPlate}
              onChange={(event) => setNoPlate(event.target.checked)}
              type="checkbox"
            />
            Xe không biển
          </label>
          {!noPlate && (
            <label>
              Biển số (GT)
              <input
                onChange={(event) => setPlate(event.target.value.toUpperCase())}
                placeholder="VD 29AF95701"
                value={plate}
              />
            </label>
          )}
          <label>
            Thời điểm (mm:ss)
            <input
              onChange={(event) => setTimestamp(event.target.value)}
              placeholder="VD 6:59"
              value={timestamp}
            />
          </label>
          <label>
            Ghi chú
            <input
              onChange={(event) => setNote(event.target.value)}
              placeholder="Tuỳ chọn"
              value={note}
            />
          </label>
        </div>

        {error && <p className="modal-error">{error}</p>}
        <div className="modal-actions">
          <button className="button button-secondary" disabled={busy} onClick={onClose} type="button">
            Hủy
          </button>
          <button className="button button-primary" disabled={busy} onClick={submit} type="button">
            {busy ? "Đang thêm…" : "Thêm case"}
          </button>
        </div>
      </div>
    </div>
  );
}
