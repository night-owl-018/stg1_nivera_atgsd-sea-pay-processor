from flask import Flask

def create_app():
    app = Flask(
        __name__,
        template_folder="web/frontend",
        static_folder="web/frontend"
    )

    from .routes import bp
    app.register_blueprint(bp)

    return app
