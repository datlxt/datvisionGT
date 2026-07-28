import { useEffect, useMemo, useRef, useState } from "react";

import { GtCompareDialog } from "../components/GtCompareDialog";
import { Icon } from "../components/Icon";
import { MissedCaseDialog } from "../components/MissedCaseDialog";
import {
  EmptyState,
  ErrorState,
  LoadingState,
  PageHeader,
  StatusBadge,
} from "../components/ui";
import { api } from "../lib/api";
import {
  formatTime,
  isSuspectedNoPlate,
  resultLabel,
} from "../lib/format";
import type {
  EventResult,
  GroundTruthList,
  GroundTruthRecord,
  Job,
  ResultList,
} from "../types";

const VERIFY_LABEL: Record<GroundTruthRecord["verify_status"], string> = {
  UNVERIFIED: "Chưa kiểm duyệt",
  IN_REVIEW: "Đang xem",
  VERIFIED: "Đã xác nhận",
  DISCARDED: "Đã loại",
};

function verifyTone(status: GroundTruthRecord["verify_status"]) {
  if (status === "VERIFIED") return "success" as const;
  if (status === "DISCARDED") return "duplicate" as const;
  return "neutral" as const;
}

const QUALITY_OPTIONS = [
  "Biển đẹp bình thường",
  "Biển bẩn",
  "Biển cũ, xước, mờ",
  "Biển bị che vật lý",
  "Biển bị dán (icon, decal, trang trí...)",
  "Biển bị chói sáng",
  "Biển giả mạo (che 1 vài số, dán biển khác lên...)",
  "Biển biến dạng vật lý (cong, vênh)",
  "Biển bóng của vật thể che khuất",
  "Xe không biển",
];

function patchGt(recordId: string, body: Record<string, unknown>) {
  return api<GroundTruthRecord>(`/api/v1/ground-truth/${recordId}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

function actionGt(recordId: string, action: "verify" | "discard" | "restore") {
  return api<GroundTruthRecord>(`/api/v1/ground-truth/${recordId}/${action}`, { method: "POST" });
}

type Filter = "ALL" | "RECOGNIZED" | "REVIEW" | "NO_PLATE" | "CHECK";

function confidence(value: number | null) {
  return value === null ? "Chưa có dữ liệu" : `${Math.round(value * 100)}%`;
}

function EvidenceBox({
  bbox,
  width,
  height,
  label,
  tone,
}: {
  bbox: [number, number, number, number];
  width: number;
  height: number;
  label: string;
  tone: "vehicle" | "plate";
}) {
  const [x1, y1, x2, y2] = bbox;
  return (
    <span
      className={`evidence-bbox bbox-${tone}`}
      style={{
        left: `${(x1 / width) * 100}%`,
        top: `${(y1 / height) * 100}%`,
        width: `${((x2 - x1) / width) * 100}%`,
        height: `${((y2 - y1) / height) * 100}%`,
      }}
    >
      <b>{label}</b>
    </span>
  );
}

function classificationTone(event: EventResult) {
  if (event.classification === "RECOGNIZED") return "success" as const;
  if (event.classification === "NO_PLATE") return "warning" as const;
  if (isSuspectedNoPlate(event)) return "duplicate" as const;
  return "neutral" as const;
}

function toClock(ms: number): string {
  const total = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function ReviewPage({ job }: { job: Job }) {
  const [results, setResults] = useState<ResultList | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [filter, setFilter] = useState<Filter>("ALL");
  const [query, setQuery] = useState("");
  const [error, setError] = useState("");
  const [reloadToken, setReloadToken] = useState(0);
  const [gt, setGt] = useState<GroundTruthList | null>(null);
  const [gtReload, setGtReload] = useState(0);
  const [showCompare, setShowCompare] = useState(false);
  const [evidenceTab, setEvidenceTab] = useState<"frame" | "video">("frame");
  const [autoThreshold, setAutoThreshold] = useState(95);
  const [autoBusy, setAutoBusy] = useState(false);
  const [autoResult, setAutoResult] = useState<number | null>(null);
  const [videoMs, setVideoMs] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [showMissed, setShowMissed] = useState(false);

  useEffect(() => {
    api<ResultList>(`/api/v1/jobs/${job.id}/results`)
      .then((payload) => {
        setResults(payload);
        setSelectedId(payload.events[0]?.track_id ?? null);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không thể tải kết quả."),
      );
  }, [job.id, reloadToken]);

  useEffect(() => {
    api<GroundTruthList>(`/api/v1/jobs/${job.id}/ground-truth`)
      .then(setGt)
      .catch(() => setGt(null));
  }, [job.id, gtReload]);

  const gtByTrack = useMemo(() => {
    const map = new Map<string, GroundTruthRecord>();
    gt?.items.forEach((item) => map.set(item.record.track_id, item.record));
    return map;
  }, [gt]);

  const filteredEvents = useMemo(() => {
    if (!results) return [];
    const normalizedQuery = query.trim().toUpperCase();
    return results.events.filter((event) => {
      const matchesQuery =
        !normalizedQuery ||
        event.track_code.toUpperCase().includes(normalizedQuery) ||
        (event.normalized_plate ?? "").toUpperCase().includes(normalizedQuery);
      if (!matchesQuery) return false;
      if (filter === "RECOGNIZED") return event.classification === "RECOGNIZED";
      if (filter === "NO_PLATE") return event.classification === "NO_PLATE";
      if (filter === "REVIEW") {
        return (
          event.classification === "LOW_CONFIDENCE" ||
          event.classification === "UNREADABLE"
        );
      }
      if (filter === "CHECK") {
        return gtByTrack.get(event.track_id)?.verify_status !== "VERIFIED";
      }
      return true;
    });
  }, [filter, query, results, gtByTrack]);

  const needCheckCount =
    results?.events.filter(
      (event) => gtByTrack.get(event.track_id)?.verify_status !== "VERIFIED",
    ).length ?? 0;

  const totalMs = Math.max(
    job.duration_ms ?? 0,
    ...(results?.events.map((event) => event.end_timestamp_ms) ?? [0]),
    1,
  );

  // Gaps between consecutive cases where no vehicle was published — the prime spots to
  // scrub for a missed vehicle instead of watching the whole clip.
  const suspectedGaps = useMemo(() => {
    const ordered = [...(results?.events ?? [])].sort(
      (a, b) => a.start_timestamp_ms - b.start_timestamp_ms,
    );
    const gaps: { start: number; end: number }[] = [];
    let cursor = 0;
    for (const event of ordered) {
      if (event.start_timestamp_ms - cursor >= 6_000) {
        gaps.push({ start: cursor, end: event.start_timestamp_ms });
      }
      cursor = Math.max(cursor, event.end_timestamp_ms);
    }
    if (totalMs - cursor >= 6_000) gaps.push({ start: cursor, end: totalMs });
    return gaps;
  }, [results, totalMs]);

  function seekVideo(ms: number) {
    if (videoRef.current) {
      setEvidenceTab("video");
      videoRef.current.currentTime = ms / 1000;
    }
  }

  function seekFromClick(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    seekVideo(ratio * totalMs);
  }

  function runAutoVerify() {
    setAutoBusy(true);
    api<{ verified: number }>(`/api/v1/jobs/${job.id}/ground-truth/auto-verify`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ min_confidence: autoThreshold / 100 }),
    })
      .then((result) => {
        setGtReload((value) => value + 1);
        setAutoResult(result.verified);
      })
      .catch((reason: unknown) =>
        window.alert(reason instanceof Error ? reason.message : "Không tự duyệt được."),
      )
      .finally(() => setAutoBusy(false));
  }

  const selected =
    results?.events.find((event) => event.track_id === selectedId) ??
    filteredEvents[0] ??
    null;
  const selectedIndex = selected
    ? filteredEvents.findIndex((event) => event.track_id === selected.track_id)
    : -1;
  const selectedGt = selected ? gtByTrack.get(selected.track_id) ?? null : null;
  const suspected = results?.events.filter(isSuspectedNoPlate).length ?? 0;
  const selectedStartMs = selected?.start_timestamp_ms ?? 0;

  useEffect(() => {
    if (evidenceTab === "video" && videoRef.current) {
      videoRef.current.currentTime = selectedStartMs / 1000;
    }
  }, [selectedStartMs, evidenceTab]);

  if (error) {
    return (
      <section className="page">
        <ErrorState
          message={error}
          onRetry={() => {
            setResults(null);
            setError("");
            setReloadToken((value) => value + 1);
          }}
        />
      </section>
    );
  }
  if (!results) {
    return (
      <section className="page">
        <LoadingState label="Đang tải evidence và kết quả model…" />
      </section>
    );
  }

  return (
    <section className="page review-page">
      <PageHeader
        action={
          <a className="button button-primary" href={`/api/v1/jobs/${job.id}/export.xlsx`}>
            <Icon name="download" size={18} /> Export Excel
          </a>
        }
        description={`${results.total} case model · ${results.counts.RECOGNIZED ?? 0} đọc được · ${
          results.counts.NO_PLATE ?? 0
        } không biển · ${suspected} nghi không biển`}
        eyebrow={job.job_code}
        title={job.source_name}
      />

      <div className="review-actions card">
        <div className="auto-verify">
          <label htmlFor="auto-th">Tự duyệt ≥</label>
          <input
            id="auto-th"
            max={100}
            min={50}
            onChange={(event) => setAutoThreshold(Number(event.target.value))}
            type="number"
            value={autoThreshold}
          />
          <span>%</span>
          <button
            className="button button-primary button-compact"
            disabled={autoBusy}
            onClick={runAutoVerify}
            type="button"
          >
            <Icon name="check" size={16} /> {autoBusy ? "Đang duyệt…" : "Tự duyệt"}
          </button>
        </div>
        <div className="review-actions-spacer" />
        <button
          className="button button-secondary button-compact"
          onClick={() => setShowMissed(true)}
          type="button"
        >
          <Icon name="plus" size={16} /> Bổ sung case bỏ sót
        </button>
        <button
          className="button button-secondary button-compact"
          onClick={() => setShowCompare(true)}
          type="button"
        >
          <Icon name="upload" size={16} /> Đối chiếu file GT
        </button>
      </div>

      <div className="review-toolbar card">
        <label>
          <Icon name="search" size={18} />
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm TrackID hoặc biển số…"
            value={query}
          />
        </label>
        <div className="filter-tabs">
          {[
            ["ALL", "Tất cả", results.total],
            ["RECOGNIZED", "Đọc được", results.counts.RECOGNIZED ?? 0],
            [
              "REVIEW",
              "Cần xem lại",
              (results.counts.LOW_CONFIDENCE ?? 0) + (results.counts.UNREADABLE ?? 0),
            ],
            ["NO_PLATE", "Không biển", results.counts.NO_PLATE ?? 0],
            ["CHECK", "Cần kiểm tra", needCheckCount],
          ].map(([value, label, count]) => (
            <button
              className={filter === value ? "active" : ""}
              key={value}
              onClick={() => setFilter(value as Filter)}
              type="button"
            >
              {label} <span>{count}</span>
            </button>
          ))}
        </div>
      </div>

      {selected ? (
        <div className="review-grid">
          <aside className="card track-panel">
            <header>
              <div>
                <h2>Danh sách Track</h2>
                <span>{filteredEvents.length}</span>
              </div>
              <p>Mỗi track là một lượt xe do model tạo.</p>
            </header>
            <div className="track-list">
              {filteredEvents.map((event) => (
                <button
                  className={`track-item ${event.track_id === selected.track_id ? "active" : ""}`}
                  key={event.track_id}
                  onClick={() => setSelectedId(event.track_id)}
                  type="button"
                >
                  <img
                    alt={`Evidence ${event.track_code}`}
                    src={event.plate_crop_url ?? event.vehicle_crop_url}
                  />
                  <span>
                    <strong>{resultLabel(event)}</strong>
                    <small>{event.track_code}</small>
                    <small>
                      {formatTime(event.start_timestamp_ms)} · {confidence(event.confidence)}
                    </small>
                  </span>
                  <StatusBadge tone={classificationTone(event)}>
                    {event.classification === "RECOGNIZED" ? "OCR" : "Xem"}
                  </StatusBadge>
                </button>
              ))}
            </div>
          </aside>

          <section className="card evidence-panel">
            <div className="evidence-tabs">
              <button
                className={evidenceTab === "frame" ? "active" : ""}
                onClick={() => setEvidenceTab("frame")}
                type="button"
              >
                Full frame
              </button>
              <button
                className={evidenceTab === "video" ? "active" : ""}
                onClick={() => setEvidenceTab("video")}
                type="button"
              >
                Video
              </button>
              <button disabled title="Không có API frame lân cận" type="button">
                Frame lân cận
              </button>
              <span>Evidence Viewer</span>
            </div>

            {evidenceTab === "video" ? (
              <div className="evidence-video">
                <video
                  controls
                  onTimeUpdate={(event) =>
                    setVideoMs(Math.round(event.currentTarget.currentTime * 1000))
                  }
                  ref={videoRef}
                  src={`/api/v1/jobs/${job.id}/source`}
                />
                <div className="case-timeline" title="Bản đồ case theo thời gian">
                  <span className="ct-playhead" style={{ left: `${(videoMs / totalMs) * 100}%` }} />
                  {results.events.map((event) => (
                    <button
                      className={`ct-seg ct-${event.classification.toLowerCase()}`}
                      key={event.track_id}
                      onClick={() => seekVideo(event.start_timestamp_ms)}
                      style={{
                        left: `${(event.start_timestamp_ms / totalMs) * 100}%`,
                        width: `${Math.max(
                          0.5,
                          ((event.end_timestamp_ms - event.start_timestamp_ms) / totalMs) * 100,
                        )}%`,
                      }}
                      title={`${event.track_code} · ${formatTime(event.start_timestamp_ms)}`}
                      type="button"
                    />
                  ))}
                  {suspectedGaps.map((gap) => (
                    <button
                      className="ct-gap"
                      key={gap.start}
                      onClick={() => seekVideo(gap.start)}
                      style={{
                        left: `${(gap.start / totalMs) * 100}%`,
                        width: `${((gap.end - gap.start) / totalMs) * 100}%`,
                      }}
                      title={`Khoảng trống ${formatTime(gap.start)}–${formatTime(gap.end)} · nghi bỏ sót`}
                      type="button"
                    />
                  ))}
                </div>
                <div className="ct-labels">
                  <span>0:00</span>
                  <span className="ct-now">▶ {formatTime(videoMs)}</span>
                  <span>{formatTime(totalMs)}</span>
                </div>
                {suspectedGaps.length > 0 && (
                  <div className="ct-gaps-list">
                    <span className="ct-gaps-title">
                      <span className="ct-gap-legend" /> Nghi bỏ sót ({suspectedGaps.length}):
                    </span>
                    {suspectedGaps.map((gap) => (
                      <button
                        className="ct-gap-chip"
                        key={gap.start}
                        onClick={() => seekVideo(gap.start)}
                        type="button"
                      >
                        {formatTime(gap.start)}–{formatTime(gap.end)}
                      </button>
                    ))}
                  </div>
                )}
                <p className="backend-note">
                  Vạch màu = xe đã bắt (click nhảy tới) · gạch chéo đỏ / chip đỏ = khoảng trống nghi
                  bỏ sót. Con trỏ ▲ = vị trí video hiện tại.
                </p>
              </div>
            ) : (
            <div className="full-frame">
              <img alt={`Full frame ${selected.track_code}`} src={selected.full_frame_url} />
              <span className="frame-stamp">
                {formatTime(selected.best_timestamp_ms)} · Frame{" "}
                {selected.best_frame_number.toLocaleString("vi-VN")}
              </span>
              {job.width && job.height && (
                <>
                  <EvidenceBox
                    bbox={selected.vehicle_bbox}
                    height={job.height}
                    label={selected.track_code}
                    tone="vehicle"
                    width={job.width}
                  />
                  {selected.plate_bbox && (
                    <EvidenceBox
                      bbox={selected.plate_bbox}
                      height={job.height}
                      label={selected.normalized_plate ?? "PLATE"}
                      tone="plate"
                      width={job.width}
                    />
                  )}
                </>
              )}
            </div>
            )}

            <div className="evidence-bottom">
              <div className="best-crop">
                <span>{selected.plate_crop_url ? "Crop biển số" : "Crop xe"}</span>
                <img
                  alt={`Crop ${selected.track_code}`}
                  src={selected.plate_crop_url ?? selected.vehicle_crop_url}
                />
              </div>
              <dl className="metadata-grid">
                <div>
                  <dt>TrackID</dt>
                  <dd>{selected.track_code}</dd>
                </div>
                <div>
                  <dt>Timestamp</dt>
                  <dd>{formatTime(selected.best_timestamp_ms)}</dd>
                </div>
                <div>
                  <dt>BBox xe</dt>
                  <dd>{selected.vehicle_bbox.join(", ")}</dd>
                </div>
                <div>
                  <dt>BBox biển</dt>
                  <dd>{selected.plate_bbox?.join(", ") ?? "Không phát hiện"}</dd>
                </div>
                <div>
                  <dt>Số detection xe</dt>
                  <dd>{selected.vehicle_detection_count}</dd>
                </div>
                <div>
                  <dt>Số detection biển</dt>
                  <dd>{selected.plate_detection_count}</dd>
                </div>
              </dl>
            </div>
            <footer className="evidence-footer">
              <Icon name="shield" size={16} /> No evidence, no record.
            </footer>
          </section>

          <aside className="card gt-panel">
            <section className="prediction-section">
              <div className="panel-section-title">
                <div>
                  <span>Prediction</span>
                  <h2>Kết quả model</h2>
                </div>
                <StatusBadge tone={classificationTone(selected)}>
                  {selected.classification}
                </StatusBadge>
              </div>
              <strong className="prediction-value">{resultLabel(selected)}</strong>
              <dl>
                <div>
                  <dt>Raw OCR</dt>
                  <dd>{selected.raw_plate ?? "Không có"}</dd>
                </div>
                <div>
                  <dt>OCR vote confidence</dt>
                  <dd>{confidence(selected.confidence)}</dd>
                </div>
                <div>
                  <dt>Plate detection</dt>
                  <dd>{confidence(selected.plate_confidence)}</dd>
                </div>
                <div>
                  <dt>Vehicle detection</dt>
                  <dd>{confidence(selected.vehicle_confidence)}</dd>
                </div>
                <div>
                  <dt>Quality score</dt>
                  <dd>{confidence(selected.quality_score)}</dd>
                </div>
              </dl>
              {selected.quality_flags.length > 0 && (
                <div className="quality-flags">
                  {selected.quality_flags.map((flag) => (
                    <span key={flag}>{flag}</span>
                  ))}
                </div>
              )}
            </section>

            <GtPanel
              key={selected.track_id}
              record={selectedGt}
              onChanged={() => setGtReload((value) => value + 1)}
            />

            <footer className="review-navigation">
              <button
                disabled={selectedIndex <= 0}
                onClick={() => setSelectedId(filteredEvents[selectedIndex - 1].track_id)}
                type="button"
              >
                ← Record trước
              </button>
              <button
                disabled={selectedIndex < 0 || selectedIndex >= filteredEvents.length - 1}
                onClick={() => setSelectedId(filteredEvents[selectedIndex + 1].track_id)}
                type="button"
              >
                Record tiếp →
              </button>
            </footer>
          </aside>
        </div>
      ) : (
        <div className="card">
          <EmptyState
            description="Hãy thay đổi bộ lọc hoặc kiểm tra lại kết quả pipeline."
            title="Không có track phù hợp"
          />
        </div>
      )}

      {showCompare && (
        <GtCompareDialog
          jobId={job.id}
          onApplied={() => setGtReload((value) => value + 1)}
          onClose={() => setShowCompare(false)}
        />
      )}

      {autoResult !== null && (
        <div className="modal-overlay" onClick={() => setAutoResult(null)} role="presentation">
          <div
            aria-label="Kết quả tự duyệt"
            aria-modal="true"
            className="modal-card"
            onClick={(event) => event.stopPropagation()}
            role="alertdialog"
          >
            <span className="modal-icon modal-icon-success">
              <Icon name="check" size={26} />
            </span>
            <h3>Đã tự duyệt xong</h3>
            <p>
              Tự duyệt <strong>{autoResult}</strong> case đọc được (OCR vote ≥ {autoThreshold}%).
              Còn <strong>{needCheckCount}</strong> case ở tab{" "}
              <strong>&quot;Cần kiểm tra&quot;</strong> cần bạn soát tay bằng video.
            </p>
            <div className="modal-actions">
              <button
                className="button button-secondary"
                onClick={() => setAutoResult(null)}
                type="button"
              >
                Đóng
              </button>
              <button
                className="button button-primary"
                disabled={needCheckCount === 0}
                onClick={() => {
                  setFilter("CHECK");
                  setAutoResult(null);
                }}
                type="button"
              >
                Xem “Cần kiểm tra” ({needCheckCount})
              </button>
            </div>
          </div>
        </div>
      )}

      {showMissed && (
        <MissedCaseDialog
          defaultTimestamp={toClock(
            evidenceTab === "video" && videoRef.current
              ? videoRef.current.currentTime * 1000
              : selectedStartMs,
          )}
          jobId={job.id}
          onAdded={() => {
            setReloadToken((value) => value + 1);
            setGtReload((value) => value + 1);
          }}
          onClose={() => setShowMissed(false)}
        />
      )}
    </section>
  );
}

function GtPanel({
  record,
  onChanged,
}: {
  record: GroundTruthRecord | null;
  onChanged: () => void;
}) {
  const [gtText, setGtText] = useState(record?.gt_text ?? record?.predicted_text ?? "");
  const [note, setNote] = useState(record?.note ?? "");
  const [quality, setQuality] = useState(record?.classification ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!record) {
    return (
      <section className="ground-truth-section">
        <div className="panel-section-title">
          <div>
            <span>Ground Truth</span>
            <h2>Kiểm duyệt của con người</h2>
          </div>
        </div>
        <p className="backend-note">Đang tạo GT draft cho track này…</p>
      </section>
    );
  }

  const run = (task: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    task()
      .then(() => onChanged())
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không lưu được kiểm duyệt."),
      )
      .finally(() => setBusy(false));
  };

  const save = () =>
    run(() => patchGt(record.id, { gt_text: gtText, note, classification: quality }));
  const verify = () =>
    run(async () => {
      await patchGt(record.id, { gt_text: gtText, note, classification: quality });
      await actionGt(record.id, "verify");
    });
  const discard = () => run(() => actionGt(record.id, "discard"));
  const restore = () => run(() => actionGt(record.id, "restore"));

  const isDiscarded = record.verify_status === "DISCARDED";
  const isVerified = record.verify_status === "VERIFIED";

  return (
    <section className="ground-truth-section">
      <div className="panel-section-title">
        <div>
          <span>Ground Truth</span>
          <h2>Kiểm duyệt của con người</h2>
        </div>
        <StatusBadge tone={verifyTone(record.verify_status)}>
          {VERIFY_LABEL[record.verify_status]}
        </StatusBadge>
      </div>
      <label>
        GT Plate
        <input
          onChange={(event) => setGtText(event.target.value.toUpperCase())}
          placeholder="Nhập biển số đúng"
          value={gtText}
        />
      </label>
      <label>
        Phân loại chất lượng biển (nhìn crop)
        <select onChange={(event) => setQuality(event.target.value)} value={quality}>
          <option value="">— Chọn phân loại —</option>
          {QUALITY_OPTIONS.map((option) => (
            <option key={option} value={option}>
              {option}
            </option>
          ))}
        </select>
      </label>
      <label>
        Ghi chú kiểm duyệt
        <textarea
          onChange={(event) => setNote(event.target.value)}
          placeholder="Ghi chú (tuỳ chọn)"
          value={note}
        />
      </label>
      {error && <p className="backend-note">{error}</p>}
      <div className="gt-actions">
        <button className="button button-secondary" disabled={busy} onClick={save} type="button">
          Lưu nháp
        </button>
        {isDiscarded ? (
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={restore}
            type="button"
          >
            Khôi phục
          </button>
        ) : (
          <button
            className="button button-secondary"
            disabled={busy}
            onClick={discard}
            type="button"
          >
            Loại (Discard)
          </button>
        )}
      </div>
      <button
        className="button button-primary button-block"
        disabled={busy || isVerified || !gtText.trim()}
        onClick={verify}
        type="button"
      >
        <Icon name="check" size={18} /> {isVerified ? "Đã xác nhận" : "Xác nhận GT"}
      </button>
      <p className="backend-note">
        Predicted: {record.predicted_text ?? "—"} · v{record.version} · reviewer mặc định.
      </p>
    </section>
  );
}
