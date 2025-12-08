import os
from flask import Flask

def create_app():
    # Base directory of the project
    base_dir = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))

    # Correct paths to your frontend
    template_path = os.path.join(base_dir, "app", "web", "frontend")
    static_path = template_path  # icon.png is also here

    app = Flask(
        __name__,
        template_folder=template_path,
        static_folder=static_path
    )

    # Register routes AFTER app is created
    from app.routes import bp
    app.register_blueprint(bp)

    return app
