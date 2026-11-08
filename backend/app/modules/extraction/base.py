"""Provider-agnostic extractor interface (ADR-003).

Both stages implement this: Azure Document Intelligence (primary) and the
OpenAI structured-output fallback. The optional `context` carries data from
the primary pass into the fallback (OCR text + primary output) without
changing the call shape for primary extractors.
"""

from abc import ABC, abstractmethod
from typing import Any

from app.modules.extraction.schemas import ExtractedInvoiceData


class BaseInvoiceExtractor(ABC):
    @abstractmethod
    async def extract(
        self,
        document_bytes: bytes,
        mime_type: str,
        context: dict[str, Any] | None = None,
    ) -> ExtractedInvoiceData:
        """Extract and normalize one invoice document.

        Implementations MUST NOT fabricate values: anything the provider did
        not produce with reasonable confidence stays None.
        """
