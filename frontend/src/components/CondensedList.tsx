import { useEffect, useState } from "react";

import { api } from "../lib/api";
import { formatTime } from "../lib/format";
import type { CondenseStatus } from "../types";
import { Icon } from "./Icon";
import { ConfirmDialog, EmptyState } from "./ui";

function formatCutDate(iso: string | null | undefined): string {
  if (!iso) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "—";
  return date.toLocaleString("vi-VN", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * The list of already-condensed videos.
 *  - mode="manage": browse, re-watch in a popup, download, or delete (used on the Cắt video page).
 *  - mode="pick":   choose one to feed into the create-job flow (used on the Tạo job page).
 * `reloadKey` lets a parent force a refetch (e.g. right after a new cut finishes).
 */
export function CondensedList({
  mode,
  onPick,
  onSendToGt,
  onDeleted,
  picking = false,
  reloadKey = 0,
}: {
  mode: "manage" | "pick";
  onPick?: (item: CondenseStatus) => void;
  onSendToGt?: (item: CondenseStatus) => void;
  onDeleted?: (id: string) => void;
  picking?: boolean;
  reloadKey?: number;
}) {
  const [items, setItems] = useState<CondenseStatus[] | null>(null);
  const [reload, setReload] = useState(0);
  const [openId, setOpenId] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<CondenseStatus | null>(null);

  useEffect(() => {
    let active = true;
    api<CondenseStatus[]>("/api/v1/condense")
      .then((list) => {
        if (active) setItems(list.filter((entry) => entry.status === "completed"));
      })
      .catch(() => {
        if (active) setItems([]);
      });
    return () => {
      active = false;
    };
  }, [reload, reloadKey]);

  const open = items?.find((entry) => entry.id === openId) ?? null;

  function remove(item: CondenseStatus) {
    // Optimistic: drop the row from the list immediately so it disappears the instant you confirm,
    // then delete on the server in the background. Raw fetch (not api()) because DELETE returns 204
    // with an EMPTY body, which api() would try to JSON-parse and throw on.
    setItems((current) => current?.filter((entry) => entry.id !== item.id) ?? current);
    setConfirmDelete(null);
    setOpenId(null);
    // Tell the parent so anything ELSE showing this same cut (e.g. the result card above the list)
    // clears too, instead of lingering until a manual reload.
    onDeleted?.(item.id);
    fetch(`/api/v1/condense/${item.id}`, { method: "DELETE" })
      .then((response) => {
        if (!response.ok && response.status !== 404) throw new Error(`HTTP ${response.status}`);
      })
      .catch(() => setReload((value) => value + 1)); // failed — refetch to restore the row
  }

  if (items === null) return null;

  if (items.length === 0) {
    if (mode === "pick") {
      return (
        <EmptyState
          description="Hãy cắt một video ở trang “Cắt video” trước, rồi quay lại đây chọn."
          title="Chưa có video đã cắt"
        />
      );
    }
    return null;
  }

  return (
    <>
      <ul className="condensed-list">
        {items.map((item) => (
          <li className="condensed-row" key={item.id}>
            <button
              className="condensed-row-main"
              disabled={picking}
              onClick={() => (mode === "pick" ? onPick?.(item) : setOpenId(item.id))}
              type="button"
            >
              <span className="condensed-row-icon">
                <Icon name="scissors" size={16} />
              </span>
              <div className="condensed-row-copy">
                <strong title={item.source_name ?? undefined}>{item.source_name ?? "video"}</strong>
                <small>
                  {formatCutDate(item.created_at)} · giữ {formatTime(item.condensed_duration_ms ?? 0)}{" "}
                  · bỏ {formatTime(item.cut_ms ?? 0)} · {item.segment_count ?? 0} đoạn
                </small>
              </div>
              <Icon name={mode === "pick" ? "arrow" : "eye"} size={16} />
            </button>
            {mode === "manage" && (
              <button
                aria-label={`Xoá ${item.source_name ?? "video"}`}
                className="icon-button icon-button-danger"
                onClick={() => setConfirmDelete(item)}
                title="Xoá video đã cắt"
                type="button"
              >
                <Icon name="trash" size={17} />
              </button>
            )}
          </li>
        ))}
      </ul>

      {open && (
        <div className="modal-overlay" onClick={() => setOpenId(null)} role="presentation">
          <div
            aria-label="Video đã cắt"
            aria-modal="true"
            className="modal-card condensed-detail"
            onClick={(event) => event.stopPropagation()}
            role="dialog"
          >
            <header className="condensed-detail-head">
              <div>
                <h3 title={open.source_name ?? undefined}>{open.source_name ?? "Video đã cắt"}</h3>
                <small>Cắt lúc {formatCutDate(open.created_at)}</small>
              </div>
              <button
                aria-label="Đóng"
                className="icon-button"
                onClick={() => setOpenId(null)}
                type="button"
              >
                <Icon name="x" size={18} />
              </button>
            </header>

            <video
              className="condense-preview"
              controls
              preload="metadata"
              src={`/api/v1/condense/${open.id}/download`}
            />

            <div className="condense-stats">
              <div>
                <span>Video gốc</span>
                <strong>{formatTime(open.source_duration_ms ?? 0)}</strong>
              </div>
              <div>
                <span>Sau khi cắt</span>
                <strong>{formatTime(open.condensed_duration_ms ?? 0)}</strong>
              </div>
              <div className="condense-stat-highlight">
                <span>Đã bỏ đi</span>
                <strong>{formatTime(open.cut_ms ?? 0)}</strong>
              </div>
              <div>
                <span>Số đoạn giữ</span>
                <strong>{open.segment_count ?? 0}</strong>
              </div>
            </div>

            <div className="condense-actions">
              {onSendToGt && (
                <button
                  className="button button-primary"
                  onClick={() => onSendToGt(open)}
                  type="button"
                >
                  Chuyển sang tạo GT
                  <Icon name="arrow" size={18} />
                </button>
              )}
              <a className="button button-secondary" href={`/api/v1/condense/${open.id}/download`}>
                <Icon name="download" size={18} /> Tải video về
              </a>
              <button
                className="button button-danger"
                onClick={() => setConfirmDelete(open)}
                type="button"
              >
                <Icon name="trash" size={17} /> Xoá
              </button>
            </div>
          </div>
        </div>
      )}

      <ConfirmDialog
        confirmLabel="Xoá"
        description={
          <>
            Xoá <strong>{confirmDelete?.source_name}</strong>? Video đã cắt và toàn bộ dữ liệu liên
            quan sẽ bị xoá vĩnh viễn, không thể hoàn tác.
          </>
        }
        onCancel={() => setConfirmDelete(null)}
        onConfirm={() => confirmDelete && remove(confirmDelete)}
        open={confirmDelete !== null}
        title="Xoá video đã cắt?"
      />
    </>
  );
}
