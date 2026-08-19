export type View = "overview" | "create" | "processing" | "review" | "exports" | "condense";

export type CondenseStatus = {
  id: string;
  status: "queued" | "scanning" | "rendering" | "completed" | "empty" | "failed" | string;
  progress: number;
  source_name: string | null;
  min_gap_seconds: number | null;
  source_duration_ms: number | null;
  condensed_duration_ms: number | null;
  cut_ms: number | null;
  segment_count: number | null;
  segments: [number, number][] | null;
};

export type JobStatus =
  | "DRAFT"
  | "PENDING"
  | "QUEUED"
  | "PROCESSING"
  | "WAITING_FOR_REVIEW"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type Job = {
  id: string;
  job_code: string;
  source_name: string;
  source_hash: string | null;
  source_size_bytes: number | null;
  status: JobStatus;
  current_stage: string | null;
  progress: number;
  processed_frames: number;
  total_frames: number | null;
  duration_ms: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  processing_mode: "HIGH_RECALL" | "BALANCED" | "FAST";
  sample_rate: number;
  vehicle_type: "motorcycle" | "car";
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
};

export type EventClassification =
  | "RECOGNIZED"
  | "LOW_CONFIDENCE"
  | "UNREADABLE"
  | "NO_PLATE";

export type EventResult = {
  track_id: string;
  track_code: string;
  classification: EventClassification;
  normalized_plate: string | null;
  raw_plate: string | null;
  confidence: number | null;
  start_timestamp_ms: number;
  end_timestamp_ms: number;
  best_timestamp_ms: number;
  best_frame_number: number;
  vehicle_bbox: [number, number, number, number];
  plate_bbox: [number, number, number, number] | null;
  vehicle_confidence: number;
  plate_confidence: number | null;
  vehicle_detection_count: number;
  plate_detection_count: number;
  quality_score: number | null;
  quality_flags: string[];
  full_frame_url: string;
  vehicle_crop_url: string;
  plate_crop_url: string | null;
  cloud_plate?: string | null;
  cloud_quality?: string | null;
  cloud_quality_all?: string[];
  qwen_plate?: string | null;
  qwen_quality?: string | null;
};

export type ResultList = {
  job_id: string;
  source_name: string;
  status: JobStatus;
  total: number;
  counts: Record<EventClassification, number>;
  events: EventResult[];
  cross_check?: {
    status: "pending" | "running" | "done";
    checked?: number;
    agree?: number;
    disagree?: number;
    unverified?: number;
    auto_verified?: number;
  } | null;
  missed_scan?: {
    status: "pending" | "running" | "done" | "error";
    gaps?: number;
    scanned?: number;
    candidates: {
      start_ms: number;
      end_ms: number;
      ts_ms: number;
      frame_url: string;
      vehicle?: boolean;
      has_plate?: boolean;
      plate?: string;
      vehicle_type?: string;
      in_list?: boolean;
      plate_elsewhere_ms?: number | null;
    }[];
  } | null;
};

export type VerifyStatus = "UNVERIFIED" | "IN_REVIEW" | "VERIFIED" | "DISCARDED";

export type GroundTruthRecord = {
  id: string;
  track_id: string;
  record_code: string;
  record_source: string;
  predicted_text: string | null;
  prediction_confidence: number | null;
  gt_text: string | null;
  normalized_gt_text: string | null;
  classification: string | null;
  verify_status: VerifyStatus;
  evidence_status: string;
  is_duplicate: boolean;
  duplicate_of_id: string | null;
  note: string | null;
  quality_flags: string[];
  version: number;
};

export type GroundTruthItem = { record: GroundTruthRecord; event: EventResult | null };

export type GroundTruthList = {
  job_id: string;
  status: JobStatus;
  total: number;
  counts: Record<string, number>;
  items: GroundTruthItem[];
};

export type GtCaseStatus = "match" | "diff" | "extra" | "missed";

export type GtCaseItem = {
  track_id: string | null;
  track_code: string | null;
  model_plate: string;
  classification: string | null;
  gt_plate: string | null;
  quality: string | null;
  agree: boolean;
  status: GtCaseStatus;
};

export type GtCompareResponse = {
  job_id: string;
  gt_events: number;
  model_events: number;
  detection: Record<string, number>;
  recognition: Record<string, number>;
  items: GtCaseItem[];
};

export type Health = {
  status: "ok" | "degraded";
  database: string;
  redis: string;
  storage: string;
};
