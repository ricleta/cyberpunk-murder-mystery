from datetime import datetime
import uuid
from extensions import db

def generate_uuid():
    return uuid.uuid4().hex

class User(db.Model):
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    email = db.Column(db.String, unique=True, nullable=False)
    password_hash = db.Column(db.String, nullable=False)
    display_name = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Session(db.Model):
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    gm_id = db.Column(db.String, db.ForeignKey('user.id'), nullable=False)
    name = db.Column(db.String, nullable=False)
    join_code = db.Column(db.String, unique=True, nullable=False)
    status = db.Column(db.String, default='lobby')  # 'lobby' | 'generated' | 'active' | 'closed'
    auto_relationships = db.Column(db.Boolean, default=True)
    scenario_pack_id = db.Column(db.String, default='arasaka_soiree_v1')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class SessionMember(db.Model):
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    session_id = db.Column(db.String, db.ForeignKey('session.id'), nullable=False)
    user_id = db.Column(db.String, db.ForeignKey('user.id'), nullable=False)
    joined_at = db.Column(db.DateTime, default=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('session_id', 'user_id'),)

class Character(db.Model):
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    session_id = db.Column(db.String, db.ForeignKey('session.id'), nullable=False)
    player_id = db.Column(db.String, db.ForeignKey('user.id'), nullable=False)
    role_key = db.Column(db.String, nullable=True)
    is_killer = db.Column(db.Boolean, default=False)
    display_name = db.Column(db.String, nullable=True)
    description = db.Column(db.Text, default='')
    traits = db.Column(db.JSON, default=list)
    secrets = db.Column(db.JSON, default=list)
    objectives = db.Column(db.JSON, default=list)
    
    # Shared Lifepath fields
    cultural_origin = db.Column(db.String, nullable=True)
    personality = db.Column(db.String, nullable=True)
    clothing_style = db.Column(db.String, nullable=True)
    hairstyle = db.Column(db.String, nullable=True)
    affectation = db.Column(db.String, nullable=True)
    value_most = db.Column(db.String, nullable=True)
    feelings_about_people = db.Column(db.String, nullable=True)
    most_valued_person = db.Column(db.String, nullable=True)
    most_valued_possession = db.Column(db.String, nullable=True)
    family_background = db.Column(db.String, nullable=True)
    childhood_environment = db.Column(db.String, nullable=True)
    family_crisis = db.Column(db.String, nullable=True)
    life_goals = db.Column(db.String, nullable=True)

    # Custom faction-based Friends/Enemies
    friend_faction_type = db.Column(db.String, nullable=True)
    friend_faction_name = db.Column(db.String, nullable=True)
    friend_faction_feeling = db.Column(db.String, nullable=True)
    enemy_faction_type = db.Column(db.String, nullable=True)
    enemy_faction_name = db.Column(db.String, nullable=True)
    enemy_faction_feeling = db.Column(db.String, nullable=True)

    # Role-specific Lifepath fields
    role_specific_lifepath = db.Column(db.JSON, default=dict)
    
    # Completion flag
    lifepath_complete = db.Column(db.Boolean, default=False)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    __table_args__ = (db.UniqueConstraint('session_id', 'player_id'),)

class Relationship(db.Model):
    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    session_id = db.Column(db.String, db.ForeignKey('session.id'), nullable=False)
    source_character_id = db.Column(db.String, db.ForeignKey('character.id'), nullable=False)
    target_character_id = db.Column(db.String, db.ForeignKey('character.id'), nullable=False)
    rel_type = db.Column(db.String, nullable=False)
    is_symmetric = db.Column(db.Boolean, nullable=False)
    description_source = db.Column(db.Text, nullable=True)
    description_target = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        db.CheckConstraint('source_character_id != target_character_id', name='no_self_loops'),
        db.UniqueConstraint('source_character_id', 'target_character_id', name='one_relationship_per_pair'),
        db.Index('idx_relationship_source', 'source_character_id'),
        db.Index('idx_relationship_target', 'target_character_id')
    )
