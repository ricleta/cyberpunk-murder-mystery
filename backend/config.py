import os

DEFAULT_SECRET_KEY = 'dev-secret-key'
DEFAULT_JWT_SECRET_KEY = 'jwt-dev-secret-key'


class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or DEFAULT_SECRET_KEY
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or 'sqlite:///cyberpunk.db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    JWT_SECRET_KEY = os.environ.get('JWT_SECRET_KEY') or DEFAULT_JWT_SECRET_KEY
    JWT_ACCESS_TOKEN_EXPIRES = 7 * 24 * 60 * 60  # 7 days in seconds
    # Comma-separated list of allowed browser origins, e.g. https://you.github.io
    CORS_ORIGINS = os.environ.get('CORS_ORIGINS') or None
