import uuid
from extensions import db
from models import User, Session, SessionMember, Character, Relationship

# Shared session builder for the generation test suites (secrets, objectives).

def create_test_session(session_id, roles, seed=42):
    """Creates a session with the specified roles and fully completed lifepath and relationship ring."""
    gm_user = User(id=f"gm_{session_id}", username=f"GM_{session_id}", password_hash="hash")
    db.session.add(gm_user)
    session = Session(id=session_id, gm_id=gm_user.id, name=f"Session {session_id}", join_code=f"J{uuid.uuid4().hex[:6].upper()}")
    db.session.add(session)
    db.session.add(SessionMember(session_id=session.id, user_id=gm_user.id))

    chars = []
    for i, role in enumerate(roles):
        u = User(id=f"u_{session_id}_{i}", username=f"P_{session_id}_{i}", password_hash="hash")
        db.session.add(u)
        db.session.add(SessionMember(session_id=session.id, user_id=u.id))
        c = Character(
            id=f"c_{session_id}_{i}",
            session_id=session.id,
            player_id=u.id,
            role_key=role,
            display_name=f"Char_{role}_{i}",
            lifepath_complete=True,
            life_goals="Live down your past life and try to forget it.",
            childhood_environment="safe_corp_zone",
            family_background="nomad_pack",
            value_most="money"
        )
        db.session.add(c)
        chars.append(c)

    db.session.commit()

    # Form a connected relationship ring
    n = len(chars)
    for i in range(n):
        c1 = chars[i]
        c2 = chars[(i + 1) % n]
        r = Relationship(
            id=f"rel_{session_id}_{i}",
            session_id=session.id,
            source_character_id=c1.id,
            target_character_id=c2.id,
            rel_type="rivals",
            is_symmetric=True,
            description_source="You are rivals.",
            description_target="You are rivals."
        )
        db.session.add(r)
    db.session.commit()
    return session, chars
