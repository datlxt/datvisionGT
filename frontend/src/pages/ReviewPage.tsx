import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";

import { GtCompareDialog } from "../components/GtCompareDialog";
import { Icon } from "../components/Icon";
import { MissedCaseDialog } from "../components/MissedCaseDialog";
import { RenameJobDialog } from "../components/RenameJobDialog";
import {
  ConfirmDialog,
  EmptyState,
  ErrorState,
  LoadingState,
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

// AI + reviewer now set a coarse 3-level "mức độ nhận diện" (how readable the plate is). The old
// fine defect taxonomy moved to a human-only column in the export. "Xe không biển" stays here as the
// no-plate marker (drives the sentinel GT).
const QUALITY_OPTIONS = [
  "Đọc rõ",
  "Khó đọc",
  "Không đọc được",
  "Xe không biển",
];

// The "no plate" category + the sentinel GT string the backend/export expect for a plateless
// vehicle (matches NO_PLATE_TEXT in backend export/plate_report.py).
const NO_PLATE_OPTION = "Xe không biển";
const NO_PLATE_GT = "LPN_NO_PLATE_VEHICLE";

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

type Filter = "ALL" | "RECOGNIZED" | "UNCERTAIN" | "NO_PLATE" | "CHECK";

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

// Human-readable Vietnamese label for the model's classification badge (never show the raw enum).
function classificationLabel(event: EventResult): string {
  switch (event.classification) {
    case "RECOGNIZED":
      return "Đọc được biển";
    case "LOW_CONFIDENCE":
      return "Độ tin thấp";
    case "UNREADABLE":
      return "Không đọc được";
    case "NO_PLATE":
      return "Không có biển";
    default:
      return "Cần xem lại";
  }
}

// A case the reviewer should NOT trust at face value and must eyeball: unreadable / low
// confidence, a "recognized" plate that still hides a doubtful character (occluded/glary digit
// the model misreads confidently, e.g. D->U), a single-frame read, a poor crop, OR a vehicle
// that came through with NO plate detected (needs a human to confirm it truly has none, not a
// missed plate). All of these land together in the yellow "Cần xem lại" bucket, on the tab, and
// on the video timeline — regardless of the % score.
function isRiskyRead(event: EventResult): boolean {
  // A MAJORITY cross-check (≥2 of local + AI-1 + AI-2 agree) is trusted — the agreed value is
  // auto-filled and the reader doubt is cleared. Only when the readers are genuinely SPLIT
  // (OCR_DISAGREEMENT — each a different answer) does a human have to decide the read.
  if (event.quality_flags.includes("OCR_AGREE")) return false;
  return (
    event.classification === "LOW_CONFIDENCE" ||
    event.classification === "UNREADABLE" ||
    event.classification === "NO_PLATE" ||
    event.quality_flags.includes("WEAK_CHARACTER") ||
    event.quality_flags.includes("SINGLE_READING_OCR") ||
    event.quality_flags.includes("OCR_DISAGREEMENT") ||
    (event.quality_score ?? 1) < 0.5
  );
}

// A case that still needs a human in "Cần xem lại": either the READ is risky (isRiskyRead), OR
// the plate is fine but the two AIs disagreed on the quality CATEGORY (a human must pick it, so
// an auto-verified case never silently carries an empty classification).
function needsReview(event: EventResult): boolean {
  return (
    isRiskyRead(event) ||
    event.quality_flags.includes("QUALITY_DISAGREEMENT") ||
    // The same plate string appears again far apart in the video — a human must confirm it is a
    // real re-entry (or two vehicles), not one vehicle wrongly split into duplicate rows.
    event.quality_flags.includes("REPEATED_PLATE") ||
    // Travelled against the lane direction — likely an adjacent-lane clip or mis-association.
    event.quality_flags.includes("WRONG_DIRECTION") ||
    // Special (military/diplomatic) plate the OCR wasn't trained on — always human-verified.
    event.quality_flags.includes("SPECIAL_PLATE")
  );
}

// The model produced a plate AND we trust it. The offline pipeline marks a plate read in a SINGLE
// frame as LOW_CONFIDENCE even at 99% vote confidence, but the cross-check often then CONFIRMS it
// (≥2 of 3 readers agree) — a clear plate seen once is not "uncertain". So a read is reliable when
// it is a solid RECOGNIZED read OR a cross-check-agreed one; only a genuinely unconfirmed weak /
// unreadable read stays in "Đọc chưa chắc".
function isReliableRead(event: EventResult): boolean {
  if (event.classification === "NO_PLATE" || !event.normalized_plate) return false;
  return (
    event.classification === "RECOGNIZED" || event.quality_flags.includes("OCR_AGREE")
  );
}

// The plate a MAJORITY (≥2 of local + AI-1 + AI-2) read — regardless of WHICH two. This mirrors the
// backend `_consensus_plate` that auto-fills GT: a 2/3 agreement wins even when the local model is
// the odd one out, so the default fill never depends on local. Returns null if the readers are split.
function consensusPlate(event: EventResult): string | null {
  const norm = (value: string | null | undefined) =>
    (value ?? "").toUpperCase().replace(/Đ/g, "D").replace(/[^A-Z0-9]/g, "");
  const reads = [event.normalized_plate, event.cloud_plate, event.qwen_plate]
    .map(norm)
    .filter(Boolean);
  if (reads.length === 0) return null;
  const counts = new Map<string, number>();
  for (const read of reads) counts.set(read, (counts.get(read) ?? 0) + 1);
  let best: string | null = null;
  let bestCount = 0;
  for (const [value, count] of counts) {
    if (count > bestCount) {
      best = value;
      bestCount = count;
    }
  }
  return bestCount >= 2 && 2 * bestCount > reads.length ? best : null;
}

// Turn the internal engine flags into plain Vietnamese the reviewer can act on. Flags not in
// this map (dedup / motion bookkeeping) are hidden — they are noise for a human reviewer.
const FLAG_META: Record<string, { label: string; tone: "warn" | "info" | "ok" }> = {
  WEAK_CHARACTER: { label: "Ký tự mờ/che", tone: "warn" },
  SINGLE_READING_OCR: { label: "Đọc 1 khung", tone: "warn" },
  OCR_DISAGREEMENT: { label: "AI đọc lệch", tone: "warn" },
  SUSPECTED_NON_PLATE: { label: "Nghi không phải biển", tone: "warn" },
  REPEATED_PLATE: { label: "Biển trùng lượt", tone: "warn" },
  WRONG_DIRECTION: { label: "Ngược hướng", tone: "warn" },
  SPECIAL_PLATE: { label: "Biển đặc biệt", tone: "warn" },
  QUALITY_DISAGREEMENT: { label: "Phân loại lệch", tone: "warn" },
  OCR_UNVERIFIED: { label: "Chưa kiểm chéo", tone: "info" },
  OCR_AGREE: { label: "AI khớp", tone: "ok" },
  QUALITY_AGREE: { label: "Phân loại khớp", tone: "ok" },
  AUTO_VERIFIED_REPEATED: { label: "Trùng biển — đã tự duyệt (soi lại nếu cần)", tone: "info" },
};

function friendlyFlags(flags: string[]) {
  // Only a UNANIMOUS cross-check resolves the local doubt; hide the alarming chips only then.
  const confirmed = flags.includes("OCR_UNANIMOUS");
  // The positive "AI khớp" / "Phân loại khớp" chips repeat what the cross-check card already says,
  // so they only add a row of height — always hide them and keep chips for things that need action.
  const hidden = confirmed
    ? new Set([
        "WEAK_CHARACTER",
        "SINGLE_READING_OCR",
        "QUALITY_DISAGREEMENT",
        "OCR_AGREE",
        "QUALITY_AGREE",
      ])
    : new Set<string>(["OCR_AGREE", "QUALITY_AGREE"]);
  // "Nghi không phải biển" is the specific, actionable message; the generic "AI đọc lệch"
  // and low-char-confidence chips are just noise once we know it's likely a logo/light.
  if (flags.includes("SUSPECTED_NON_PLATE")) {
    hidden.add("OCR_DISAGREEMENT");
    hidden.add("WEAK_CHARACTER");
    hidden.add("SINGLE_READING_OCR");
  }
  return flags
    .filter((flag) => flag in FLAG_META && !hidden.has(flag))
    .map((flag) => ({ flag, ...FLAG_META[flag] }));
}

// Which track corresponds to a moment in the video. Among events whose window COVERS the time,
// pick the TIGHTEST one — an over-merged plate can span minutes and would otherwise shadow the
// short pass that actually matches. If nothing covers the time (a gap), pick the nearest event
// so clicking anywhere on the bar still lands on a real plate.
function trackIdForTime(events: EventResult[], ms: number): string | null {
  if (events.length === 0) return null;
  const covering = events.filter(
    (event) => ms >= event.start_timestamp_ms && ms <= event.end_timestamp_ms,
  );
  if (covering.length > 0) {
    return covering.reduce((best, event) =>
      event.end_timestamp_ms - event.start_timestamp_ms <
      best.end_timestamp_ms - best.start_timestamp_ms
        ? event
        : best,
    ).track_id;
  }
  const gap = (event: EventResult) =>
    Math.min(
      Math.abs(event.start_timestamp_ms - ms),
      Math.abs(event.end_timestamp_ms - ms),
    );
  return events.reduce((best, event) => (gap(event) < gap(best) ? event : best)).track_id;
}

// The consensus card: shows the local model + the two AI readers side by side with one clear
// verdict, so a first-time reviewer instantly sees whether to trust the read or look closer.
function CrossCheckCard({ event }: { event: EventResult }) {
  const ran = event.cloud_plate != null || event.qwen_plate != null;
  const unverified = event.quality_flags.includes("OCR_UNVERIFIED");
  const disagree = event.quality_flags.includes("OCR_DISAGREEMENT");
  const unanimous = event.quality_flags.includes("OCR_UNANIMOUS");
  const agree = event.quality_flags.includes("OCR_AGREE");
  const nonPlate = event.quality_flags.includes("SUSPECTED_NON_PLATE");
  // A trustworthy match (all / most sources agree) stays COLLAPSED — the one-line verdict is enough,
  // the plate is already shown big above. Only a case that needs the reviewer's eyes (readers split /
  // logo / not yet checked) opens the 3 source rows by default. The chevron toggles either way.
  const needsAttention = disagree || nonPlate || unverified;
  const [open, setOpen] = useState(needsAttention);
  if (!ran && !unverified) return null;

  // Reading is cross-checked by 3 sources: the local OCR + AI-1 + AI-2. AI-3 is a
  // classification-only tie-breaker, so it does NOT appear as a reading source here.
  const rows = [
    { name: "Model (máy)", value: event.normalized_plate || event.raw_plate || "—" },
    { name: "AI-1", value: event.cloud_plate },
    { name: "AI-2", value: event.qwen_plate },
  ].filter((row) => row.value != null);

  const verdict = unverified
    ? { cls: "info", icon: "clock" as const, text: "Chưa đối chiếu được." }
    : nonPlate
      ? { cls: "diff", icon: "alert" as const, text: "AI không xác nhận được biển số — nghi logo/tem dán. Chọn \"Xe không biển\" hoặc Loại bỏ." }
      : disagree
      ? { cls: "diff", icon: "alert" as const, text: "3 nguồn đọc khác nhau — vui lòng chọn biển số đúng." }
      : unanimous
        ? { cls: "same", icon: "check" as const, text: "3 nguồn khớp — kết quả đáng tin cậy." }
        : agree
          ? { cls: "warn", icon: "check" as const, text: "2/3 nguồn khớp — đã điền sẵn, kiểm tra lại nếu cần." }
          : { cls: "info", icon: "eye" as const, text: "Đã đối chiếu với AI." };

  return (
    <div className={`crosscheck crosscheck-${verdict.cls}`}>
      <button
        aria-expanded={open}
        className="crosscheck-head"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        <span className="crosscheck-title">
          <Icon name="shield" size={15} /> Đối chiếu AI (đọc lại biển)
        </span>
        <span className={`cc-chevron${open ? " cc-chevron-open" : ""}`}>
          <Icon name="chevron" size={16} />
        </span>
      </button>
      <div className="crosscheck-verdict">
        <Icon name={verdict.icon} size={15} /> {verdict.text}
      </div>
      {open && (
        <div className="crosscheck-rows">
          {rows.map((row) => (
            <div className="crosscheck-row" key={row.name}>
              <span>{row.name}</span>
              <strong>{row.value || "không đọc được"}</strong>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function toClock(ms: number): string {
  const total = Math.floor(Math.max(0, ms) / 1000);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function ReviewPage({
  job,
  onRename,
}: {
  job: Job;
  onRename: (job: Job, name: string) => Promise<void>;
}) {
  const [renaming, setRenaming] = useState(false);
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
  // Magnifier loupe over the full frame — follows the cursor so hard plates can be read closely.
  const [loupe, setLoupe] = useState<{
    left: number;
    top: number;
    bgX: number;
    bgY: number;
    bgW: number;
    bgH: number;
  } | null>(null);
  const [hoverTrack, setHoverTrack] = useState<{
    event: EventResult;
    anchorY: number; // vertical CENTER of the hovered row — the popup is centered on this, then clamped
    left: number;
  } | null>(null);
  const popupRef = useRef<HTMLDivElement>(null);
  const [hoverTop, setHoverTop] = useState(0);
  // Center the popup on the hovered row, then clamp so it never runs off the top/bottom of the
  // screen — the last cases used to overflow because the popup was anchored at the row's TOP with a
  // too-small height guess. Measured after render (and after the crop image loads) for exactness.
  useLayoutEffect(() => {
    if (!hoverTrack || !popupRef.current) return;
    const height = popupRef.current.offsetHeight;
    const top = Math.max(12, Math.min(hoverTrack.anchorY - height / 2, window.innerHeight - height - 12));
    setHoverTop(top);
  }, [hoverTrack]);
  // Success toast lives HERE (not in GtPanel): GtPanel remounts on save (its key changes), which
  // would drop a toast owned by it. This parent survives, so the confirmation actually shows.
  const [saveToast, setSaveToast] = useState<string | null>(null);
  useEffect(() => {
    if (!saveToast) return;
    const timer = setTimeout(() => setSaveToast(null), 5000);
    return () => clearTimeout(timer);
  }, [saveToast]);
  // The "sửa khác model" heads-up also lives here so both toasts share one stack (no overlap).
  const [diffToast, setDiffToast] = useState<string | null>(null);
  useEffect(() => {
    if (!diffToast) return;
    const timer = setTimeout(() => setDiffToast(null), 6000);
    return () => clearTimeout(timer);
  }, [diffToast]);
  const [crossBusy, setCrossBusy] = useState(false);
  const [crossResult, setCrossResult] = useState<{
    checked: number;
    agree: number;
    disagree: number;
    unverified: number;
    auto_verified?: number;
    error?: string;
  } | null>(null);
  const [videoMs, setVideoMs] = useState(0);
  const videoRef = useRef<HTMLVideoElement>(null);
  const trackListRef = useRef<HTMLDivElement>(null);
  // A manual pick (list click / prev-next) wins for a moment so the playback-follow effect can't
  // yank the selection back — a video seek snaps to the nearest keyframe, which can land just
  // before the track's window, where an over-merged wide window would otherwise re-capture it.
  const manualSelectUntil = useRef(0);
  const [showMissed, setShowMissed] = useState(false);

  useEffect(() => {
    api<ResultList>(`/api/v1/jobs/${job.id}/results`)
      .then((payload) => {
        setResults(payload);
        setSelectedId((current) => current ?? payload.events[0]?.track_id ?? null);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không thể tải kết quả."),
      );
  }, [job.id, reloadToken]);

  // Auto-refresh while the background AI cross-check OR the missed-vehicle recall runs, so their
  // results (agreement flags, then the refined "nghi bỏ sót" gaps) appear on their own. The recall
  // runs right after the cross-check finishes, so keep polling until it is done too.
  useEffect(() => {
    const ccStatus = results?.cross_check?.status;
    const missedStatus = results?.missed_scan?.status;
    const ccActive = ccStatus === "pending" || ccStatus === "running";
    // Recall is queued/running (set "pending" the moment the cross-check task starts) → keep polling
    // until it reports "done"/"error". Legacy jobs never get this key, so they don't poll.
    const missedActive = missedStatus === "pending" || missedStatus === "running";
    if (!ccActive && !missedActive) return;
    const timer = setTimeout(() => setReloadToken((value) => value + 1), 4000);
    return () => clearTimeout(timer);
  }, [results]);

  // Kick the missed-vehicle recall on its own the first time we open a job that never ran it (older
  // jobs, or before the feature). It runs in the BACKGROUND — no need to touch the AI cross-check.
  const missedKicked = useRef(false);
  useEffect(() => {
    if (!results || missedKicked.current) return;
    if (results.missed_scan == null) {
      missedKicked.current = true;
      api(`/api/v1/jobs/${job.id}/missed-scan`, { method: "POST" })
        .then(() => setReloadToken((value) => value + 1))
        .catch(() => undefined);
    }
  }, [results, job.id]);

  // Two-way sync: while the video plays/seeks, auto-select the track that matches the current
  // position (tightest covering window, else nearest) so the left-column crop follows the screen.
  useEffect(() => {
    if (evidenceTab !== "video" || !results) return;
    if (Date.now() < manualSelectUntil.current) return; // a fresh manual pick must not be undone
    // Only FOLLOW while the video is actually PLAYING. When it is paused (the user is browsing the
    // list or just clicked a case), a stray time-update must never yank the selection to another
    // track — that was the "click a case, it jumps to a different one" bug.
    if (videoRef.current?.paused) return;
    const id = trackIdForTime(results.events, videoMs);
    if (id && id !== selectedId) {
      setSelectedId(id);
    }
  }, [videoMs, evidenceTab, results, selectedId]);

  // Keep the selected plate visible: whenever the selection changes (click, playback-follow,
  // or timeline seek), smoothly scroll the left list so the corresponding crop is on screen.
  useEffect(() => {
    if (!selectedId || !trackListRef.current) return;
    const item = trackListRef.current.querySelector(`[data-track-id="${selectedId}"]`);
    item?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [selectedId]);

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
      if (filter === "RECOGNIZED") return isReliableRead(event);
      if (filter === "NO_PLATE") return event.classification === "NO_PLATE";
      // Read a plate but NOT confirmed (weak single-frame with no cross-check agreement, or
      // unreadable) — so the three "read result" tabs add up to the total without dropping a case.
      if (filter === "UNCERTAIN")
        return !isReliableRead(event) && event.classification !== "NO_PLATE";
      // "Cần xử lý" = everything not yet verified. The matched cases are already auto-verified, so
      // what remains is exactly what a human still has to handle (risky reads + no-plate + splits).
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

  // "Read result" buckets computed from the events (not the raw backend counts) so a single-frame
  // read the cross-check CONFIRMED counts as "Model ra biển", not "Đọc chưa chắc". Mutually
  // exclusive → the three sum to the total.
  const readCounts = useMemo(() => {
    const events = results?.events ?? [];
    let reliable = 0;
    let noPlate = 0;
    for (const event of events) {
      if (event.classification === "NO_PLATE") noPlate += 1;
      else if (isReliableRead(event)) reliable += 1;
    }
    return { reliable, noPlate, uncertain: events.length - reliable - noPlate };
  }, [results]);

  const totalMs = Math.max(
    job.duration_ms ?? 0,
    ...(results?.events.map((event) => event.end_timestamp_ms) ?? [0]),
    1,
  );

  // Gaps between consecutive cases where no vehicle was published — the prime spots to
  // scrub for a missed vehicle instead of watching the whole clip. Once the AI missed-vehicle
  // recall has run (missed_scan.status === "done"), we show ONLY the gaps it CONFIRMED still hold
  // a vehicle — with the exact frame + timestamp it saw — so the QC never scrubs an empty gap. If
  // the scan hasn't run / errored, we fall back to every raw time-gap (nothing is hidden).
  const missedScan = results?.missed_scan ?? null;
  const suspectedGaps = useMemo(() => {
    type Gap = {
      start: number;
      end: number;
      ts: number;
      frameUrl?: string;
      confirmed: boolean;
      plate?: string;
      hasPlate?: boolean;
      vehicleType?: string;
      inList?: boolean;
      plateElsewhereMs?: number | null;
    };
    if (missedScan?.status === "done") {
      return missedScan.candidates.map<Gap>((c) => ({
        start: c.start_ms,
        end: c.end_ms,
        ts: c.ts_ms,
        frameUrl: c.frame_url,
        confirmed: true,
        plate: c.plate,
        hasPlate: c.has_plate,
        vehicleType: c.vehicle_type,
        inList: c.in_list,
        plateElsewhereMs: c.plate_elsewhere_ms,
      }));
    }
    // While the AI recall is queued/running (OR unavailable), show the RAW detection gaps as
    // "chưa soi" markers so a reviewer can already cross-check them by hand in parallel — the bar
    // must never go blank just because the AI hasn't answered yet. Once the AI is done we swap to
    // its filtered candidates above.
    const ordered = [...(results?.events ?? [])].sort(
      (a, b) => a.start_timestamp_ms - b.start_timestamp_ms,
    );
    const gaps: Gap[] = [];
    let cursor = 0;
    for (const event of ordered) {
      if (event.start_timestamp_ms - cursor >= 6_000) {
        gaps.push({ start: cursor, end: event.start_timestamp_ms, ts: cursor, confirmed: false });
      }
      cursor = Math.max(cursor, event.end_timestamp_ms);
    }
    if (totalMs - cursor >= 6_000)
      gaps.push({ start: cursor, end: totalMs, ts: cursor, confirmed: false });
    return gaps;
  }, [missedScan, results, totalMs]);

  // Which AI-confirmed gap the reviewer clicked open (shows the AI's finding for that moment).
  const [openGapKey, setOpenGapKey] = useState<number | null>(null);
  const [gapBusy, setGapBusy] = useState(false);
  const [confirmDismissTs, setConfirmDismissTs] = useState<number | null>(null);
  // Cap the "nghi bỏ sót" chips so a video with many gaps doesn't overflow the panel; the rest is
  // one click away via "xem thêm".
  const [gapsExpanded, setGapsExpanded] = useState(false);
  const GAP_CHIP_LIMIT = 5;
  const openGap = suspectedGaps.find((gap) => gap.start === openGapKey) ?? null;
  const visibleGaps = gapsExpanded ? suspectedGaps : suspectedGaps.slice(0, GAP_CHIP_LIMIT);

  async function dismissGap(tsMs: number) {
    // Remove this gap from the "nghi bỏ sót" timeline (false positive, or already added to GT).
    await api(`/api/v1/jobs/${job.id}/missed-scan/dismiss`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ts_ms: tsMs }),
    });
  }

  async function addGapToGt(gap: NonNullable<typeof openGap>) {
    // Create an evidence-backed GT case for a real missed vehicle (anchored to the nearest frame),
    // then drop the gap so it no longer shows as pending. No plate seen → LPN_NO_PLATE_VEHICLE.
    if (gapBusy) return;
    setGapBusy(true);
    try {
      const record = await api<{ track_id: string }>(
        `/api/v1/jobs/${job.id}/ground-truth/manual`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            timestamp_ms: gap.ts,
            end_timestamp_ms: gap.end,
            gt_text: gap.hasPlate && gap.plate ? gap.plate : "",
            no_plate: !gap.hasPlate,
          }),
        },
      );
      await dismissGap(gap.ts);
      setOpenGapKey(null);
      // Jump straight to the new case: select it in the left list + move the video to that exact
      // moment. The manual-select guard stops playback-follow from yanking the selection away.
      manualSelectUntil.current = Date.now() + 3000;
      setSelectedId(record.track_id);
      seekVideo(gap.ts);
      setReloadToken((value) => value + 1);
      // Reload the GT records too — the manual case is created VERIFIED, so refreshing here makes it
      // show as verified immediately (out of "Cần xử lý", no "đang tạo GT draft" wait).
      setGtReload((value) => value + 1);
    } catch {
      /* leave the panel open so the reviewer can retry */
    } finally {
      setGapBusy(false);
    }
  }

  async function removeGap(tsMs: number) {
    if (gapBusy) return;
    setGapBusy(true);
    try {
      await dismissGap(tsMs);
      setOpenGapKey(null);
      setReloadToken((value) => value + 1);
    } catch {
      /* ignore */
    } finally {
      setGapBusy(false);
    }
  }

  function seekVideo(ms: number) {
    if (videoRef.current) {
      setEvidenceTab("video");
      videoRef.current.currentTime = ms / 1000;
    }
  }

  function seekFromClick(event: React.MouseEvent<HTMLDivElement>) {
    const rect = event.currentTarget.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
    const ms = ratio * totalMs;
    seekVideo(ms);
    // Snap the left list to the plate for THIS part of the video, so clicking anywhere on the
    // bar (including a gap) always lands on a real plate instead of leaving the list unchanged.
    const id = trackIdForTime(results?.events ?? [], ms);
    if (id) {
      setSelectedId(id);
    }
  }

  // Explicit user pick (list click / prev-next): show the video and jump it to this track's
  // start. This is the ONLY path that seeks on selection — playback-follow selection (the
  // videoMs effect) must never seek, or it would yank the playhead back every frame.
  function selectTrack(trackId: string, startMs: number) {
    manualSelectUntil.current = Date.now() + 1500;
    setSelectedId(trackId);
    // Keep whichever evidence tab the reviewer is on (frame vs video) when they move between cases —
    // don't yank them to Video. The video is still seeked below so it's positioned if they switch.
    if (videoRef.current) {
      // Clicking a case = REVIEW that case: pause and jump to its start so the selection stays put.
      // (While playing, the follow-effect would keep re-selecting the track under the playhead —
      // that was the "click a case, it jumps around / seeks away" mess.) Press play to watch.
      videoRef.current.pause();
      videoRef.current.currentTime = startMs / 1000;
    }
  }

  function runCrossCheck() {
    setCrossBusy(true);
    api<{
      checked: number;
      agree: number;
      disagree: number;
      unverified: number;
      auto_verified: number;
    }>(
      `/api/v1/jobs/${job.id}/cross-check`,
      { method: "POST" },
    )
      .then((r) => {
        setReloadToken((value) => value + 1);
        setCrossResult(r);
      })
      .catch((reason: unknown) =>
        setCrossResult({
          checked: 0,
          agree: 0,
          disagree: 0,
          unverified: 0,
          error: reason instanceof Error ? reason.message : "Không kiểm chéo được.",
        }),
      )
      .finally(() => setCrossBusy(false));
  }

  const selected =
    results?.events.find((event) => event.track_id === selectedId) ??
    filteredEvents[0] ??
    null;
  // Prev/Next walk the FULL ordered list, not the current filter tab — otherwise confirming a case
  // drops it out of a filter (e.g. "Cần xử lý"), its index becomes -1, and "Bản ghi sau" wrongly
  // disables. Navigating the full list keeps Next working after every verify.
  const navEvents = results?.events ?? [];
  const navIndex = selected
    ? navEvents.findIndex((event) => event.track_id === selected.track_id)
    : -1;
  const selectedGt = selected ? gtByTrack.get(selected.track_id) ?? null : null;
  const selectedStartMs = selected?.start_timestamp_ms ?? 0;

  // Seek only when the evidence view switches TO video, reading the latest selected start from
  // a ref. Keying the effect on `evidenceTab` alone (not selectedStartMs) stops it re-firing
  // and seeking backwards every time playback auto-selects the track under the playhead.
  const selectedStartRef = useRef(selectedStartMs);
  selectedStartRef.current = selectedStartMs;
  useEffect(() => {
    if (evidenceTab === "video" && videoRef.current) {
      videoRef.current.currentTime = selectedStartRef.current / 1000;
    }
  }, [evidenceTab]);

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
        <LoadingState label="Đang tải bằng chứng và kết quả model…" />
      </section>
    );
  }

  return (
    <section className="page review-page">
      <section className="review-controls card">
      {/* Title (vehicle type · date · filename) lives INSIDE the top card so it reads as one block
          with the status/toolbar below it, instead of floating above out of place. */}
      <div className="review-title-bar">
        <div className="review-title-meta">
          <span className="review-eyebrow">
            {job.vehicle_type === "car" ? "Ô tô" : "Xe máy"} ·{" "}
            {new Date(job.created_at).toLocaleDateString("vi-VN", {
              day: "2-digit",
              month: "2-digit",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit",
            })}
          </span>
          <h1 className="review-filename" title={job.source_name}>
            {job.source_name}
          </h1>
          <button
            className="rename-pencil"
            onClick={() => setRenaming(true)}
            title="Đổi tên phiên"
            type="button"
          >
            <Icon name="edit" size={16} />
          </button>
        </div>
      </div>
      {/* Status cluster (left) sits on the SAME row as the action buttons (right) so the empty left
          space of the button row isn't wasted — saves a full row of vertical scroll. */}
      <div className="review-header">
      <div className="review-status">
      {/* Cross-check runs AUTOMATICALLY after processing — this line just reports its state. The
          disagreement count is labelled "AI đọc lệch" (not "cần xem lại") so it doesn't clash with
          the "Cần xem lại" tab, which counts more than just reader splits. */}
      <div
        className="crosscheck-inline"
        title="Mỗi biển được 3 nguồn đọc độc lập: mô hình của hệ thống và 2 AI. Cả 3 khớp thì tự động xác nhận; nếu có nguồn đọc lệch thì đưa vào tab 'Cần xử lý' để người kiểm tra."
      >
        {results.cloud_ocr_available === false &&
        results.cross_check?.status !== "done" &&
        results.cross_check?.status !== "running" &&
        results.cross_check?.status !== "pending" ? (
          <>
            <Icon name="alert" size={15} /> Chưa bật đối chiếu AI (phiên chạy offline).
          </>
        ) : results.cross_check?.status === "running" ||
          results.cross_check?.status === "pending" ? (
          <>
            <Icon name="clock" size={15} /> Đang đối chiếu AI… <em>(tự động)</em>
          </>
        ) : results.cross_check?.status === "done" ? (
          // Just the status — the actionable count already lives in the "Cần xử lý" tab below, so
          // repeating numbers here only confused users (299 vs 300 vs the missed-scan 1).
          <>
            <Icon name="check" size={15} /> AI đối chiếu xong
            <span
              className="crosscheck-help"
              title="Mỗi biển được 3 nguồn đọc độc lập: mô hình của hệ thống và 2 AI. Cả 3 khớp thì tự động xác nhận; nếu có nguồn đọc lệch thì đưa vào tab 'Cần xử lý' để người kiểm tra."
            >
              ⓘ
            </span>
          </>
        ) : (
          <>
            <Icon name="clock" size={15} /> AI chưa đối chiếu — bấm “Chạy lại AI” ở trên.
          </>
        )}
      </div>

      {/* Job-level missed-vehicle recall summary — a whole-video fact, so it belongs here next to the
          cross-check status, not buried inside one case's video tab. The clickable per-moment markers
          still live on the timeline in the Video evidence tab. */}
      {missedScan && missedScan.status !== "error" && (
        <div className="crosscheck-inline">
          {missedScan.status === "pending" || missedScan.status === "running" ? (
            <>
              <Icon name="clock" size={15} /> Đang rà soát đoạn trống tìm xe bỏ sót… <em>(tự động)</em>
            </>
          ) : (missedScan.candidates?.length ?? 0) > 0 ? (
            <>
              <Icon name="alert" size={15} /> AI nghi{" "}
              <strong>{missedScan.candidates.length}</strong> xe bị bỏ sót — xem thanh thời gian (tab
              Video) để bổ sung.
            </>
          ) : (
            <>
              <Icon name="check" size={15} /> AI đã rà soát{" "}
              <strong>{missedScan.gaps ?? 0}</strong> đoạn trống, không có xe bỏ sót.
            </>
          )}
        </div>
      )}
      </div>
      <div className="review-actions">
        <button
          className="button button-secondary button-compact"
          onClick={() => setShowMissed(true)}
          title="Bổ sung xe bị bỏ sót"
          type="button"
        >
          <Icon name="plus" size={16} /> Bổ sung xe
        </button>
        <button
          className="button button-secondary button-compact"
          onClick={() => setShowCompare(true)}
          title="Đối chiếu với file GT có sẵn"
          type="button"
        >
          <Icon name="upload" size={16} /> Đối chiếu GT
        </button>
        {/* AI cross-check runs automatically after processing; this is a manual re-run fallback. */}
        <button
          className="button button-blue button-compact"
          disabled={crossBusy}
          onClick={runCrossCheck}
          title="Chạy lại đối chiếu AI (bình thường tự chạy sau khi xử lý xong)."
          type="button"
        >
          <Icon name="refresh" size={16} /> {crossBusy ? "Đang chạy…" : "Chạy lại AI"}
        </button>
        {/* Primary export — kept in the toolbar (not a floating button) so it never covers the review
            controls. Turns green-emphasised once every case is handled (Cần xử lý = 0). */}
        <a
          className={`button button-primary button-compact export-top${
            needCheckCount === 0 ? " export-top-ready" : ""
          }`}
          href={`/api/v1/jobs/${job.id}/export.xlsx`}
          title={
            needCheckCount === 0
              ? "Đã soát xong tất cả — xuất GT ra Excel"
              : "Xuất GT ra Excel (trạng thái hiện tại)"
          }
        >
          <Icon name="download" size={16} /> Xuất Excel
        </a>
      </div>
      </div>

      <div className="review-toolbar">
        <label>
          <Icon name="search" size={18} />
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Tìm TrackID hoặc biển số…"
            value={query}
          />
        </label>
        {/* Two DIFFERENT groupings, separated so they aren't read as one adding-up list:
            (1) by what the model READ (mutually exclusive, sums to total), then a divider, then
            (2) by REVIEW STATUS (cross-cutting — a "Model ra biển" case can still be "Chưa duyệt"). */}
        <div className="filter-tabs">
          {[
            ["ALL", "Tất cả", results.total],
            ["RECOGNIZED", "Đọc được biển", readCounts.reliable],
            ["NO_PLATE", "Không có biển", readCounts.noPlate],
            ["DIVIDER", "", 0],
            ["CHECK", "Cần xử lý", needCheckCount],
          ].map(([value, label, count]) =>
            value === "DIVIDER" ? (
              <span aria-hidden className="filter-divider" key="divider" />
            ) : (
              <button
                className={filter === value ? "active" : ""}
                key={value}
                onClick={() => setFilter(value as Filter)}
                type="button"
              >
                {label} <span>{count}</span>
              </button>
            ),
          )}
        </div>
      </div>
      </section>

      {selected ? (
        <div className="review-grid">
          <aside className="card track-panel">
            <header>
              <div>
                <h2>Danh sách lượt xe</h2>
                <span>{filteredEvents.length}</span>
              </div>
              <p>Mỗi dòng là một lượt xe hệ thống phát hiện.</p>
            </header>
            <div className="track-list" ref={trackListRef}>
              {filteredEvents.map((event) => {
                const rec = gtByTrack.get(event.track_id);
                const discarded = rec?.verify_status === "DISCARDED" || rec?.is_duplicate;
                return (
                  <div
                    className="track-item-wrap"
                    key={event.track_id}
                    onMouseEnter={(e) => {
                      // Fixed-position popup computed from the row's rect — the list has
                      // overflow:auto, so an in-flow absolute popup would be clipped.
                      const r = e.currentTarget.getBoundingClientRect();
                      setHoverTrack({
                        event,
                        anchorY: r.top + r.height / 2,
                        left: r.right + 10,
                      });
                    }}
                    onMouseLeave={() => setHoverTrack(null)}
                  >
                    <button
                      className={`track-item ${event.track_id === selected.track_id ? "active" : ""}${
                        discarded ? " track-item-discarded" : ""
                      }`}
                      data-track-id={event.track_id}
                      onClick={() => selectTrack(event.track_id, event.start_timestamp_ms)}
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
                      {discarded ? (
                        <StatusBadge tone="duplicate">Đã loại</StatusBadge>
                      ) : (
                        <StatusBadge tone={classificationTone(event)}>
                          {event.classification === "RECOGNIZED" ? "OCR" : "Xem"}
                        </StatusBadge>
                      )}
                    </button>
                  </div>
                );
              })}
            </div>
          </aside>

          <section className="card evidence-panel">
            <div className="evidence-tabs">
              <button
                className={evidenceTab === "frame" ? "active" : ""}
                onClick={() => setEvidenceTab("frame")}
                type="button"
              >
                Ảnh toàn cảnh
              </button>
              <button
                className={evidenceTab === "video" ? "active" : ""}
                onClick={() => setEvidenceTab("video")}
                type="button"
              >
                Video
              </button>
            </div>

            <div className="evidence-stage">
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
                <div
                  className="case-timeline"
                  onClick={seekFromClick}
                  role="slider"
                  aria-label="Tua video"
                  aria-valuenow={Math.round(videoMs / 1000)}
                  tabIndex={0}
                  title="Nhấp vào bất kỳ vị trí nào trên thanh để tua video đến đó"
                >
                  {results.events.map((event) => {
                    // Two timeline states, matching the simplified tabs: orange = "Cần xử lý" (still
                    // needs a human), dark green = handled/trustworthy (verified or a confident read).
                    // A case is orange ONLY while unverified — once VERIFIED (auto or manual) it turns
                    // green — so the orange count on the bar equals the "Cần xử lý" tab. Gaps
                    // (nghi bỏ sót) are drawn separately.
                    const verified =
                      gtByTrack.get(event.track_id)?.verify_status === "VERIFIED";
                    const risky = needsReview(event) && !verified;
                    const cls = risky ? "ct-low_confidence ct-risky" : "ct-verified";
                    return (
                      <span
                        className={`ct-seg ${cls}`}
                        key={event.track_id}
                        title={
                          risky
                            ? `${
                                event.normalized_plate ??
                                (event.classification === "NO_PLATE"
                                  ? "Xe không biển"
                                  : "?")
                              } — cần xem lại (${toClock(event.start_timestamp_ms)})`
                            : `${event.normalized_plate ?? ""} — đáng tin / đã duyệt (${toClock(
                                event.start_timestamp_ms,
                              )})`
                        }
                        style={{
                          left: `${(event.start_timestamp_ms / totalMs) * 100}%`,
                          width: `${Math.max(
                            0.5,
                            ((event.end_timestamp_ms - event.start_timestamp_ms) / totalMs) * 100,
                          )}%`,
                        }}
                      />
                    );
                  })}
                  {suspectedGaps.map((gap) => (
                    <span
                      className="ct-gap"
                      key={gap.start}
                      style={{
                        left: `${(gap.start / totalMs) * 100}%`,
                        width: `${((gap.end - gap.start) / totalMs) * 100}%`,
                      }}
                    />
                  ))}
                  <span className="ct-playhead" style={{ left: `${(videoMs / totalMs) * 100}%` }} />
                </div>
                <div className="ct-labels">
                  <span>0:00</span>
                  <span className="ct-now">▶ {formatTime(videoMs)}</span>
                  <span>{formatTime(totalMs)}</span>
                </div>
                {suspectedGaps.length > 0 && (
                  <div className="ct-gaps-list">
                    <span className="ct-gaps-title">
                      <span className="ct-gap-legend" />{" "}
                      {missedScan?.status === "done"
                        ? `AI xác nhận có xe (${suspectedGaps.length})`
                        : `Nghi bỏ sót (${suspectedGaps.length})`}
                      :
                    </span>
                    {visibleGaps.map((gap) => (
                      <button
                        className={`ct-gap-chip${gap.confirmed ? " ct-gap-chip-ai" : ""}${
                          openGapKey === gap.start ? " ct-gap-chip-open" : ""
                        }`}
                        key={gap.start}
                        onClick={() => {
                          seekVideo(gap.ts);
                          setOpenGapKey(
                            gap.confirmed && openGapKey !== gap.start ? gap.start : null,
                          );
                        }}
                        title={
                          gap.confirmed
                            ? "AI phát hiện có xe — nhấn để xem chi tiết"
                            : "Khoảng trống — nhấn để tua tới đầu khoảng và tự kiểm tra"
                        }
                        type="button"
                      >
                        {gap.frameUrl && (
                          <img alt="" className="ct-gap-thumb" src={gap.frameUrl} />
                        )}
                        {gap.confirmed ? formatTime(gap.ts) : `${formatTime(gap.start)}–${formatTime(gap.end)}`}
                      </button>
                    ))}
                    {suspectedGaps.length > GAP_CHIP_LIMIT && (
                      <button
                        className="ct-gap-more"
                        onClick={() => setGapsExpanded((value) => !value)}
                        type="button"
                      >
                        {gapsExpanded
                          ? "Thu gọn"
                          : `+${suspectedGaps.length - GAP_CHIP_LIMIT} nữa`}
                      </button>
                    )}
                  </div>
                )}
                {openGap && openGap.confirmed && (
                  <div className="gap-detail">
                    {openGap.frameUrl && (
                      <img alt="Khung hình AI phát hiện" className="gap-detail-frame" src={openGap.frameUrl} />
                    )}
                    <div className="gap-detail-body">
                      <div className="gap-detail-head">
                        <strong>Kết quả AI tại {formatTime(openGap.ts)}</strong>
                        <button
                          className="gap-detail-close"
                          onClick={() => setOpenGapKey(null)}
                          type="button"
                        >
                          <Icon name="x" size={14} />
                        </button>
                      </div>
                      <ul className="gap-detail-list">
                        <li>
                          <span>Có xe</span>
                          <b>Có{openGap.vehicleType ? ` (${openGap.vehicleType === "car" ? "ô tô" : "xe máy"})` : ""}</b>
                        </li>
                        <li>
                          <span>Biển số</span>
                          <b>{openGap.hasPlate && openGap.plate ? openGap.plate : "Không thấy biển"}</b>
                        </li>
                        <li>
                          <span>Trong list</span>
                          <b className={openGap.inList ? "" : "gap-detail-warn"}>
                            {openGap.inList
                              ? "Đã có case ở thời điểm này"
                              : "Chưa có (thời điểm này) → nên bổ sung"}
                          </b>
                        </li>
                        {openGap.plateElsewhereMs != null && (
                          <li>
                            <span>Biển này</span>
                            <b>Từng xuất hiện lúc {formatTime(openGap.plateElsewhereMs)} (lượt khác)</b>
                          </li>
                        )}
                      </ul>
                      <div className="gap-detail-actions">
                        <button
                          className="button button-primary button-compact"
                          disabled={gapBusy}
                          onClick={() => addGapToGt(openGap)}
                          type="button"
                        >
                          <Icon name="plus" size={15} /> Thêm vào GT
                        </button>
                        <button
                          className="button button-secondary button-compact"
                          disabled={gapBusy}
                          onClick={() => setConfirmDismissTs(openGap.ts)}
                          type="button"
                          title="Không phải xe bỏ sót — xoá khỏi danh sách"
                        >
                          <Icon name="trash" size={15} /> Xoá
                        </button>
                      </div>
                    </div>
                  </div>
                )}
                <div className="ct-legend">
                  <span>
                    <i className="ct-sw ct-verified" /> Đáng tin cậy / đã duyệt
                  </span>
                  <span>
                    <i className="ct-sw ct-low_confidence" /> Cần xử lý
                  </span>
                  <span>
                    <i className="ct-gap-legend" /> Nghi bỏ sót
                  </span>
                  <span>
                    <i className="ct-sw-playhead" /> Vị trí video hiện tại
                  </span>
                </div>
              </div>
            ) : (
            <div
              className="full-frame"
              onMouseLeave={() => setLoupe(null)}
              onMouseMove={(event) => {
                const rect = event.currentTarget.getBoundingClientRect();
                const mx = event.clientX - rect.left;
                const my = event.clientY - rect.top;
                const Z = 2.6;
                const SIZE = 146;
                setLoupe({
                  left: mx - SIZE / 2,
                  top: my - SIZE / 2,
                  bgX: -(mx * Z - SIZE / 2),
                  bgY: -(my * Z - SIZE / 2),
                  bgW: rect.width * Z,
                  bgH: rect.height * Z,
                });
              }}
              style={
                job.width && job.height
                  ? { aspectRatio: `${job.width} / ${job.height}` }
                  : undefined
              }
            >
              <img alt={`Ảnh toàn cảnh ${selected.track_code}`} src={selected.full_frame_url} />
              {loupe && (
                <div
                  className="frame-loupe"
                  style={{
                    left: loupe.left,
                    top: loupe.top,
                    backgroundImage: `url(${selected.full_frame_url})`,
                    backgroundSize: `${loupe.bgW}px ${loupe.bgH}px`,
                    backgroundPosition: `${loupe.bgX}px ${loupe.bgY}px`,
                  }}
                />
              )}
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
            </div>

            <footer className="evidence-footer">
              <Icon name="shield" size={16} /> Không bằng chứng, không ghi nhận.
            </footer>
          </section>

          <aside className="card gt-panel">
            <section className="prediction-section">
              <div className="panel-section-title">
                <div>
                  <span>Dự đoán</span>
                  <h2>Kết quả model</h2>
                </div>
                <StatusBadge tone={classificationTone(selected)}>
                  {classificationLabel(selected)}
                </StatusBadge>
              </div>
              <strong className="prediction-value">{resultLabel(selected)}</strong>
              <dl>
                {selected.raw_plate && selected.raw_plate !== selected.normalized_plate && (
                  <div>
                    <dt>Máy đọc gốc (trước chuẩn hóa)</dt>
                    <dd>{selected.raw_plate}</dd>
                  </div>
                )}
                <div>
                  <dt>Độ tin đọc biển</dt>
                  <dd>{confidence(selected.confidence)}</dd>
                </div>
                <div>
                  <dt>Độ tin phát hiện (biển / xe)</dt>
                  <dd>
                    {confidence(selected.plate_confidence)} / {confidence(selected.vehicle_confidence)}
                  </dd>
                </div>
              </dl>
              <CrossCheckCard event={selected} key={selected.track_id} />
              {friendlyFlags(selected.quality_flags).length > 0 && (
                <div className="quality-flags">
                  {friendlyFlags(selected.quality_flags).map(({ flag, label, tone }) => (
                    <span className={`qflag qflag-${tone}`} key={flag}>
                      {label}
                    </span>
                  ))}
                </div>
              )}
              {selected.normalized_plate &&
                isRiskyRead(selected) &&
                !selected.quality_flags.includes("OCR_DISAGREEMENT") && (
                <div className="prediction-warning" role="alert">
                  <Icon name="alert" size={18} />
                  <p>
                    {selected.quality_flags.includes("WEAK_CHARACTER") ? (
                      <>
                        <strong>
                          Cẩn thận — có ký tự bị che / chói, model có thể đọc SAI dù % cao.
                        </strong>{" "}
                        Biển này có ít nhất 1 ký tự model không chắc (thường do tem/vật che hoặc
                        đèn rọi). Phóng to crop, đối chiếu TỪNG ký tự với video trước khi xác nhận
                        (vd: D dễ bị đọc thành U).
                      </>
                    ) : (
                      <>
                        <strong>Cẩn thận — biển khó, model có thể đọc SAI dù % cao.</strong> Chói /
                        mờ / che hoặc chỉ đọc 1 frame. Nhìn kỹ crop + video, đối chiếu từng ký tự
                        trước khi xác nhận (đừng tin số % một mình).
                      </>
                    )}
                  </p>
                </div>
              )}
            </section>

            <GtPanel
              // Key on the STORED values, not just the track: the record can arrive/change AFTER
              // this panel mounts (GT loads late, or the cross-check auto-fills gt_text + verifies),
              // and a plain track key wouldn't remount → the inputs would keep their stale (empty)
              // initial state. Re-keying on the persisted fields re-seeds the inputs when the saved
              // value changes, but NOT while the reviewer types (local edits don't touch the record).
              key={`${selected.track_id}:${selectedGt?.verify_status ?? ""}:${
                selectedGt?.gt_text ?? ""
              }:${selectedGt?.classification ?? ""}`}
              cloudQuality={selected.cloud_quality}
              cloudQualityAll={selected.cloud_quality_all}
              defaultPlate={consensusPlate(selected)}
              onChanged={() => setGtReload((value) => value + 1)}
              onDiff={setDiffToast}
              onNotify={setSaveToast}
              qualityDisagree={selected.quality_flags.includes("QUALITY_DISAGREEMENT")}
              suspectNoPlate={selected.classification === "NO_PLATE"}
              suspectLogo={selected.quality_flags.includes("SUSPECTED_NON_PLATE")}
              record={selectedGt}
            />

            <footer className="review-navigation">
              <button
                disabled={navIndex <= 0}
                onClick={() =>
                  selectTrack(
                    navEvents[navIndex - 1].track_id,
                    navEvents[navIndex - 1].start_timestamp_ms,
                  )
                }
                type="button"
              >
                ← Bản ghi trước
              </button>
              <button
                disabled={navIndex < 0 || navIndex >= navEvents.length - 1}
                onClick={() =>
                  selectTrack(
                    navEvents[navIndex + 1].track_id,
                    navEvents[navIndex + 1].start_timestamp_ms,
                  )
                }
                type="button"
              >
                Bản ghi sau →
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


      {crossResult !== null && (
        <div className="modal-overlay" onClick={() => setCrossResult(null)} role="presentation">
          <div
            aria-label="Kết quả kiểm chéo AI"
            aria-modal="true"
            className="modal-card modal-card-wide"
            onClick={(event) => event.stopPropagation()}
            role="alertdialog"
          >
            <span
              className={`modal-icon ${
                crossResult.error ? "modal-icon-danger" : "modal-icon-blue"
              }`}
            >
              <Icon name={crossResult.error ? "alert" : "shield"} size={26} />
            </span>
            <h3>{crossResult.error ? "Kiểm chéo AI thất bại" : "Kết quả kiểm chéo AI"}</h3>
            {crossResult.error ? (
              <p>{crossResult.error}</p>
            ) : (
              <>
                <p>
                  Đọc lại <strong>{crossResult.checked}</strong> biển bằng <strong>2 model AI
                  (cloud)</strong> rồi so với <strong>model local</strong>. Cả 3 cùng đọc → tự
                  duyệt; khác nhau → cần bạn xem.
                </p>
                <div className="cross-stats">
                  <div className="cross-stat cross-stat-ok">
                    <strong>{crossResult.auto_verified ?? 0}</strong>
                    <span>Tự duyệt — cả 3 nguồn khớp, đã xác nhận GT</span>
                  </div>
                  <div className="cross-stat cross-stat-diff">
                    <strong>{crossResult.disagree}</strong>
                    <span>Cần xem lại — có nguồn đọc khác, chờ bạn quyết</span>
                  </div>
                  {crossResult.unverified > 0 && (
                    <div className="cross-stat cross-stat-muted">
                      <strong>{crossResult.unverified}</strong>
                      <span>Chưa kiểm được (mạng/AI lỗi) — thử lại sau</span>
                    </div>
                  )}
                </div>
              </>
            )}
            <div className="modal-actions">
              <button
                className="button button-secondary"
                onClick={() => setCrossResult(null)}
                type="button"
              >
                Đóng
              </button>
              {!crossResult.error && crossResult.disagree > 0 && (
                <button
                  className="button button-blue"
                  onClick={() => {
                    setFilter("CHECK");
                    setCrossResult(null);
                  }}
                  type="button"
                >
                  Xem “Cần xử lý” ({crossResult.disagree})
                </button>
              )}
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

      <RenameJobDialog
        initialName={job.source_name}
        onClose={() => setRenaming(false)}
        onSave={(name) => onRename(job, name)}
        open={renaming}
      />

      <ConfirmDialog
        busy={gapBusy}
        cancelLabel="Không"
        confirmLabel="Xoá"
        description="Xoá khoảng này khỏi danh sách nghi bỏ sót? (dùng khi AI báo nhầm — không phải xe bị bỏ sót)"
        onCancel={() => setConfirmDismissTs(null)}
        onConfirm={() => {
          const ts = confirmDismissTs;
          setConfirmDismissTs(null);
          if (ts != null) removeGap(ts);
        }}
        open={confirmDismissTs !== null}
        title="Xoá khoảng nghi bỏ sót?"
      />

      {(saveToast || diffToast) && (
        <div className="toast-stack">
          {saveToast && (
            <div className="toast-corner toast-corner-success" role="status">
              <Icon name="check" size={16} />
              <div>{saveToast}</div>
              <button
                aria-label="Đóng thông báo"
                className="toast-corner-close"
                onClick={() => setSaveToast(null)}
                type="button"
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          )}
          {diffToast && (
            <div className="toast-corner" role="status">
              <div>{diffToast}</div>
              <button
                aria-label="Đóng thông báo"
                className="toast-corner-close"
                onClick={() => setDiffToast(null)}
                type="button"
              >
                <Icon name="x" size={14} />
              </button>
            </div>
          )}
        </div>
      )}


      {hoverTrack && (
        <div
          className="track-popup"
          ref={popupRef}
          role="tooltip"
          style={{ position: "fixed", top: hoverTop, left: hoverTrack.left }}
        >
          <img
            alt={`Crop ${hoverTrack.event.track_code}`}
            className="track-popup-crop"
            onLoad={() => {
              // The crop's height isn't known until it loads; re-clamp so a tall crop still fits.
              if (!popupRef.current) return;
              const height = popupRef.current.offsetHeight;
              setHoverTop(
                Math.max(12, Math.min(hoverTrack.anchorY - height / 2, window.innerHeight - height - 12)),
              );
            }}
            src={hoverTrack.event.plate_crop_url ?? hoverTrack.event.vehicle_crop_url}
          />
          <dl className="track-popup-meta">
            <div>
              <dt>Mã lượt xe</dt>
              <dd>{hoverTrack.event.track_code}</dd>
            </div>
            <div>
              <dt>Thời điểm</dt>
              <dd>{formatTime(hoverTrack.event.best_timestamp_ms)}</dd>
            </div>
            <div>
              <dt>Khung xe</dt>
              <dd>{hoverTrack.event.vehicle_bbox.join(", ")}</dd>
            </div>
            <div>
              <dt>Khung biển</dt>
              <dd>{hoverTrack.event.plate_bbox?.join(", ") ?? "Không phát hiện"}</dd>
            </div>
            <div>
              <dt>Số lần bắt xe</dt>
              <dd>{hoverTrack.event.vehicle_detection_count}</dd>
            </div>
            <div>
              <dt>Số lần bắt biển</dt>
              <dd>{hoverTrack.event.plate_detection_count}</dd>
            </div>
          </dl>
        </div>
      )}
    </section>
  );
}

function GtPanel({
  record,
  onChanged,
  onNotify,
  onDiff,
  cloudQuality,
  cloudQualityAll,
  defaultPlate,
  qualityDisagree,
  suspectNoPlate,
  suspectLogo,
}: {
  record: GroundTruthRecord | null;
  onChanged: () => void;
  onNotify?: (message: string) => void;
  onDiff?: (message: string | null) => void;
  cloudQuality?: string | null;
  cloudQualityAll?: string[];
  defaultPlate?: string | null;
  qualityDisagree?: boolean;
  suspectNoPlate?: boolean;
  suspectLogo?: boolean;
}) {
  // Only a GENUINE no-plate case (the pipeline found no plate at all) defaults to "Xe không biển"
  // with an empty GT. A "suspected logo" (SUSPECTED_NON_PLATE) still has a real plate read, so we
  // KEEP that read and only hint the reviewer to check it — never auto-blank a real plate.
  // Prefill the GT plate, in order of trust: the stored gt_text → the 2/3 CONSENSUS the readers
  // agreed on (``defaultPlate``, which does NOT require local) → the local read as a last resort.
  // (``??`` alone wasn't enough: an EMPTY-STRING gt_text isn't nullish, so it wouldn't fall through.)
  const [gtText, setGtText] = useState(
    (record?.gt_text && record.gt_text.trim()
      ? record.gt_text
      : suspectNoPlate
        ? ""
        : defaultPlate || record?.predicted_text) ?? "",
  );
  const [note, setNote] = useState(record?.note ?? "");
  // Prefill the category with AI-1's label as a SUGGESTION even when the two AIs disagree — the
  // reviewer just confirms it with one click (or changes it), never has to type from scratch.
  const [quality, setQuality] = useState(
    record?.classification ?? (suspectNoPlate ? NO_PLATE_OPTION : cloudQuality) ?? "",
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  // When the reviewer's GT differs from what the model read, make the divergence explicit: the
  // model's number on the left NEVER changes (it is preserved evidence — Prediction ≠ GT), so this
  // is the only cue that a correction took effect. Computed BEFORE the early return so the toast
  // effect can depend on it (rules of hooks).
  const modelPlate = (record?.predicted_text ?? "").trim().toUpperCase();
  const gtDiffersFromModel =
    quality !== NO_PLATE_OPTION &&
    gtText.trim().length > 0 &&
    modelPlate.length > 0 &&
    gtText.trim().toUpperCase() !== modelPlate;
  // Surface it via the PARENT's toast stack (so it stacks with the "Đã lưu" toast instead of
  // overlapping). Fire on each transition into "differs"; clear it otherwise and on unmount.
  useEffect(() => {
    onDiff?.(
      gtDiffersFromModel
        ? `Bạn đang sửa khác model đọc (${modelPlate}). Số bạn nhập sẽ là GT chính thức khi lưu; kết quả model bên trái giữ nguyên để đối chiếu.`
        : null,
    );
    return () => onDiff?.(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gtDiffersFromModel, modelPlate]);

  if (!record) {
    return (
      <section className="ground-truth-section">
        <div className="panel-section-title">
          <div>
            <h2>Kiểm duyệt thủ công</h2>
          </div>
        </div>
        <p className="backend-note">Đang tạo bản nháp GT cho lượt xe này…</p>
      </section>
    );
  }

  const run = (task: () => Promise<unknown>, successMessage?: string) => {
    setBusy(true);
    setError("");
    task()
      .then(() => {
        onChanged();
        if (successMessage) onNotify?.(successMessage);
      })
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "Không lưu được kiểm duyệt."),
      )
      .finally(() => setBusy(false));
  };

  const isNoPlate = quality === NO_PLATE_OPTION;
  // For a no-plate vehicle, persist the sentinel GT string (the export renders it as "Xe không
  // biển") instead of the empty box or a garbage logo read.
  const gtToSave = isNoPlate ? NO_PLATE_GT : gtText;
  const save = () =>
    run(
      () => patchGt(record.id, { gt_text: gtToSave, note, classification: quality }),
      "Đã lưu thay đổi.",
    );
  const verify = () =>
    run(async () => {
      await patchGt(record.id, { gt_text: gtToSave, note, classification: quality });
      await actionGt(record.id, "verify");
    }, "Đã xác nhận GT.");
  const discard = () => run(() => actionGt(record.id, "discard"), "Đã loại bỏ lượt xe này.");
  const restore = () => run(() => actionGt(record.id, "restore"), "Đã khôi phục lượt xe.");

  const isDiscarded = record.verify_status === "DISCARDED";
  const isVerified = record.verify_status === "VERIFIED";

  return (
    <section className="ground-truth-section">
      <div className="panel-section-title">
        <div>
          <h2>Kiểm duyệt thủ công</h2>
        </div>
        <StatusBadge tone={verifyTone(record.verify_status)}>
          {VERIFY_LABEL[record.verify_status]}
        </StatusBadge>
      </div>
      <div className="gt-field-row">
        <label>
          Biển số đúng (GT)
          <input
            disabled={isNoPlate}
            onChange={(event) => setGtText(event.target.value.toUpperCase())}
            placeholder={isNoPlate ? "Xe không biển" : "Nhập biển số đúng"}
            value={isNoPlate ? "" : gtText}
          />
        </label>
        <label>
          Mức độ nhận diện
          <select onChange={(event) => setQuality(event.target.value)} value={quality}>
            <option value="">— Chọn mức —</option>
            {QUALITY_OPTIONS.map((option) => (
              <option key={option} value={option}>
                {option}
              </option>
            ))}
          </select>
        </label>
      </div>
      {suspectNoPlate ? (
        <p className="quality-hint quality-hint-diff">
          ⚠ Nghi ngờ <strong>xe không biển</strong> (OCR đọc nhầm từ logo/tem dán). Đã chọn "Xe không
          biển" — không cần điền số; hoặc chọn <strong>Loại bỏ</strong> nếu không phải phương tiện.
        </p>
      ) : suspectLogo ? (
        <p className="quality-hint quality-hint-diff">
          ⚠ Nghi ngờ <strong>logo/không phải biển số</strong> — vui lòng xem kỹ ảnh crop. Nếu{" "}
          <strong>CÓ biển</strong>, giữ hoặc chỉnh số đã điền; nếu là <strong>logo</strong>, chọn "Xe
          không biển" hoặc <strong>Loại bỏ</strong>.
        </p>
      ) : cloudQuality ? (
        <p className={qualityDisagree ? "quality-hint quality-hint-diff" : "quality-hint"}>
          {qualityDisagree ? (
            // 1-1-1 split — the three AIs each said something different; show all so you decide.
            <>
              ⚠ 3 AI phân loại khác nhau:{" "}
              <strong>{(cloudQualityAll ?? []).join(" · ")}</strong> — vui lòng chọn phân loại.
            </>
          ) : (
            `Gợi ý: "${cloudQuality}" (2/3 AI khớp, đã điền sẵn — chỉnh nếu cần).`
          )}
        </p>
      ) : null}
      <details className="note-details" open={note.trim().length > 0}>
        <summary>
          Ghi chú kiểm duyệt (tuỳ chọn)
          <Icon name="chevron" size={15} />
        </summary>
        <textarea
          onChange={(event) => setNote(event.target.value)}
          placeholder="Nhập ghi chú nếu cần…"
          value={note}
        />
      </details>
      {error && <p className="backend-note">{error}</p>}
      <div className="gt-actions">
        <button className="button button-secondary" disabled={busy} onClick={save} type="button">
          {isVerified ? "Lưu thay đổi" : "Lưu nháp"}
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
            Loại bỏ
          </button>
        )}
        <button
          className="button button-primary gt-verify"
          disabled={busy || isVerified || (!gtText.trim() && !isNoPlate)}
          onClick={verify}
          type="button"
        >
          <Icon name="check" size={16} /> {isVerified ? "Đã xác nhận" : "Xác nhận GT"}
        </button>
      </div>
    </section>
  );
}
