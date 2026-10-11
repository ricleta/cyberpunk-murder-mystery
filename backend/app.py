from flask import Flask, jsonify
from config import Config
from extensions import db, migrate, cors

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    db.init_app(app)
    migrate.init_app(app, db)
    cors.init_app(app) # For production we will restrict this to specific origins

    # Validate secret and objective templates at startup (fail loudly)
    from generation.secret_templates import validate_secret_templates
    from generation.objective_templates import validate_objective_templates
    with app.app_context():
        validate_secret_templates()
        validate_objective_templates()
    from routes.auth_routes import bp as auth_bp
    from routes.session_routes import bp as session_bp
    from routes.character_routes import bp as character_bp
    from routes.lifepath_routes import bp as lifepath_bp

    app.register_blueprint(auth_bp, url_prefix='/api/auth')
    app.register_blueprint(session_bp, url_prefix='/api/sessions')
    app.register_blueprint(character_bp, url_prefix='/api/sessions') # Prefix shared with sessions
    app.register_blueprint(lifepath_bp, url_prefix='/api/lifepath-data')

    @app.route('/api/health')
    def health_check():
        return jsonify({'status': 'ok'})

    return app
