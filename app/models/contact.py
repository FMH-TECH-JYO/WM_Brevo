"""Contact model for imported contact records."""
from datetime import datetime
from app.extensions import db


class Contact(db.Model):
    __tablename__ = "contacts"
    id = db.Column(db.Integer, primary_key=True)
    source_file = db.Column(db.String(512), default="")
    name = db.Column(db.String(512), nullable=False)
    email = db.Column(db.String(255), nullable=False)
    branch = db.Column(db.String(255), default="")
    sales_rep = db.Column(db.String(255), default="")
    department = db.Column(db.String(255), default="")
    company = db.Column(db.String(512), default="")
    title = db.Column(db.String(255), default="")
    raw_row = db.Column(db.Text, default="")
    imported_at = db.Column(db.DateTime, default=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "source_file": self.source_file, "name": self.name,
            "email": self.email, "branch": self.branch, "sales_rep": self.sales_rep,
            "department": self.department, "company": self.company, "title": self.title,
            "imported_at": self.imported_at.isoformat() if self.imported_at else None,
        }
