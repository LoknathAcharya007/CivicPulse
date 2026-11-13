"""Command-line tools:  flask --app wsgi init-db | create-user | verify-models"""
import secrets

import click
from flask import current_app

from .constants import ROLE_ADMIN, ROLE_OFFICIAL, ROLES
from .extensions import db
from .models import Town, User
from .security import hash_password, password_problems
from .seed import seed_database
from .services.costing import check_reference_consistency, load_references

DEMO_USERS = [
    ("admin", "Demo Administrator", ROLE_ADMIN, []),
    ("official.andal", "Demo Official - Andal area", ROLE_OFFICIAL, ["Andal", "Ukhra", "Dignala"]),
    ("official.asansol", "Demo Official - Asansol", ROLE_OFFICIAL, ["Asansol"]),
]


def _new_user(username, full_name, role, town_names, password):
    towns = Town.query.filter(Town.name.in_(town_names)).all() if town_names else []
    if len(towns) != len(set(town_names)):
        raise click.ClickException(f"Unknown town in {town_names}")
    user = User(username=username, full_name=full_name, role=role, password_hash=hash_password(password),
                towns=towns if role == ROLE_OFFICIAL else [])
    db.session.add(user)
    return user


def register_cli(app):
    @app.cli.command("init-db")
    @click.option("--reset", is_flag=True, help="Delete ALL existing data first.")
    @click.option("--demo-users", is_flag=True, help="Create demo accounts with random one-time passwords.")
    def init_db(reset, demo_users):
        """Create tables and load the illustrative demonstration data."""
        if reset:
            click.confirm("This deletes every record in the database. Continue?", abort=True)
            db.drop_all()
        db.create_all()
        if Town.query.first() is not None:
            raise click.ClickException("Database already contains data. Use --reset to start again.")
        counts = seed_database(current_app.extensions["civicpulse_ml"])
        click.echo(f"Seeded: {counts} (all assets are illustrative synthetic records)")
        if demo_users:
            click.echo("\nDemo accounts - passwords are shown ONCE; store them safely:")
            for username, name, role, towns in DEMO_USERS:
                password = secrets.token_urlsafe(12)
                while password_problems(password, username):
                    password = secrets.token_urlsafe(12)
                _new_user(username, name, role, towns, password)
                click.echo(f"  {username:18s} {ROLES[role]:20s} {', '.join(towns) or 'all towns':28s} {password}")
            db.session.commit()

    @app.cli.command("create-user")
    @click.option("--username", required=True)
    @click.option("--full-name", required=True)
    @click.option("--role", type=click.Choice(list(ROLES)), required=True)
    @click.option("--towns", default="", help="Comma-separated town names (officials only).")
    @click.password_option()
    def create_user(username, full_name, role, towns, password):
        """Create an account (the password is typed, never passed on the command line)."""
        username = username.strip().lower()
        if User.query.filter_by(username=username).first():
            raise click.ClickException("Username already exists")
        problems = password_problems(password, username)
        if problems:
            raise click.ClickException("Password needs: " + ", ".join(problems))
        town_names = [t.strip() for t in towns.split(",") if t.strip()]
        if role == ROLE_OFFICIAL and not town_names:
            raise click.ClickException("An official needs at least one town (--towns)")
        _new_user(username, full_name, role, town_names, password)
        db.session.commit()
        click.echo(f"Created {ROLES[role]} '{username}'")

    @app.cli.command("verify-models")
    def verify_models():
        """Re-check artifact checksums, golden samples and cost reference consistency."""
        from .services.ml_service import ModelService
        ml = ModelService(current_app.config["ARTIFACT_DIR"], current_app.config["ARTIFACT_MANIFEST_SHA256"])
        status = ml.status()
        click.echo(f"MANIFEST sha256 : {status['manifest_sha256']}")
        click.echo(f"Files verified  : {status['files_verified']}")
        click.echo(f"Golden samples  : {status['golden']}")
        click.echo(f"Installed       : {status['installed']}")
        if status["version_notes"]:
            click.echo(f"Version notes   : {status['version_notes']}")
        problems = check_reference_consistency(load_references(), ml.spec)
        click.echo("Cost references : " + ("consistent with Phase 1" if not problems else "; ".join(problems)))
        if problems:
            raise click.ClickException("Cost references inconsistent")
