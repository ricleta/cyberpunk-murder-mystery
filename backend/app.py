import re

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from config import Config, DEFAULT_SECRET_KEY, DEFAULT_JWT_SECRET_KEY
from extensions import db, migrate, cors
from generation.name_tokens import RAW_TOKEN_RE
from services.errors import ApiError

# Dev/test default: any localhost port.
LOCAL_ORIGIN_RE = re.compile(r'^https?://(localhost|127\.0\.0\.1)(:\d+)?$')


def _iter_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for k, v in value.items():
            yield from _iter_strings(k)
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def _check_production_config(app):
    """Refuse to start in production with insecure defaults. Debug/testing are exempt."""
    if app.debug or app.testing:
        return
    problems = []
    if app.config.get('SECRET_KEY') == DEFAULT_SECRET_KEY:
        problems.append('SECRET_KEY is still the built-in development default')
    if app.config.get('JWT_SECRET_KEY') == DEFAULT_JWT_SECRET_KEY:
        problems.append('JWT_SECRET_KEY is still the built-in development default')
    if not app.config.get('CORS_ORIGINS'):
        problems.append('CORS_ORIGINS is not set (comma-separated list of allowed origins, e.g. https://you.github.io)')
    if problems:
        raise RuntimeError('Refusing to start: ' + '; '.join(problems) + '. See DEPLOY.md.')


def _cors_origins(app):
    configured = app.config.get('CORS_ORIGINS')
    if configured:
        origins = [o.strip().rstrip('/') for o in configured.split(',') if o.strip()]
        if '*' in origins and not (app.debug or app.testing):
            raise RuntimeError("Refusing to start: CORS_ORIGINS must list explicit origins, not '*'. See DEPLOY.md.")
        return origins
    return [LOCAL_ORIGIN_RE]


def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    _check_production_config(app)

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app, resources={r'/api/*': {'origins': _cors_origins(app)}})

    # Validate data files and templates at startup (fail loudly)
    from generation.secret_templates import validate_secret_templates
    from generation.objective_templates import validate_objective_templates
    from services.lifepath_data import load_lifepath_data
    with app.app_context():
        load_lifepath_data(reload=True)
        validate_secret_templates()
        validate_objective_templates()
    from routes.auth_routes import bp as auth_bp
    from routes.session_routes import bp as session_bp
    from routes.character_routes import bp as character_bp
    from routes.generation_routes import bp as generation_bp
    from routes.content_routes import bp as content_bp
    from routes.lifepath_routes import bp as lifepath_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(session_bp, url_prefix='/api/sessions')
    app.register_blueprint(character_bp, url_prefix='/api/sessions') # Prefix shared with sessions
    app.register_blueprint(generation_bp, url_prefix='/api/sessions')
    app.register_blueprint(content_bp, url_prefix='/api/sessions')
    app.register_blueprint(lifepath_bp, url_prefix='/api/lifepath-data')

    @app.route('/api/health')
    def health_check():
        return jsonify({'status': 'ok'})

    if app.testing:
        # Tripwire: any raw {{c:<id>}} token in a JSON response means a serializer forgot to
        # resolve names. Raising here turns every endpoint test into a leak test.
        @app.after_request
        def fail_on_raw_name_tokens(response):
            if app.config.get('NAME_TOKEN_TRIPWIRE', True) and response.is_json:
                for text in _iter_strings(response.get_json(silent=True)):
                    if RAW_TOKEN_RE.search(text):
                        raise AssertionError(f'Raw name token leaked in {request.method} {request.path}: {text!r}')
            return response

    @app.errorhandler(ApiError)
    def handle_api_error(e):
        return e.to_response()

    @app.errorhandler(Exception)
    def handle_unexpected(e):
        if isinstance(e, HTTPException):
            if request.path.startswith('/api'):
                return jsonify({'error': e.description or e.name}), e.code
            return e
        db.session.rollback()
        if not request.path.startswith('/api'):
            raise e
        app.logger.exception('Unhandled error on %s %s', request.method, request.path)
        return jsonify({'error': 'Internal server error'}), 500

    return app
