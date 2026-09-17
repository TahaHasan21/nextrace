import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL environment variable is not set")


# pool_pre_ping issues a lightweight "is this connection still alive?"
# check before handing a pooled connection back out, transparently
# discarding and replacing it if not - without this, a connection a
# long-lived server (or RDS) silently closed behind our back (idle
# timeout, failover, restart) surfaces as an OperationalError on the next
# unrelated request instead of being recycled. No other pool behavior
# changes: size/overflow/recycle stay at SQLAlchemy's defaults, since
# nothing here demonstrates a need to tune them.
engine = create_engine(DATABASE_URL, pool_pre_ping=True)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


def get_db():
    db = SessionLocal()

    try:
        yield db
    finally:
        db.close()
