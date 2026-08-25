import { useEffect, useState } from "react";

import { Icon } from "./Icon";

const MAX_LEN = 30;

// Rename the DISPLAY name of a job (used in the review header + export list). Only the label changes;
// evidence/data are untouched. Validates: trimmed, not empty, at most MAX_LEN characters.
export function RenameJobDialog({
  open,
  initialName,
  onClose,
  onSave,
}: {
  open: boolean;
  initialName: string;
  onClose: () => void;
  onSave: (name: string) => Promise<void>;
}) {
  const [name, setName] = useState(initialName);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (open) {
      setName(initialName);
      setError("");
      setBusy(false);
    }
  }, [open, initialName]);

  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busy) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, busy, onClose]);

  if (!open) return null;

  const trimmed = name.trim();
  const invalid = trimmed.length === 0 || trimmed.length > MAX_LEN;

  async function save() {
    const value = name.trim();
    if (!value) {
      setError("Tên không được để trống.");
      return;
    }
    if (value.length > MAX_LEN) {
      setError(`Tên tối đa ${MAX_LEN} ký tự.`);
      return;
    }
    setBusy(true);
    setError("");
    try {
      await onSave(value);
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Không đổi được tên.");
      setBusy(false);
    }
  }

  return (
    <div className="modal-overlay" onClick={() => !busy && onClose()} role="presentation">
      <div
        aria-label="Đổi tên phiên"
        aria-modal="true"
        className="modal-card"
        onClick={(event) => event.stopPropagation()}
        role="dialog"
      >
        <span className="modal-icon">
          <Icon name="edit" size={22} />
        </span>
        <h3>Đổi tên phiên</h3>
        <p>Chỉ đổi tên hiển thị (dùng khi xuất GT) — không ảnh hưởng dữ liệu hay bằng chứng.</p>
        <form
          className="rename-form"
          onSubmit={(event) => {
            event.preventDefault();
            if (!invalid && !busy) save();
          }}
        >
          <input
            autoFocus
            className="rename-input"
            maxLength={MAX_LEN}
            onChange={(event) => setName(event.target.value)}
            placeholder="Nhập tên phiên"
            value={name}
          />
          <div className="rename-meta">
            <span className={error ? "rename-error" : ""}>
              {error || `${trimmed.length}/${MAX_LEN} ký tự`}
            </span>
          </div>
          <div className="modal-actions">
            <button className="button button-secondary" disabled={busy} onClick={onClose} type="button">
              Hủy
            </button>
            <button className="button button-primary" disabled={busy || invalid} type="submit">
              {busy ? "Đang lưu…" : "Lưu"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
