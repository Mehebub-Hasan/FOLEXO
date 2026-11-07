"""DocumentFile model — metadata for uploaded source documents.

Bytes live in Supabase Storage (private ``invoices`` bucket, path prefix
``{organization_id}/…``); this row is the system-of-record metadata and the
dedup index (content hash per organization, FR-2).
"""

import uuid

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base, TimestampMixin


class DocumentFile(Base, TimestampMixin):
    __tablename__ = "document_files"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(), primary_key=True, default=uuid.uuid4, server_default=text("gen_random_uuid()")
    )
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    storage_path: Mapped[str] = mapped_column(Text(), nullable=False)
    original_filename: Mapped[str] = mapped_column(Text(), nullable=False)
    mime_type: Mapped[str] = mapped_column(Text(), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger(), nullable=False)
    page_count: Mapped[int | None] = mapped_column()
    # sha256 hex of the file bytes (64 chars)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    uploaded_by_id: Mapped[uuid.UUID] = mapped_column(UUID(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "file_size_bytes > 0", name="ck_document_files_size_positive"
        ),
        Index(
            "ix_document_files_org_content_hash",
            "organization_id",
            "content_hash",
        ),
    )
