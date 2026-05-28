"""
app/__init__.py — CamWatch
Flask application factory.
"""

from flask import Flask
from .models import db


def create_app(config=None):
    app = Flask(__name__)

    # Carrega configurações do Config (lê .env via dotenv)
    from config import Config
    app.config.from_object(Config)

    if config:
        app.config.update(config)

    # Inicializa extensões
    db.init_app(app)

    # Registra blueprints
    from .routes.monitor  import monitor_bp
    from .routes.cadastro import cadastro_bp
    app.register_blueprint(monitor_bp)
    app.register_blueprint(cadastro_bp, url_prefix="/cadastro")

    return app