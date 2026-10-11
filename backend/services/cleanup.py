"""Row cleanup helpers shared by the session, character and generation routes."""
from models import Character, Relationship, Secret, SecretKnowledge, Objective


def delete_characters(char_ids):
    """Delete characters plus every row that references them (FK-safe order)."""
    if not char_ids:
        return
    secret_ids = [s.id for s in Secret.query.filter(
        (Secret.subject_character_id.in_(char_ids)) | (Secret.related_character_id.in_(char_ids))).all()]
    rel_ids = [r.id for r in Relationship.query.filter(
        (Relationship.source_character_id.in_(char_ids)) | (Relationship.target_character_id.in_(char_ids))).all()]
    # Secrets tied to relationships being removed must go too
    if rel_ids:
        secret_ids += [s.id for s in Secret.query.filter(Secret.related_relationship_id.in_(rel_ids)).all()]
    secret_ids = list(set(secret_ids))
    # Objectives: the removed characters' own objectives are deleted. Objectives that other
    # characters keep only lose their references to removed rows (their text is a snapshot).
    own_objective_ids = [o.id for o in Objective.query.filter(Objective.character_id.in_(char_ids)).all()]
    if own_objective_ids:
        Objective.query.filter(Objective.linked_objective_id.in_(own_objective_ids)).update(
            {Objective.linked_objective_id: None, Objective.link_type: None}, synchronize_session=False)
    Objective.query.filter(Objective.related_character_id.in_(char_ids)).update(
        {Objective.related_character_id: None}, synchronize_session=False)
    if secret_ids:
        Objective.query.filter(Objective.related_secret_id.in_(secret_ids)).update(
            {Objective.related_secret_id: None}, synchronize_session=False)
    if rel_ids:
        Objective.query.filter(Objective.related_relationship_id.in_(rel_ids)).update(
            {Objective.related_relationship_id: None}, synchronize_session=False)
    Objective.query.filter(Objective.character_id.in_(char_ids)).delete(synchronize_session=False)
    if secret_ids:
        SecretKnowledge.query.filter(SecretKnowledge.secret_id.in_(secret_ids)).delete(synchronize_session=False)
    SecretKnowledge.query.filter(SecretKnowledge.knower_character_id.in_(char_ids)).delete(synchronize_session=False)
    if secret_ids:
        Secret.query.filter(Secret.id.in_(secret_ids)).delete(synchronize_session=False)
    if rel_ids:
        Relationship.query.filter(Relationship.id.in_(rel_ids)).delete(synchronize_session=False)
    Character.query.filter(Character.id.in_(char_ids)).delete(synchronize_session=False)


def clear_generated(session_id, relationships=False):
    """Delete a session's generated content in FK-safe order:
    objectives -> secret knowledge -> secrets (-> relationships). Does not commit.
    Returns the number of deleted rows (truthy when anything was deleted)."""
    deleted = Objective.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    secret_ids = [s.id for s in Secret.query.filter_by(session_id=session_id).all()]
    if secret_ids:
        SecretKnowledge.query.filter(SecretKnowledge.secret_id.in_(secret_ids)).delete(synchronize_session=False)
        deleted += Secret.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    if relationships:
        deleted += Relationship.query.filter_by(session_id=session_id).delete(synchronize_session=False)
    return deleted


def clear_generation_metadata(session):
    """Forget the seed, timestamp and report of the previous generation. Does not commit."""
    session.generation_seed = None
    session.generated_at = None
    session.generation_report = None
