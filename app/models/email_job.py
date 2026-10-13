"""EmailJob and EmailAttempt models for persistent job tracking."""
import json
from datetime import datetime
from app.extensions import db


class EmailJob(db.Model):
    __tablename__ = "email_jobs"
    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(64), unique=True, nullable=False, index=True)
    status = db.Column(db.String(32), default="pending")  # pending/running/done/failed/skipped
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    started_at = db.Column(db.DateTime, nullable=True)
    completed_at = db.Column(db.DateTime, nullable=True)
    attempt_count = db.Column(db.Integer, default=0)
    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    customer = db.Column(db.String(512), nullable=False)
    recipients_json = db.Column(db.Text, default="[]")
    cc_json = db.Column(db.Text, default="[]")
    subject = db.Column(db.Text, nullable=False)
    body_html = db.Column(db.Text, nullable=False)
    invoice_month = db.Column(db.Integer, nullable=True)
    invoice_year = db.Column(db.Integer, nullable=True)
    batch_id = db.Column(db.String(64), nullable=True, index=True)

    attempts = db.relationship("EmailAttempt", back_populates="job", cascade="all, delete-orphan")

    @property
    def to_list(self):
        try:
            return json.loads(self.recipients_json or "[]")
        except Exception:
            return []

    @property
    def cc_list(self):
        try:
            return json.loads(self.cc_json or "[]")
        except Exception:
            return []

    def to_dict(self):
        return {
            "id": self.id,
            "idempotency_key": self.idempotency_key,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "attempt_count": self.attempt_count,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "customer": self.customer,
            "to": self.to_list,
            "cc": self.cc_list,
            "subject": self.subject,
            "invoice_month": self.invoice_month,
            "invoice_year": self.invoice_year,
            "batch_id": self.batch_id,
        }


class EmailAttempt(db.Model):
    __tablename__ = "email_attempts"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("email_jobs.id"), nullable=False, index=True)
    attempted_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(32))  # ok/failed
    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)
    smtp_response = db.Column(db.Text, nullable=True)

    job = db.relationship("EmailJob", back_populates="attempts")

    def to_dict(self):
        return {
            "id": self.id, "job_id": self.job_id,
            "attempted_at": self.attempted_at.isoformat() if self.attempted_at else None,
            "status": self.status, "error_code": self.error_code,
            "error_message": self.error_message, "smtp_response": self.smtp_response,
        }
