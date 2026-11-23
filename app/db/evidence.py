from sqlalchemy import update

from app.models import Evidence


def clear_weight_cache(evidence):
    evidence.evidence_weight = None
    evidence.weight_run_id = None
    evidence.weight_components = {}


async def invalidate_independence(session, hypothesis_id) -> int:
    """Clear current versioned caches; immutable runs and archived attachments stay intact."""
    changed = await session.execute(
        update(Evidence)
        .where(
            Evidence.hypothesis_id == hypothesis_id,
            Evidence.archived_at.is_(None),
            Evidence.independence_run_id.is_not(None),
        )
        .values(
            independence_score=None,
            independence_run_id=None,
            independence_components={},
            evidence_weight=None,
            weight_run_id=None,
            weight_components={},
        )
        .execution_options(synchronize_session="fetch")
    )
    return changed.rowcount
