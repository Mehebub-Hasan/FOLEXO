"""Phase 4 step 2 extraction pipeline tests.

Case A: high-confidence Azure pass → fallback never invoked.
Case B: low-confidence Azure pass → fallback reconciles; audit marks it.
Case C: provider failure → the task transitions the invoice to FAILED and
        records the error.

Plus: Azure SDK-response mapping (polygon normalization, Decimal/date
typing), OpenAI structured-output conversion, the gate boundary, and
vendor reuse. All provider SDKs are stubbed — no network.
"""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.models import (
    DocumentFile,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceItem,
    InvoiceStatus,
    Vendor,
)
from app.modules.extraction.azure_extractor import AzureInvoiceExtractor
from app.modules.extraction.base import BaseInvoiceExtractor
from app.modules.extraction.openai_extractor import (
    OpenAIFallbackExtractor,
    OpenAINormalizedInvoice,
    OpenAILineItem,
)
from app.modules.extraction.orchestrator import ExtractionOrchestrator
from app.modules.extraction.schemas import (
    ExtractedInvoiceData,
    ExtractedLineItem,
    ExtractionError,
    ExtractorConfigurationError,
)
from app.workers.tasks import process_invoice_task
from tests.test_uploads import seed_invoice


# ── Fixtures and helpers ─────────────────────────────────────────────────


def make_data(**overrides) -> ExtractedInvoiceData:
    base: dict = dict(
        vendor_name="Acme GmbH",
        vendor_tax_id="DE811907980",
        invoice_number="INV-1042",
        invoice_date=date(2026, 10, 28),
        due_date=date(2026, 11, 27),
        currency="EUR",
        subtotal=Decimal("1240.0000"),
        tax_amount=Decimal("248.0000"),
        total_amount=Decimal("1488.0000"),
        line_items=[
            ExtractedLineItem(
                description="Consulting",
                quantity=Decimal("10"),
                unit_price=Decimal("100"),
                line_total=Decimal("1000"),
                sku="CON-10",
                page_number=1,
                confidence=0.95,
                bounding_box=[[0.1, 0.2], [0.3, 0.2], [0.3, 0.25], [0.1, 0.25]],
            ),
            ExtractedLineItem(
                description="Support",
                quantity=Decimal("2"),
                unit_price=Decimal("120"),
                line_total=Decimal("240"),
                sku="SUP-2",
                page_number=2,
                confidence=0.9,
            ),
        ],
        confidence_score=0.92,
        bounding_boxes={
            "total_amount": {
                "unit": "normalized",
                "page_number": 1,
                "polygon": [[0.6, 0.9], [0.87, 0.9], [0.87, 0.94], [0.6, 0.94]],
            }
        },
    )
    base.update(overrides)
    return ExtractedInvoiceData(**base)


class StubPrimary(BaseInvoiceExtractor):
    def __init__(self, data: ExtractedInvoiceData, error: Exception | None = None) -> None:
        self._data = data
        self._error = error
        self.calls = 0
        self.last_raw_payload: dict = {"documents": [{"azure": "raw"}]}
        self.last_ocr_text = "ACME GMBH\nINVOICE INV-1042\nTOTAL 1488.00 EUR"

    async def extract(self, document_bytes, mime_type, context=None):
        self.calls += 1
        if self._error is not None:
            raise self._error
        return self._data


class StubFallback(BaseInvoiceExtractor):
    def __init__(self, data: ExtractedInvoiceData) -> None:
        self._data = data
        self.calls: list[dict | None] = []

    async def extract(self, document_bytes, mime_type, context=None):
        self.calls.append(context)
        return self._data


class FakeFetcher:
    async def fetch(self, storage_path: str) -> bytes:
        return b"%PDF-1.4 fake-bytes"


async def seed_pipeline(session_factory, seeded, *, filename="inv.pdf", with_run=True):
    """Document + invoice(uploaded) + optionally run(running), mirroring the task setup."""
    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        storage_path=f"{seeded['workspace_a_id']}/2026/11/{uuid.uuid4()}_{filename}",
        original_filename=filename,
        mime_type="application/pdf",
        file_size_bytes=4096,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=seeded["user_a_id"],
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=seeded["workspace_a_id"],
        document_id=document.id,
        status=InvoiceStatus.UPLOADED,
    )
    rows: list[DocumentFile | Invoice | ExtractionRun] = [document, invoice]
    run: ExtractionRun | None = None
    if with_run:
        run = ExtractionRun(
            id=uuid.uuid4(),
            organization_id=seeded["workspace_a_id"],
            invoice_id=invoice.id,
            status=ExtractionRunStatus.RUNNING,
            attempt=1,
            started_at=datetime.now(timezone.utc),
        )
        rows.append(run)
    async with session_factory() as session:
        session.add_all(rows)
        await session.commit()
    return invoice, run


def build_orchestrator(session_factory, primary, fallback) -> ExtractionOrchestrator:
    return ExtractionOrchestrator(
        session_factory,
        document_fetcher=FakeFetcher(),
        primary_extractor=primary,
        fallback_extractor=fallback,
        confidence_threshold=0.75,
    )


# ── Case A: high confidence → no fallback ────────────────────────────────


async def test_case_a_high_confidence_skips_fallback(seeded, session_factory) -> None:
    invoice, run = await seed_pipeline(session_factory, seeded)
    primary = StubPrimary(make_data(confidence_score=0.92))
    fallback = StubFallback(make_data(vendor_name="Should-Not-Be-Used"))
    orchestrator = build_orchestrator(session_factory, primary, fallback)

    await orchestrator.run(invoice.id, run.id)

    assert primary.calls == 1
    assert fallback.calls == []  # OpenAI never invoked

    async with session_factory() as session:
        updated = await session.get(Invoice, invoice.id)
        assert updated is not None
        assert updated.status == InvoiceStatus.NEEDS_REVIEW
        assert updated.vendor_id is not None
        assert updated.invoice_number == "INV-1042"
        assert updated.currency_code == "EUR"
        assert updated.total_amount == Decimal("1488.0000")
        assert updated.extraction_engine.value == "azure_di"
        assert float(updated.confidence_avg) == pytest.approx(0.92)

        vendor = await session.get(Vendor, updated.vendor_id)
        assert vendor is not None
        assert vendor.name == "Acme GmbH"
        assert vendor.name_norm == "acme"  # corporate suffix ("GmbH") stripped
        assert vendor.tax_id == "DE811907980"

        items = (
            (
                await session.execute(
                    select(InvoiceItem)
                    .where(InvoiceItem.invoice_id == invoice.id)
                    .order_by(InvoiceItem.line_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 2
        assert items[0].description == "Consulting"
        assert items[0].line_total == Decimal("1000.0000")
        assert items[0].bbox["polygon"] == [[0.1, 0.2], [0.3, 0.2], [0.3, 0.25], [0.1, 0.25]]
        assert items[0].bbox["page_number"] == 1
        assert items[0].page_number == 1
        assert items[1].bbox is None
        assert all(item.source_engine.value == "azure_di" for item in items)

        audit = await session.get(ExtractionRun, run.id)
        assert audit is not None
        assert audit.status == ExtractionRunStatus.SUCCEEDED
        assert audit.fallback_used is False
        assert audit.primary_provider == "azure_di"
        assert audit.engine.value == "azure_di"
        assert audit.raw_payload == {"documents": [{"azure": "raw"}]}
        assert audit.normalized_output["vendor_name"] == "Acme GmbH"
        assert float(audit.confidence_score) == pytest.approx(0.92)
        assert audit.duration_ms is not None


# ── Case B: low confidence → fallback reconciles ─────────────────────────


async def test_case_b_low_confidence_triggers_fallback(seeded, session_factory) -> None:
    invoice, run = await seed_pipeline(session_factory, seeded)
    weak = make_data(vendor_name=None, total_amount=None, line_items=[], confidence_score=0.60)
    reconciled = make_data(
        vendor_name="Beta LLC",
        vendor_tax_id=None,
        total_amount=Decimal("1488.0000"),
        line_items=[
            ExtractedLineItem(description="Consulting", line_total=Decimal("1000"), confidence=0.9),
            ExtractedLineItem(description="Support", line_total=Decimal("240"), confidence=0.9),
            ExtractedLineItem(description="Hardware", line_total=Decimal("248"), confidence=0.9),
        ],
        confidence_score=0.60,
    )
    primary = StubPrimary(weak)
    fallback = StubFallback(reconciled)
    orchestrator = build_orchestrator(session_factory, primary, fallback)

    await orchestrator.run(invoice.id, run.id)

    assert primary.calls == 1
    assert len(fallback.calls) == 1
    context = fallback.calls[0]
    assert context["ocr_text"] == primary.last_ocr_text
    assert context["azure_extraction"]["vendor_name"] is None
    assert context["azure_extraction"]["confidence_score"] == 0.60

    async with session_factory() as session:
        updated = await session.get(Invoice, invoice.id)
        assert updated is not None
        assert updated.status == InvoiceStatus.NEEDS_REVIEW  # forced, never auto-pass
        assert updated.vendor_id is not None
        assert updated.total_amount == Decimal("1488.0000")

        vendor = await session.get(Vendor, updated.vendor_id)
        assert vendor is not None
        assert vendor.name == "Beta LLC"

        items = (
            (
                await session.execute(
                    select(InvoiceItem)
                    .where(InvoiceItem.invoice_id == invoice.id)
                    .order_by(InvoiceItem.line_number)
                )
            )
            .scalars()
            .all()
        )
        assert len(items) == 3
        assert all(item.source_engine.value == "openai_fallback" for item in items)

        audit = await session.get(ExtractionRun, run.id)
        assert audit is not None
        assert audit.fallback_used is True
        assert audit.engine.value == "openai_fallback"
        assert audit.normalized_output["vendor_name"] == "Beta LLC"


async def test_gate_boundary_exactly_at_threshold_skips_fallback(
    seeded, session_factory
) -> None:
    invoice, run = await seed_pipeline(session_factory, seeded)
    primary = StubPrimary(make_data(confidence_score=0.75))  # 0.75 is not < 0.75
    fallback = StubFallback(make_data())
    orchestrator = build_orchestrator(session_factory, primary, fallback)

    await orchestrator.run(invoice.id, run.id)
    assert fallback.calls == []

    async with session_factory() as session:
        audit = await session.get(ExtractionRun, run.id)
        assert audit is not None
        assert audit.fallback_used is False


async def test_vendor_reused_across_invoices(seeded, session_factory) -> None:
    first, first_run = await seed_pipeline(session_factory, seeded, filename="one.pdf")
    second, second_run = await seed_pipeline(session_factory, seeded, filename="two.pdf")
    orchestrator = build_orchestrator(
        session_factory, StubPrimary(make_data()), StubFallback(make_data())
    )

    await orchestrator.run(first.id, first_run.id)
    await orchestrator.run(second.id, second_run.id)

    async with session_factory() as session:
        vendors = (await session.execute(select(Vendor))).scalars().all()
        assert len(vendors) == 1
        invoices = (
            (await session.execute(select(Invoice).where(Invoice.vendor_id.isnot(None))))
            .scalars()
            .all()
        )
        assert len(invoices) == 2
        assert all(inv.vendor_id == vendors[0].id for inv in invoices)


# ── Case C: provider failure → FAILED ────────────────────────────────────


async def test_case_c_extraction_failure_marks_failed(
    seeded, session_factory, monkeypatch
) -> None:
    import app.workers.tasks as tasks_module

    invoice, _ = await seed_pipeline(session_factory, seeded, filename="failed.pdf", with_run=False)
    primary = StubPrimary(make_data(), error=ExtractionError("azure exploded"))
    orchestrator = build_orchestrator(session_factory, primary, StubFallback(make_data()))

    async def failing_seam(factory, invoice_uuid, run_id):
        await orchestrator.run(invoice_uuid, run_id)

    monkeypatch.setattr(tasks_module, "_run_extraction", failing_seam)
    result = await process_invoice_task({"session_factory": session_factory}, str(invoice.id))

    assert result == "failed"
    async with session_factory() as session:
        failed = await session.get(Invoice, invoice.id)
        assert failed is not None
        assert failed.status == InvoiceStatus.FAILED
        assert failed.extraction_finished_at is not None

        # The task owns run creation — exactly one run row exists and it is FAILED.
        runs = (
            (
                await session.execute(
                    select(ExtractionRun).where(ExtractionRun.invoice_id == invoice.id)
                )
            )
            .scalars()
            .all()
        )
        assert len(runs) == 1
        audit = runs[0]
        assert audit.status == ExtractionRunStatus.FAILED
        assert audit.error_code == "EXTRACTION_FAILED"
        assert audit.error_message == "azure exploded"
        assert audit.normalized_output is None
        assert audit.fallback_used is False


# ── Azure SDK mapping ────────────────────────────────────────────────────


def di_field(**attributes) -> SimpleNamespace:
    """A DocumentField-shaped stub with every attribute the mapper touches."""
    base = dict(
        content=None,
        value_string=None,
        value_date=None,
        value_number=None,
        value_integer=None,
        value_currency=None,
        value_array=None,
        value_object=None,
        confidence=None,
        bounding_regions=[],
    )
    base.update(attributes)
    return SimpleNamespace(**base)


def di_region(page_number: int, polygon: list[float]) -> SimpleNamespace:
    return SimpleNamespace(page_number=page_number, polygon=polygon)


def build_di_result() -> SimpleNamespace:
    return SimpleNamespace(
        documents=[
            SimpleNamespace(
                confidence=0.92,
                fields={
                    "VendorName": di_field(
                        value_string="Acme GmbH",
                        confidence=0.98,
                        bounding_regions=[di_region(1, [5.1, 1.0, 7.0, 1.0, 7.0, 1.3, 5.1, 1.3])],
                    ),
                    "InvoiceId": di_field(value_string="INV-1042"),
                    "InvoiceDate": di_field(value_date=date(2026, 10, 28)),
                    "DueDate": di_field(value_date=date(2026, 11, 27)),
                    "CurrencyCode": di_field(value_string="eur"),
                    "SubTotal": di_field(value_currency=SimpleNamespace(amount=1240.0)),
                    "TotalTaxAmount": di_field(value_currency=SimpleNamespace(amount=248.0)),
                    "InvoiceTotal": di_field(
                        value_currency=SimpleNamespace(amount=1488.0),
                        confidence=0.93,
                        bounding_regions=[
                            di_region(1, [6.8, 9.9, 7.42, 9.9, 7.42, 10.12, 6.8, 10.12])
                        ],
                    ),
                    "Items": di_field(
                        value_array=[
                            SimpleNamespace(
                                value_object={
                                    "Description": di_field(value_string="Consulting"),
                                    "Quantity": di_field(value_number=10.0),
                                    "UnitPrice": di_field(value_currency=SimpleNamespace(amount=100.0)),
                                    "Amount": di_field(value_currency=SimpleNamespace(amount=1000.0)),
                                    "ProductCode": di_field(value_string="CON-10"),
                                },
                                confidence=0.88,
                                bounding_regions=[
                                    di_region(1, [1.0, 3.0, 8.0, 3.0, 8.0, 3.5, 1.0, 3.5])
                                ],
                            )
                        ]
                    ),
                },
            )
        ],
        pages=[SimpleNamespace(page_number=1, width=8.5, height=11.0)],
        content="OCR MARKDOWN CONTENT",
        as_dict=lambda: {"documents": [{"di": "raw"}]},
    )


async def test_azure_mapping_normalizes_polygons_and_types(monkeypatch) -> None:
    extractor = AzureInvoiceExtractor(
        endpoint="https://example.cognitiveservices.azure.com/", api_key="test-key"
    )
    fake_result = build_di_result()
    monkeypatch.setattr(extractor, "_analyze", lambda document_bytes: fake_result)

    data = await extractor.extract(b"%PDF", "application/pdf")

    assert data.vendor_name == "Acme GmbH"
    assert data.invoice_number == "INV-1042"
    assert data.invoice_date == date(2026, 10, 28)
    assert data.due_date == date(2026, 11, 27)
    assert data.currency == "EUR"  # "eur" normalized to ISO 4217
    assert data.total_amount == Decimal("1488.0")
    assert data.subtotal == Decimal("1240.0")
    assert data.confidence_score == pytest.approx(0.92)
    assert extractor.last_ocr_text == "OCR MARKDOWN CONTENT"
    assert extractor.last_raw_payload == {"documents": [{"di": "raw"}]}

    # 5.1/8.5 → 0.6, 1.0/11 → 0.0909 — normalized against page geometry
    vendor_bbox = data.bounding_boxes["VendorName"]
    assert vendor_bbox["polygon"] == [
        [0.6, 0.0909],
        [0.8235, 0.0909],
        [0.8235, 0.1182],
        [0.6, 0.1182],
    ]
    assert data.bounding_boxes["InvoiceTotal"]["polygon"][0] == [0.8, 0.9]

    assert len(data.line_items) == 1
    item = data.line_items[0]
    assert item.description == "Consulting"
    assert item.quantity == Decimal("10.0")
    assert item.unit_price == Decimal("100.0")
    assert item.line_total == Decimal("1000.0")
    assert item.sku == "CON-10"
    assert item.page_number == 1
    assert item.confidence == pytest.approx(0.88)
    assert item.bounding_box == [
        [0.1176, 0.2727],
        [0.9412, 0.2727],
        [0.9412, 0.3182],
        [0.1176, 0.3182],
    ]


# ── OpenAI structured-output conversion ──────────────────────────────────


class StubCompletions:
    def __init__(self, parsed) -> None:
        self._parsed = parsed
        self.calls: list[dict] = []

    async def parse(self, **kwargs):
        self.calls.append(kwargs)
        message = SimpleNamespace(parsed=self._parsed, content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class StubOpenAIClient:
    def __init__(self, parsed) -> None:
        self.chat = SimpleNamespace(completions=StubCompletions(parsed))


async def test_openai_fallback_conversion_and_context() -> None:
    parsed = OpenAINormalizedInvoice(
        vendor_name="Beta LLC",
        invoice_number="B-9",
        currency="usd",
        total_amount=99.5,
        line_items=[
            OpenAILineItem(description="Widget", quantity=1, unit_price=99.5, line_total=99.5)
        ],
    )
    client = StubOpenAIClient(parsed)
    extractor = OpenAIFallbackExtractor(client=client, model="gpt-test")
    context = {
        "ocr_text": "BETA LLC TOTAL 99.50 USD",
        "azure_extraction": {"confidence_score": 0.6},
    }

    data = await extractor.extract(b"jpeg-bytes", "image/jpeg", context=context)

    assert data.vendor_name == "Beta LLC"
    assert data.currency == "USD"  # normalized to ISO 4217
    assert data.total_amount == Decimal("99.5")
    assert data.invoice_number == "B-9"
    assert data.confidence_score == pytest.approx(0.6)  # carried, not LLM-claimed
    assert data.line_items[0].line_total == Decimal("99.5")
    assert data.bounding_boxes == {}

    call = client.chat.completions.calls[0]
    assert call["model"] == "gpt-test"
    assert call["response_model"] is OpenAINormalizedInvoice
    user_content = call["messages"][1]["content"]
    assert user_content[0]["type"] == "text"
    assert "BETA LLC TOTAL 99.50 USD" in user_content[0]["text"]
    # Photos are attached as base64 data URLs
    assert user_content[1]["image_url"]["url"].startswith("data:image/jpeg;base64,")


async def test_extractor_configuration_guards() -> None:
    with pytest.raises(ExtractorConfigurationError):
        OpenAIFallbackExtractor(api_key=None, client=None)
    with pytest.raises(ExtractorConfigurationError):
        AzureInvoiceExtractor(endpoint="", api_key="")
