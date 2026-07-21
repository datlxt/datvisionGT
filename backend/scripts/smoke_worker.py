import os

from redis import Redis
from rq import Queue

from app.workers.tasks import system_smoke_task


def main() -> None:
    redis = Redis.from_url(os.getenv("REDIS_URL", "redis://redis:6379/0"))
    queue = Queue(os.getenv("RQ_QUEUE", "datvision"), connection=redis)
    job = queue.enqueue(system_smoke_task, job_timeout=30, result_ttl=60)
    result = job.latest_result(timeout=15)
    if result is None or result.return_value is None:
        raise RuntimeError("Worker did not return a result within 15 seconds")
    print(result.return_value)


if __name__ == "__main__":
    main()

