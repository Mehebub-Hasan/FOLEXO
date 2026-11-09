"""Vendor name normalization + find-or-create matching.

Normalization is the single source of truth for `vendors.name_norm` across
the whole system (extraction orchestrator, manual creation, this matcher):
lowercase, whitespace-collapsed, common corporate suffixes stripped so
"Acme GmbH", "Acme Ltd" and "Acme Inc" deduplicate to one vendor.
"""

import logging
import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Vendor

logger = logging.getLogger(__name__)

# Trailing legal-form tokens ignored during normalization (dots optional).
CORPORATE_SUFFIXES = {
    "inc", "incorporated", "llc", "llp", "lp", "ltd", "limited",
    "gmbh", "corp", "corporation", "co", "company", "plc", "ag",
    "sa", "bv", "nv", "pty", "srl", "spa", "oy", "ab", "as",
    "kg", "gbr", "ug",
}

_WHITESPACE = re.compile(r"\s+")


def normalize_vendor_name(raw_name: str) -> str:
    """Lowercase, collapse whitespace, strip trailing corporate suffixes.

    Single-token names are left intact (a company literally named "Co"
    should not normalize to an empty string).
    """
    tokens = _WHITESPACE.sub(" ", raw_name.strip().lower()).split(" ")
    while len(tokens) > 1 and tokens[-1].strip(".") in CORPORATE_SUFFIXES:
        tokens.pop()
    return " ".join(tokens).strip()


async def find_or_create_vendor(
    db: AsyncSession,
    org_id: uuid.UUID,
    extracted_name: str | None,
    tax_id: str | None,
    *,
    created_by_id: uuid.UUID | None = None,
    default_currency: str | None = None,
) -> Vendor | None:
    """Match an extracted vendor to the directory, creating one when unknown.

    Order: tax-id exact match → normalized-name match → create. Returns None
    only when there is neither a usable name nor a tax id.
    """
    cleaned_tax_id = tax_id.strip() if tax_id else None
    cleaned_name = normalize_vendor_name(extracted_name) if extracted_name else ""

    if cleaned_tax_id:
        vendor = (
            await db.execute(
                select(Vendor).where(
                    Vendor.organization_id == org_id,
                    Vendor.tax_id == cleaned_tax_id,
                )
            )
        ).scalar_one_or_none()
        if vendor is not None:
            return vendor

    if cleaned_name:
        vendor = (
            await db.execute(
                select(Vendor).where(
                    Vendor.organization_id == org_id,
                    Vendor.name_norm == cleaned_name,
                )
            )
        ).scalar_one_or_none()
        if vendor is not None:
            return vendor

        vendor = Vendor(
            organization_id=org_id,
            name=extracted_name.strip(),
            name_norm=cleaned_name,
            tax_id=cleaned_tax_id,
            default_currency=default_currency,
            created_by_id=created_by_id,
        )
        db.add(vendor)
        await db.flush()
        logger.info("auto-created vendor '%s' (org %s)", cleaned_name, org_id)
        return vendor

    return None
