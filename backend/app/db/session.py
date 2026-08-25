from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import get_settings

settings = get_settings()
# prepare_threshold=None disables psycopg3 server-side prepared statements.
# With a pooled connection, an interrupted transaction (e.g. a worker restart
# mid-query) can leave a half-created prepared statement on a connection; when
# that connection is reused psycopg raises DuplicatePreparedStatement
# ("prepared statement _pg3_0 already exists"). Disabling them removes the
# failure mode entirely at a negligible cost for this low-QPS workload.
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    connect_args={"prepare_threshold": None},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db():
    with SessionLocal() as session:
        yield session
