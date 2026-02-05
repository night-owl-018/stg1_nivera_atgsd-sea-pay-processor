from flask import Flask
import os

def create_app():
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    TEMPLATE_DIR = os.path.join(BASE_DIR, "web", "frontend")

    app = Flask(
        __name__,
        template_folder=TEMPLATE_DIR,
        static_folder=TEMPLATE_DIR  # so CSS/JS/images also work
    )

    from .routes import bp
    app.register_blueprint(bp)

    return app
