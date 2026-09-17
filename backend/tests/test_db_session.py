from app.db.session import engine

# Pure configuration check - no db_session/client fixture needed. Confirms
# the engine is built with pool_pre_ping enabled, so a connection a
# long-lived server (or RDS) silently closed behind our back is detected
# and recycled rather than surfacing as an OperationalError on the next
# unrelated request.


def test_engine_has_pool_pre_ping_enabled():
    assert engine.pool._pre_ping is True
