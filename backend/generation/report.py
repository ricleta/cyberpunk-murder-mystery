"""The generation report lives on the session row (Session.generation_report), not in
process memory, so it survives restarts and works with several server workers."""


def store_report(session, key, report):
    """Set one stage's report. A new dict is assigned so SQLAlchemy sees the JSON change."""
    merged = dict(session.generation_report or {})
    merged[key] = report
    session.generation_report = merged
