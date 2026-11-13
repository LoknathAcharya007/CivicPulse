"""WTForms classes. Flask-WTF adds a CSRF token to every form automatically."""
from datetime import date

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    DateField,
    FloatField,
    IntegerField,
    PasswordField,
    RadioField,
    SelectField,
    SelectMultipleField,
    StringField,
    TextAreaField,
)
from wtforms.fields.choices import SelectChoice
from wtforms.validators import (
    DataRequired,
    EqualTo,
    InputRequired,
    Length,
    NumberRange,
    Optional,
    Regexp,
    ValidationError,
)

from .constants import GRADE_NAMES, GRADE_ORDER, ROLES


def grade_choices(grades):
    return [SelectChoice(g, f"{g} - {GRADE_NAMES[g]}") for g in grades]


def not_in_future(form, field):
    if field.data and field.data > date.today():
        raise ValidationError("Date cannot be in the future.")


class LoginForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), Length(max=40)])
    password = PasswordField("Password", validators=[DataRequired(), Length(max=128)])


class ActionForm(FlaskForm):
    """Empty form used only for its CSRF token (logout and other POST buttons)."""


class InspectionForm(FlaskForm):
    assessed_on = DateField("Inspection date", validators=[DataRequired(), not_in_future])
    grade = SelectField("Observed condition grade", choices=grade_choices(reversed(GRADE_ORDER)),
                        validators=[DataRequired()])
    damaged_share_pct = FloatField("Damaged share (%)", validators=[InputRequired(), NumberRange(0, 100)])
    years_since_maintenance = IntegerField("Years since last maintenance",
                                           validators=[InputRequired(), NumberRange(0, 100)])
    complaints_last_12m = IntegerField("Complaints in the last 12 months",
                                       validators=[InputRequired(), NumberRange(0, 1000)])
    notes = TextAreaField("Notes", validators=[Optional(), Length(max=500)])


class ImprovementForm(FlaskForm):
    """to_grade choices are set per asset to the better grades only; the service checks again."""
    performed_on = DateField("Completion date", validators=[DataRequired(), not_in_future])
    to_grade = SelectField("New condition grade", choices=[], validators=[DataRequired()])
    damaged_share_pct = FloatField("Damaged share after the work (%)",
                                   validators=[InputRequired(), NumberRange(0, 100)])
    description = TextAreaField("Work done", validators=[DataRequired(), Length(min=5, max=500)])
    actual_cost_inr = FloatField("Actual cost (₹, optional)", validators=[Optional(), NumberRange(0, 1e10)])


class EstimateForm(FlaskForm):
    """Only the target grade is accepted from the browser. Every amount is computed on the server."""
    target_grade = RadioField("Target condition grade", choices=[], validators=[DataRequired()])


USERNAME_RULE = Regexp(r"^[a-z0-9_.-]{3,40}$", message="3-40 characters: lowercase letters, digits, . _ -")


class UserCreateForm(FlaskForm):
    username = StringField("Username", validators=[DataRequired(), USERNAME_RULE])
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=100)])
    role = SelectField("Role", choices=[SelectChoice(k, v) for k, v in ROLES.items()], validators=[DataRequired()])
    towns = SelectMultipleField("Permitted towns (officials)", coerce=int, choices=[])
    password = PasswordField("Initial password", validators=[DataRequired(), Length(max=128)])
    confirm = PasswordField("Repeat password", validators=[DataRequired(), EqualTo("password", "Passwords differ")])


class UserEditForm(FlaskForm):
    full_name = StringField("Full name", validators=[DataRequired(), Length(min=2, max=100)])
    role = SelectField("Role", choices=[SelectChoice(k, v) for k, v in ROLES.items()], validators=[DataRequired()])
    towns = SelectMultipleField("Permitted towns (officials)", coerce=int, choices=[])
    active = BooleanField("Account active")


class PasswordResetForm(FlaskForm):
    password = PasswordField("New password", validators=[DataRequired(), Length(max=128)])
    confirm = PasswordField("Repeat password", validators=[DataRequired(), EqualTo("password", "Passwords differ")])
