"""Entry point:  flask --app wsgi run   (or any WSGI server: wsgi:app)."""
from app import create_app

app = create_app()
