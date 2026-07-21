import uuid

from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from app.db.session import engine

EXPECTED_TABLES = {
    "alembic_version",
    "artifacts",
    "camera_configs",
    "detections",
    "exports",
    "ground_truth_records",
    "job_events",
    "job_models",
    "model_versions",
    "processing_jobs",
    "recognition_results",
    "review_actions",
    "tracks",
    "users",
}


def _expect_integrity_error(connection, statement: str, parameters: dict[str, object]) -> None:
    try:
        with connection.begin_nested():
            connection.execute(text(statement), parameters)
    except IntegrityError:
        return
    raise AssertionError("Database accepted a row that should violate an integrity constraint")


def main() -> None:
    inspector = inspect(engine)
    actual_tables = set(inspector.get_table_names())
    missing = EXPECTED_TABLES - actual_tables
    if missing:
        raise RuntimeError(f"Missing database tables: {sorted(missing)}")

    job_constraints = {
        constraint["name"] for constraint in inspector.get_check_constraints("processing_jobs")
    }
    if "ck_processing_jobs_status" not in job_constraints:
        raise RuntimeError("processing_jobs status constraint is missing")

    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            _expect_integrity_error(
                connection,
                """
                INSERT INTO processing_jobs
                    (id, job_code, source_name, source_path, status)
                VALUES
                    (:id, :job_code, 'invalid.mp4', '/invalid.mp4', 'NOT_A_STATUS')
                """,
                {"id": uuid.uuid4(), "job_code": f"INVALID_{uuid.uuid4().hex}"},
            )

            job_a = uuid.uuid4()
            job_b = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO processing_jobs
                        (id, job_code, source_name, source_path)
                    VALUES
                        (:job_a, :code_a, 'a.mp4', '/a.mp4'),
                        (:job_b, :code_b, 'b.mp4', '/b.mp4')
                    """
                ),
                {
                    "job_a": job_a,
                    "job_b": job_b,
                    "code_a": f"SCHEMA_A_{uuid.uuid4().hex}",
                    "code_b": f"SCHEMA_B_{uuid.uuid4().hex}",
                },
            )

            frame_a = uuid.uuid4()
            crop_b = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO artifacts (id, job_id, kind, storage_key, size_bytes)
                    VALUES
                        (:frame_a, :job_a, 'FULL_FRAME', 'jobs/a/frame.jpg', 1),
                        (:crop_b, :job_b, 'PLATE_CROP', 'jobs/b/crop.jpg', 1)
                    """
                ),
                {"frame_a": frame_a, "job_a": job_a, "crop_b": crop_b, "job_b": job_b},
            )

            track_a = uuid.uuid4()
            connection.execute(
                text(
                    """
                    INSERT INTO tracks
                        (id, job_id, track_code, start_frame, end_frame,
                         start_timestamp_ms, end_timestamp_ms, event_key)
                    VALUES
                        (:id, :job_id, 'PLATE_000001', 0, 1, 0, 40, :event_key)
                    """
                ),
                {"id": track_a, "job_id": job_a, "event_key": "a" * 64},
            )

            _expect_integrity_error(
                connection,
                """
                INSERT INTO detections
                    (id, job_id, track_id, frame_number, timestamp_ms,
                     bbox_x1, bbox_y1, bbox_x2, bbox_y2, detection_confidence,
                     full_frame_artifact_id, crop_artifact_id)
                VALUES
                    (:id, :job_id, :track_id, 0, 0, 0, 0, 10, 10, 0.9,
                     :frame_artifact, :cross_job_crop)
                """,
                {
                    "id": uuid.uuid4(),
                    "job_id": job_a,
                    "track_id": track_a,
                    "frame_artifact": frame_a,
                    "cross_job_crop": crop_b,
                },
            )
        finally:
            transaction.rollback()

    print("Database schema verification passed")


if __name__ == "__main__":
    main()

