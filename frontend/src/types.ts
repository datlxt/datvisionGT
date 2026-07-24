export type View = "overview" | "create" | "processing" | "review" | "exports";

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
};

export type ResultList = {
  job_id: string;
  source_name: string;
  status: JobStatus;
  total: number;
  counts: Record<EventClassification, number>;
  events: EventResult[];
};

export type Health = {
  status: "ok" | "degraded";
  database: string;
  redis: string;
  storage: string;
};
