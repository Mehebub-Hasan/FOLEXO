"""Model registry — importing this module populates ``Base.metadata``.

This is the single import point used by Alembic (alembic/env.py). Import
order matters only for readability; SQLAlchemy resolves cross-table
references (e.g. ExtractionRun → invoices) from table-name strings at
mapper configuration time, so there are no import cycles.
"""

from app.models.audit import AuditLog
from app.models.document import DocumentFile
from app.models.extraction import (
    ExtractionEngine,
    ExtractionRun,
    ExtractionRunStatus,
)
from app.models.invoice import Invoice, InvoiceItem, InvoiceStatus
from app.models.organization import (
    Organization,
    OrganizationMember,
    OrganizationRole,
)
from app.models.vendor import Vendor

__all__ = [
    "AuditLog",
    "DocumentFile",
    "ExtractionEngine",
    "ExtractionRun",
    "ExtractionRunStatus",
    "Invoice",
    "InvoiceItem",
    "InvoiceStatus",
    "Organization",
    "OrganizationMember",
    "OrganizationRole",
    "Vendor",
]
