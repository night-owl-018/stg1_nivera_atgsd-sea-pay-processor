from flask import Flask
import os

def create_app():
    # Resolve absolute path to THIS folder (app/)
    base_dir = os.path.abspath(os.path.dirname(__file__))

    # Point Flask to your actual index.html folder
    template_path = os.path.join(base_dir, "web", "frontend")
    static_path = template_path   # index.html + icon.png located here

    app = Flask(
        __name__,
        template_folder=template_path,
        static_folder=static_path
    )

    # Register routes AFTER creating the app
    from .routes import bp
    app.register_blueprint(bp)

    return app
