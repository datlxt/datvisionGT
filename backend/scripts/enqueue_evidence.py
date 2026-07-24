import argparse

from redis import Redis
from rq import Queue

from app.core.config import get_settings
from app.workers.evidence import process_evidence_job


def main() -> None:
    parser = argparse.ArgumentParser(description="Enqueue evidence extraction for a database job")
    parser.add_argument("job_id")
    args = parser.parse_args()
    settings = get_settings()
    queue = Queue(settings.rq_queue, connection=Redis.from_url(settings.redis_url))
    rq_job = queue.enqueue(
        process_evidence_job,
        args.job_id,
        job_id=f"evidence:{args.job_id}",
        result_ttl=86_400,
        job_timeout="6h",
    )
    print(rq_job.id)


if __name__ == "__main__":
    main()
