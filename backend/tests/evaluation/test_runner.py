"""Golden-dataset benchmark runner (pytest + CLI).

    pytest backend/tests/evaluation/                      # gated benchmark
    cd backend && python -m tests.evaluation.test_runner  # scorecard only

What this measures — and what it does not
-----------------------------------------
Each fixture pairs a Document-Intelligence-shaped provider payload
(`dataset/samples/`) with independent human ground truth (`dataset/truth/`).
The payload is fed through the REAL pipeline: `AzureInvoiceExtractor`'s
response mapping (only the SDK network call is stubbed), the orchestrator's
confidence gate, the fallback merge, vendor matching, persistence, and the
deterministic validation engine.

So a regression in the mapping, the gate, the merge, or the validators shows
up here as a score drop or a gate failure.

It does NOT measure how well Azure/OpenAI read real documents: the provider
payloads are synthetic and hand-authored. The PRD §9.1 acceptance criterion
(≥ 95% on a 200-document golden set of real invoices) needs real provider
calls against real files and is not satisfied by this suite.
"""

import asyncio
import json
import sys
import time
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.models import (
    DocumentFile,
    ExtractionRun,
    ExtractionRunStatus,
    Invoice,
    InvoiceStatus,
    Organization,
)
from app.modules.extraction.azure_extractor import AzureInvoiceExtractor
from app.modules.extraction.base import BaseInvoiceExtractor
from app.modules.extraction.orchestrator import ExtractionOrchestrator
from app.modules.extraction.schemas import ExtractedInvoiceData, ExtractedLineItem
from tests.conftest import _sqlite_metadata
from tests.evaluation.metrics import (
    SCORED_FIELDS,
    BenchmarkAggregate,
    DocumentScore,
    aggregate_benchmark,
    date_match_score,
    exact_match_score,
    format_report,
    fuzzy_match_score,
    line_items_f1_score,
    monetary_accuracy,
)

DATASET_DIR = Path(__file__).parent / "dataset"
SAMPLES_DIR = DATASET_DIR / "samples"
TRUTH_DIR = DATASET_DIR / "truth"

TOTAL_AMOUNT_ACCURACY_GATE = 95.0
REPORT_SUBTITLE = (
    "synthetic fixtures, provider calls stubbed — pipeline regression check, "
    "NOT a measure of real-document extraction accuracy"
)


# ── Fixture loading ──────────────────────────────────────────────────────


def load_fixtures() -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """(sample, truth) pairs, matched by filename, sorted by id."""
    pairs: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for sample_path in sorted(SAMPLES_DIR.glob("*.json")):
        truth_path = TRUTH_DIR / sample_path.name
        if not truth_path.exists():
            raise FileNotFoundError(f"no ground truth for sample {sample_path.name}")
        sample = json.loads(sample_path.read_text(encoding="utf-8"))
        truth = json.loads(truth_path.read_text(encoding="utf-8"))
        if sample["id"] != truth["id"]:
            raise ValueError(f"id mismatch between sample and truth: {sample_path.name}")
        pairs.append((sample, truth))
    return pairs


def _to_namespace(value: Any) -> Any:
    """JSON → attribute-access tree shaped like the Azure SDK result objects.

    `fields` and `value_object` stay plain dicts (the mapper calls `.get()` on
    them); `value_date` ISO strings become `date` objects like the SDK returns.
    """
    if isinstance(value, list):
        return [_to_namespace(item) for item in value]
    if not isinstance(value, dict):
        return value

    converted: dict[str, Any] = {}
    for key, item in value.items():
        if key in ("fields", "value_object") and isinstance(item, dict):
            converted[key] = {name: _to_namespace(field) for name, field in item.items()}
        elif key == "value_date" and isinstance(item, str):
            converted[key] = date.fromisoformat(item)
        else:
            converted[key] = _to_namespace(item)
    return SimpleNamespace(**converted)


# ── Provider stubs ───────────────────────────────────────────────────────


def build_primary_extractor(sample: dict[str, Any]) -> AzureInvoiceExtractor:
    """Real AzureInvoiceExtractor with only the SDK call replaced."""
    extractor = AzureInvoiceExtractor(
        endpoint="https://benchmark.invalid/", api_key="benchmark-key"
    )
    if sample.get("simulate_azure_failure"):

        def failing_analyze(document_bytes: bytes) -> Any:
            raise RuntimeError("simulated provider outage")

        extractor._analyze = failing_analyze  # type: ignore[method-assign]
        return extractor

    result = _to_namespace(sample["di_response"])
    raw_payload = sample.get("azure_raw") or {}
    result.as_dict = lambda: raw_payload

    extractor._analyze = lambda document_bytes: result  # type: ignore[method-assign]
    return extractor


class StubFallbackExtractor(BaseInvoiceExtractor):
    """Stands in for OpenAI: returns the fixture's `openai` block.

    A fixture with `openai: null` must never reach the fallback — if it does,
    that is a gate regression and is surfaced as an exception.
    """

    def __init__(self, payload: dict[str, Any] | None) -> None:
        self._payload = payload
        self.invocations = 0

    async def extract(self, document_bytes, mime_type, context=None):
        self.invocations += 1
        if self._payload is None:
            raise AssertionError("fallback invoked for a fixture that defines no fallback output")
        return ExtractedInvoiceData.model_validate(self._payload)


class FakeFetcher:
    async def fetch(self, storage_path: str) -> bytes:
        return b"synthetic-benchmark-document"


# ── Pipeline execution + scoring ─────────────────────────────────────────


async def run_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    organization_id: uuid.UUID,
    sample: dict[str, Any],
    truth: dict[str, Any],
) -> DocumentScore:
    fixture_id = sample["id"]
    expected = truth.get("expected", {})
    score = DocumentScore(
        fixture_id=fixture_id,
        description=sample.get("description", ""),
        fallback_expected=bool(expected.get("fallback_used", False)),
        expected_validation_codes=list(expected.get("validation_codes", [])),
        field_scores={name: 0.0 for name in SCORED_FIELDS},
    )

    document = DocumentFile(
        id=uuid.uuid4(),
        organization_id=organization_id,
        storage_path=f"{organization_id}/benchmark/{fixture_id}",
        original_filename=f"{fixture_id}.bin",
        mime_type=sample.get("mime_type", "application/pdf"),
        file_size_bytes=1024,
        content_hash=uuid.uuid4().hex,
        uploaded_by_id=uuid.uuid4(),
    )
    invoice = Invoice(
        id=uuid.uuid4(),
        organization_id=organization_id,
        document_id=document.id,
        status=InvoiceStatus.UPLOADED,
    )
    run = ExtractionRun(
        id=uuid.uuid4(),
        organization_id=organization_id,
        invoice_id=invoice.id,
        status=ExtractionRunStatus.RUNNING,
        attempt=1,
        started_at=datetime.now(timezone.utc),
    )
    async with session_factory() as session:
        session.add_all([document, invoice, run])
        await session.commit()

    fallback = StubFallbackExtractor(sample.get("openai"))
    started = time.perf_counter()
    try:
        orchestrator = ExtractionOrchestrator(
            session_factory,
            document_fetcher=FakeFetcher(),
            primary_extractor=build_primary_extractor(sample),
            fallback_extractor=fallback,
            confidence_threshold=0.75,
        )
        await orchestrator.run(invoice.id, run.id)
    except Exception as exc:  # noqa: BLE001 — the benchmark counts every failure
        score.exceptions.append(f"{type(exc).__name__}: {exc}")
    score.latency_ms = int((time.perf_counter() - started) * 1000)

    async with session_factory() as session:
        stored_invoice = await session.get(Invoice, invoice.id)
        stored_run = await session.get(ExtractionRun, run.id)

    score.fallback_used = bool(stored_run.fallback_used) if stored_run else False
    if stored_invoice is not None and stored_invoice.validation_errors:
        score.validation_codes = sorted(
            issue["code"] for issue in stored_invoice.validation_errors
        )

    normalized = stored_run.normalized_output if stored_run else None
    if not normalized:
        return score  # pipeline produced nothing: every field stays 0.0

    final = ExtractedInvoiceData.model_validate(normalized)
    truth_data = ExtractedInvoiceData.model_validate(truth["truth"])
    score.field_scores = {
        "vendor_name": fuzzy_match_score(final.vendor_name, truth_data.vendor_name),
        "invoice_number": exact_match_score(final.invoice_number, truth_data.invoice_number),
        "currency": exact_match_score(final.currency, truth_data.currency),
        "invoice_date": date_match_score(final.invoice_date, truth_data.invoice_date),
        "due_date": date_match_score(final.due_date, truth_data.due_date),
        "subtotal": monetary_accuracy(final.subtotal, truth_data.subtotal),
        "tax_amount": monetary_accuracy(final.tax_amount, truth_data.tax_amount),
        "total_amount": monetary_accuracy(final.total_amount, truth_data.total_amount),
    }
    score.line_item_f1 = line_items_f1_score(final.line_items, truth_data.line_items)
    return score


async def run_benchmark(
    session_factory: async_sessionmaker[AsyncSession],
) -> tuple[BenchmarkAggregate, list[DocumentScore]]:
    organization = Organization(
        id=uuid.uuid4(), name="Benchmark Org", slug=f"benchmark-{uuid.uuid4().hex[:8]}"
    )
    async with session_factory() as session:
        session.add(organization)
        await session.commit()

    scores = [
        await run_fixture(session_factory, organization.id, sample, truth)
        for sample, truth in load_fixtures()
    ]
    return aggregate_benchmark(scores), scores


def gate_failures(aggregate: BenchmarkAggregate, scores: list[DocumentScore]) -> list[str]:
    """Human-readable reasons the benchmark should fail (empty = pass)."""
    failures: list[str] = []
    total_accuracy = aggregate.accuracy_percent["total_amount"]
    if total_accuracy < TOTAL_AMOUNT_ACCURACY_GATE:
        failures.append(
            f"Total Amount Accuracy {total_accuracy:.1f}% < {TOTAL_AMOUNT_ACCURACY_GATE:.0f}%"
        )
    for score in scores:
        for message in score.exceptions:
            failures.append(f"{score.fixture_id}: unhandled exception — {message}")
        if score.unexpected_fallback:
            failures.append(
                f"{score.fixture_id}: fallback_used={score.fallback_used}, "
                f"expected {score.fallback_expected}"
            )
        if score.validation_mismatch:
            failures.append(
                f"{score.fixture_id}: validation codes {score.validation_codes}, "
                f"expected {sorted(score.expected_validation_codes)}"
            )
    return failures


# ── Pytest: the gated benchmark ──────────────────────────────────────────


async def test_benchmark_scorecard(session_factory) -> None:
    aggregate, scores = await run_benchmark(session_factory)
    print("\n" + format_report(aggregate, scores, subtitle=REPORT_SUBTITLE))

    assert aggregate.document_count >= 5
    assert aggregate.unhandled_exception_count == 0, [s.exceptions for s in scores]
    assert aggregate.accuracy_percent["total_amount"] >= TOTAL_AMOUNT_ACCURACY_GATE
    assert gate_failures(aggregate, scores) == []


async def test_benchmark_detects_a_pipeline_failure(session_factory) -> None:
    """A provider outage must surface as a counted exception and zeroed
    scores — proving the gates can actually fail."""
    organization = Organization(id=uuid.uuid4(), name="Outage Org", slug="outage-org")
    async with session_factory() as session:
        session.add(organization)
        await session.commit()

    sample, truth = load_fixtures()[0]
    broken_sample = {**sample, "simulate_azure_failure": True}
    score = await run_fixture(session_factory, organization.id, broken_sample, truth)

    assert len(score.exceptions) == 1
    assert "simulated provider outage" in score.exceptions[0]
    assert score.field_scores["total_amount"] == 0.0
    aggregate = aggregate_benchmark([score])
    assert aggregate.unhandled_exception_count == 1
    assert gate_failures(aggregate, [score])  # non-empty → benchmark would fail


async def test_benchmark_detects_a_wrong_total(session_factory) -> None:
    """If the provider reports a wrong total, Total Amount Accuracy must drop
    — the score is computed against independent truth, not echoed back."""
    organization = Organization(id=uuid.uuid4(), name="Drift Org", slug="drift-org")
    async with session_factory() as session:
        session.add(organization)
        await session.commit()

    sample, truth = load_fixtures()[0]
    drifted = json.loads(json.dumps(sample))
    drifted["di_response"]["documents"][0]["fields"]["InvoiceTotal"]["value_currency"]["amount"] = 9999.0
    score = await run_fixture(session_factory, organization.id, drifted, truth)

    assert score.exceptions == []
    assert score.field_scores["total_amount"] == 0.0
    assert score.field_scores["vendor_name"] == 1.0  # other fields unaffected
    aggregate = aggregate_benchmark([score])
    assert aggregate.accuracy_percent["total_amount"] == 0.0
    assert any("Total Amount Accuracy" in f for f in gate_failures(aggregate, [score]))


def test_fixture_dataset_integrity() -> None:
    pairs = load_fixtures()
    assert len(pairs) >= 5
    ids = [sample["id"] for sample, _ in pairs]
    assert len(set(ids)) == len(ids)
    for sample, truth in pairs:
        assert "di_response" in sample
        ExtractedInvoiceData.model_validate(truth["truth"])  # truth is schema-valid
        assert set(truth["expected"]) == {"fallback_used", "validation_codes"}


# ── Pytest: scorer unit tests (hits AND misses) ──────────────────────────


def test_exact_match_score() -> None:
    assert exact_match_score("INV-1", "inv-1") == 1.0
    assert exact_match_score("INV-1", "INV-2") == 0.0
    assert exact_match_score(None, None) == 1.0
    assert exact_match_score(None, "INV-1") == 0.0  # missed
    assert exact_match_score("INV-1", None) == 0.0  # fabricated


def test_fuzzy_match_score() -> None:
    assert fuzzy_match_score("Acme GmbH", "Acme Ltd") == 1.0  # suffix-insensitive
    assert fuzzy_match_score("Berliner Digtal", "Berliner Digital") == 1.0  # OCR typo
    assert fuzzy_match_score("Acme GmbH", "Zenith Inc") == 0.0
    assert fuzzy_match_score(None, "Acme") == 0.0
    assert fuzzy_match_score(None, None) == 1.0


def test_date_match_score() -> None:
    assert date_match_score(date(2026, 11, 1), date(2026, 11, 1)) == 1.0
    assert date_match_score(date(2026, 11, 1), date(2026, 11, 2)) == 0.0
    assert date_match_score(None, date(2026, 11, 1)) == 0.0
    assert date_match_score(None, None) == 1.0


def test_monetary_accuracy_tolerance() -> None:
    assert monetary_accuracy(Decimal("100.00"), Decimal("100.02")) == 1.0
    assert monetary_accuracy(Decimal("100.00"), Decimal("100.03")) == 0.0
    assert monetary_accuracy(None, Decimal("1.00")) == 0.0
    assert monetary_accuracy(None, None) == 1.0


def test_line_items_f1_score() -> None:
    def item(quantity: str, unit_price: str) -> ExtractedLineItem:
        return ExtractedLineItem(quantity=Decimal(quantity), unit_price=Decimal(unit_price))

    truth_items = [item("10", "100.00"), item("2", "120.00")]
    assert line_items_f1_score(truth_items, truth_items) == 1.0
    assert line_items_f1_score([], []) == 1.0
    assert line_items_f1_score([], truth_items) == 0.0
    # one of two predicted items is wrong → precision 0.5, recall 0.5 → F1 0.5
    assert line_items_f1_score([item("10", "100.00"), item("9", "9.99")], truth_items) == 0.5
    # an extra hallucinated line lowers precision only → F1 = 0.8
    assert line_items_f1_score(
        truth_items + [item("1", "1.00")], truth_items
    ) == pytest.approx(0.8)


# ── CLI ──────────────────────────────────────────────────────────────────


async def _cli() -> int:
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as connection:
            await connection.run_sync(_sqlite_metadata().create_all)
        session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
        aggregate, scores = await run_benchmark(session_factory)
    finally:
        await engine.dispose()

    print(format_report(aggregate, scores, subtitle=REPORT_SUBTITLE))
    failures = gate_failures(aggregate, scores)
    if failures:
        print("BENCHMARK FAILED:")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("BENCHMARK PASSED (pipeline gates only — see header note).")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(_cli()))
