"""Mapping models: SalesRep, Branch, Customer email, Invoice team."""
from datetime import datetime
from app.extensions import db


class SalesRepMapping(db.Model):
    __tablename__ = "sales_rep_mappings"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    emails = db.Column(db.Text, default="")
    branch = db.Column(db.String(255), default="")
    source = db.Column(db.String(64), default="manual")
    status = db.Column(db.String(32), default="active")
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "name": self.name, "emails": self.emails,
            "branch": self.branch, "source": self.source, "status": self.status,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class BranchMapping(db.Model):
    __tablename__ = "branch_mappings"
    id = db.Column(db.Integer, primary_key=True)
    branch = db.Column(db.String(255), unique=True, nullable=False)
    emails = db.Column(db.Text, default="")
    source = db.Column(db.String(64), default="manual")
    status = db.Column(db.String(32), default="active")
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "branch": self.branch, "emails": self.emails,
            "source": self.source, "status": self.status,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class CustomerMapping(db.Model):
    __tablename__ = "customer_mappings"
    id = db.Column(db.Integer, primary_key=True)
    customer = db.Column(db.String(512), unique=True, nullable=False)
    emails = db.Column(db.Text, default="")
    source = db.Column(db.String(64), default="manual")
    status = db.Column(db.String(32), default="active")
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "customer": self.customer, "emails": self.emails,
            "source": self.source, "status": self.status,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }


class InvoiceTeamEmail(db.Model):
    __tablename__ = "invoice_team_emails"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    source = db.Column(db.String(64), default="manual")
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id, "email": self.email, "source": self.source,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }
