"""Upload model for tracking file uploads."""
from datetime import datetime
from app.extensions import db


class Upload(db.Model):
    __tablename__ = "uploads"
    id = db.Column(db.Integer, primary_key=True)
    filename = db.Column(db.String(512), nullable=False)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)
    row_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(32), default="ok")  # ok/error/processing
    invoice_month = db.Column(db.Integer, nullable=True)
    invoice_year = db.Column(db.Integer, nullable=True)
    error_message = db.Column(db.Text, nullable=True)

    def to_dict(self):
        return {
            "id": self.id, "filename": self.filename,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
            "row_count": self.row_count, "status": self.status,
            "invoice_month": self.invoice_month, "invoice_year": self.invoice_year,
        }
