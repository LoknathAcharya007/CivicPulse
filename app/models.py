"""Database models (SQLite through SQLAlchemy). The ORM always uses bound parameters, so values typed by
users are never pasted into SQL text. CHECK constraints repeat the most important business rules
(valid grades, upgrade-only improvements, matching units) inside the database itself."""
from datetime import UTC, datetime

from flask_login import UserMixin

from .constants import CATEGORIES, GRADE_ORDER, ROLE_ADMIN, ROLES, TOWN_CLASSES, UNITS
from .extensions import db


def utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _in(column, values):
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


GRADE_LIST = "".join(GRADE_ORDER)  # 'EDCBA' - position in this string = how good the grade is
UNIT_RULE = " OR ".join(f"(category = '{c}' AND unit = '{u}')" for c, u in UNITS.items())


def upgrade_rule(current_col, target_col):
    return f"instr('{GRADE_LIST}', {target_col}) > instr('{GRADE_LIST}', {current_col})"


# Which towns a Municipal Official may access (many-to-many).
user_towns = db.Table(
    "user_towns",
    db.Column("user_id", db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    db.Column("town_id", db.Integer, db.ForeignKey("towns.id", ondelete="CASCADE"), primary_key=True),
)


class Town(db.Model):
    __tablename__ = "towns"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)
    town_class = db.Column(db.String(40), nullable=False)
    in_coalfield = db.Column(db.Boolean, nullable=False)
    population_2011 = db.Column(db.Integer)
    area_km2 = db.Column(db.Float)
    district = db.Column(db.String(80), nullable=False)
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)

    assets = db.relationship("Asset", back_populates="town", lazy="dynamic")
    __table_args__ = (db.CheckConstraint(_in("town_class", TOWN_CLASSES), name="ck_town_class"),)

    def __repr__(self):
        return f"<Town {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(40), unique=True, nullable=False, index=True)
    full_name = db.Column(db.String(100), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)  # Argon2id hash, never the password
    role = db.Column(db.String(20), nullable=False)
    active = db.Column(db.Boolean, nullable=False, default=True)
    # Changing this number logs the user out everywhere (password reset, deactivation, role change).
    session_version = db.Column(db.Integer, nullable=False, default=1)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    last_login_at = db.Column(db.DateTime)

    towns = db.relationship("Town", secondary=user_towns, lazy="selectin", order_by="Town.name")
    __table_args__ = (db.CheckConstraint(_in("role", ROLES), name="ck_user_role"),)

    # Flask-Login: the session stores "id:session_version" so old sessions stop working after a change.
    def get_id(self):
        return f"{self.id}:{self.session_version}"

    @property
    def is_active(self):
        return bool(self.active)

    @property
    def is_admin(self):
        return self.role == ROLE_ADMIN

    @property
    def role_label(self):
        return ROLES.get(self.role, self.role)


class Asset(db.Model):
    __tablename__ = "assets"
    id = db.Column(db.Integer, primary_key=True)
    asset_code = db.Column(db.String(30), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    town_id = db.Column(db.Integer, db.ForeignKey("towns.id"), nullable=False, index=True)
    category = db.Column(db.String(40), nullable=False)
    unit = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    commissioning_year = db.Column(db.Integer, nullable=False)
    population_served = db.Column(db.Integer, nullable=False)
    current_grade = db.Column(db.String(1), nullable=False, index=True)  # copy of the latest assessment
    latitude = db.Column(db.Float)
    longitude = db.Column(db.Float)
    location_note = db.Column(db.String(200))
    is_illustrative = db.Column(db.Boolean, nullable=False, default=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    town = db.relationship("Town", back_populates="assets")
    assessments = db.relationship("Assessment", back_populates="asset", lazy="dynamic",
                                  order_by="(Assessment.assessed_on.desc(), Assessment.id.desc())")
    maintenance = db.relationship("MaintenanceRecord", back_populates="asset", lazy="dynamic",
                                  order_by="(MaintenanceRecord.performed_on.desc(), MaintenanceRecord.id.desc())")
    estimates = db.relationship("CostEstimate", back_populates="asset", lazy="dynamic",
                                order_by="CostEstimate.created_at.desc()")

    __table_args__ = (
        db.CheckConstraint(_in("category", CATEGORIES), name="ck_asset_category"),
        db.CheckConstraint(UNIT_RULE, name="ck_asset_unit_matches_category"),
        db.CheckConstraint(_in("current_grade", GRADE_ORDER), name="ck_asset_grade"),
        db.CheckConstraint("quantity > 0", name="ck_asset_quantity_positive"),
        db.CheckConstraint("population_served > 0", name="ck_asset_population_positive"),
        db.Index("ix_assets_town_category_grade", "town_id", "category", "current_grade"),
    )

    def latest_assessment(self):
        return self.assessments.first()


class Assessment(db.Model):
    """A condition inspection. Records the OBSERVED grade (an asset can deteriorate between inspections)."""
    __tablename__ = "assessments"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    assessed_on = db.Column(db.Date, nullable=False)
    grade = db.Column(db.String(1), nullable=False)
    damaged_share_pct = db.Column(db.Float, nullable=False)
    years_since_maintenance = db.Column(db.Integer, nullable=False)
    complaints_last_12m = db.Column(db.Integer, nullable=False)
    asset_age_years = db.Column(db.Integer, nullable=False)
    source = db.Column(db.String(20), nullable=False)  # seed | inspection | improvement
    notes = db.Column(db.String(500))
    predicted_priority = db.Column(db.String(20))
    predicted_group = db.Column(db.Integer)
    assessed_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    asset = db.relationship("Asset", back_populates="assessments")
    assessed_by = db.relationship("User")
    __table_args__ = (
        db.CheckConstraint(_in("grade", GRADE_ORDER), name="ck_assessment_grade"),
        db.CheckConstraint("damaged_share_pct >= 0 AND damaged_share_pct <= 100", name="ck_assessment_damage"),
        db.CheckConstraint("years_since_maintenance >= 0", name="ck_assessment_ysm"),
        db.CheckConstraint("complaints_last_12m >= 0", name="ck_assessment_complaints"),
        db.CheckConstraint("asset_age_years >= 0", name="ck_assessment_age"),
        db.CheckConstraint(_in("source", ["seed", "inspection", "improvement"]), name="ck_assessment_source"),
        db.Index("ix_assessments_asset_date", "asset_id", "assessed_on"),
    )


class MaintenanceRecord(db.Model):
    """Maintenance history. An 'improvement' must raise the grade (CHECK constraint below)."""
    __tablename__ = "maintenance_records"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False)
    performed_on = db.Column(db.Date, nullable=False)
    work_type = db.Column(db.String(20), nullable=False)  # routine | improvement
    from_grade = db.Column(db.String(1))
    to_grade = db.Column(db.String(1))
    actual_cost_inr = db.Column(db.Float)
    description = db.Column(db.String(500), nullable=False)
    source = db.Column(db.String(20), nullable=False)  # seed | user
    recorded_by_id = db.Column(db.Integer, db.ForeignKey("users.id"))
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow)

    asset = db.relationship("Asset", back_populates="maintenance")
    recorded_by = db.relationship("User")
    __table_args__ = (
        db.CheckConstraint(_in("work_type", ["routine", "improvement"]), name="ck_maint_type"),
        db.CheckConstraint(f"work_type = 'routine' OR (from_grade IS NOT NULL AND to_grade IS NOT NULL "
                           f"AND {upgrade_rule('from_grade', 'to_grade')})",
                           name="ck_maint_upgrade_only"),
        db.CheckConstraint("actual_cost_inr IS NULL OR actual_cost_inr >= 0", name="ck_maint_cost"),
        db.Index("ix_maintenance_asset_date", "asset_id", "performed_on"),
    )


class CostReference(db.Model):
    """One row of the finalized Phase 1 cost reference table, with its source and date preserved."""
    __tablename__ = "cost_references"
    id = db.Column(db.Integer, primary_key=True)
    ref_id = db.Column(db.String(10), unique=True, nullable=False)
    category = db.Column(db.String(40), nullable=False)
    item_code = db.Column(db.String(40))
    description = db.Column(db.String(300), nullable=False)
    unit = db.Column(db.String(20))
    rate_inr = db.Column(db.Float)  # NULL = rate unavailable
    rate_type = db.Column(db.String(40), nullable=False)
    verification_status = db.Column(db.String(60), nullable=False)
    used_in_cost_model = db.Column(db.Boolean, nullable=False)
    source_document = db.Column(db.String(300))
    source_url = db.Column(db.String(400))
    document_date = db.Column(db.String(40))
    effective_or_price_basis = db.Column(db.String(200))
    notes = db.Column(db.String(1000))
    __table_args__ = (db.CheckConstraint("rate_inr IS NULL OR rate_inr >= 0", name="ck_costref_rate"),)


class CostEstimate(db.Model):
    """A saved estimate. Every amount is computed on the server; the request only chooses the target grade."""
    __tablename__ = "cost_estimates"
    id = db.Column(db.Integer, primary_key=True)
    asset_id = db.Column(db.Integer, db.ForeignKey("assets.id", ondelete="CASCADE"), nullable=False, index=True)
    assessment_id = db.Column(db.Integer, db.ForeignKey("assessments.id"), nullable=False)
    created_by_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False, index=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    current_grade = db.Column(db.String(1), nullable=False)
    target_grade = db.Column(db.String(1), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    unit = db.Column(db.String(10), nullable=False)
    ml_base_cost = db.Column(db.Float, nullable=False)
    price_index_factor = db.Column(db.Float, nullable=False)
    price_adjustment = db.Column(db.Float, nullable=False)
    contingency_percent = db.Column(db.Float, nullable=False)
    contingency_amount = db.Column(db.Float, nullable=False)
    final_cost = db.Column(db.Float, nullable=False)
    reference_rate_cost = db.Column(db.Float, nullable=False)
    package_name = db.Column(db.String(200), nullable=False)
    predicted_priority = db.Column(db.String(20), nullable=False)
    predicted_group = db.Column(db.Integer, nullable=False)
    model_manifest_sha256 = db.Column(db.String(64), nullable=False)
    input_snapshot = db.Column(db.JSON, nullable=False)  # exact model inputs used
    breakdown = db.Column(db.JSON, nullable=False)       # all lines with sources and dates

    asset = db.relationship("Asset", back_populates="estimates")
    assessment = db.relationship("Assessment")
    created_by = db.relationship("User")
    __table_args__ = (
        db.CheckConstraint(_in("target_grade", GRADE_ORDER), name="ck_estimate_target_grade"),
        db.CheckConstraint(upgrade_rule("current_grade", "target_grade"), name="ck_estimate_upgrade_only"),
        db.CheckConstraint("ml_base_cost > 0 AND final_cost > 0", name="ck_estimate_positive"),
        db.CheckConstraint("contingency_percent >= 0 AND contingency_percent <= 20", name="ck_estimate_contingency"),
    )


class AuditLog(db.Model):
    """Who did what and when. Never stores passwords, hashes, tokens or secrets."""
    __tablename__ = "audit_logs"
    id = db.Column(db.Integer, primary_key=True)
    created_at = db.Column(db.DateTime, nullable=False, default=utcnow, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id"), index=True)
    username = db.Column(db.String(40))
    action = db.Column(db.String(50), nullable=False, index=True)
    outcome = db.Column(db.String(10), nullable=False)
    target_type = db.Column(db.String(30))
    target_id = db.Column(db.String(40))
    ip_address = db.Column(db.String(45))
    details = db.Column(db.String(500))
    __table_args__ = (db.CheckConstraint(_in("outcome", ["success", "denied", "failure"]), name="ck_audit_outcome"),)
