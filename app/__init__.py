from flask import Flask

def create_app():
    # The Flask app points to your frontend templates folder
    app = Flask(__name__, template_folder="web/frontend")

    # Import and register your routes blueprint
    from app.routes import bp
    app.register_blueprint(bp)

    return app
