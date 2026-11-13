from flask import Flask
from dotenv import load_dotenv
import os

load_dotenv()

from routes.auth import auth_bp
from routes.secretary import secretary_bp
from routes.director import director_bp
from routes.teacher import teacher_bp
from routes.admin import admin_bp
from services.db import close_db
from database.init_db import init_db

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'SIGE_SECRET_KEY_PROD_MOZ_2026')

init_db()

app.register_blueprint(auth_bp)
app.register_blueprint(secretary_bp)
app.register_blueprint(director_bp)
app.register_blueprint(teacher_bp)
app.register_blueprint(admin_bp)

@app.teardown_appcontext
def teardown_db(exception):
    close_db(exception)

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
