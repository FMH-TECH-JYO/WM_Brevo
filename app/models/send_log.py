"""SendLog model for recording sent email history."""
from datetime import datetime
from app.extensions import db


class SendLog(db.Model):
    __tablename__ = "send_logs"
    id = db.Column(db.Integer, primary_key=True)
    job_id = db.Column(db.Integer, db.ForeignKey("email_jobs.id"), nullable=True)
    customer = db.Column(db.String(512), nullable=False)
    to_emails = db.Column(db.Text, default="")
    cc_emails = db.Column(db.Text, default="")
    subject = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, default=datetime.utcnow)
    status = db.Column(db.String(32), default="sent")  # sent/failed/skipped
    attempts = db.Column(db.Integer, default=1)
    error_code = db.Column(db.String(64), nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "job_id": self.job_id, "customer": self.customer,
            "to_emails": self.to_emails, "cc_emails": self.cc_emails,
            "subject": self.subject,
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
            "status": self.status, "attempts": self.attempts,
            "error_code": self.error_code, "error_message": self.error_message,
        }
