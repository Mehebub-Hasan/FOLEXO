"""Field-level scoring + benchmark aggregation for the extraction evaluation.

Every scorer returns a float in [0.0, 1.0]:
- both sides None   → 1.0 (they agree the field is absent)
- one side None     → 0.0 (missing where truth exists = miss;
                       value where truth is absent = fabrication)
- otherwise         → rule-specific comparison.

Vendor-name comparison goes through the production normalizer
(`normalize_vendor_name`) so legal-form suffixes (GmbH/Ltd/Inc) do not count
as errors — the same rule the matcher applies when deduplicating vendors.
"""

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from typing import Any

from app.modules.vendors.matcher import normalize_vendor_name

DEFAULT_FUZZY_THRESHOLD = 0.85
DEFAULT_MONEY_TOLERANCE = Decimal("0.02")


def exact_match_score(pred: str | None, true: str | None) -> float:
    """Case-insensitive exact match (invoice_number, currency)."""
    if pred is None and true is None:
        return 1.0
    if pred is None or true is None:
        return 0.0
    return 1.0 if pred.strip().casefold() == true.strip().casefold() else 0.0


def _similarity(pred: str, true: str) -> float:
    return SequenceMatcher(None, pred, true).ratio()


def fuzzy_match_score(
    pred: str | None, true: str | None, threshold: float = DEFAULT_FUZZY_THRESHOLD
) -> float:
    """Vendor-name match: production normalization + SequenceMatcher ratio,
    binarized at `threshold` so accuracy percentages stay honest."""
    if pred is None and true is None:
        return 1.0
    if pred is None or true is None:
        return 0.0
    pred_norm = normalize_vendor_name(pred)
    true_norm = normalize_vendor_name(true)
    if pred_norm == true_norm:
        return 1.0
    similarity = _similarity(pred_norm, true_norm)
    return 1.0 if similarity >= threshold else 0.0


def date_match_score(pred: date | None, true: date | None) -> float:
    if pred is None and true is None:
        return 1.0
    if pred is None or true is None:
        return 0.0
    return 1.0 if pred == true else 0.0


def monetary_accuracy(
    pred: Decimal | None,
    true: Decimal | None,
    tolerance: Decimal = DEFAULT_MONEY_TOLERANCE,
) -> float:
    if pred is None and true is None:
        return 1.0
    if pred is None or true is None:
        return 0.0
    return 1.0 if abs(pred - true) <= tolerance else 0.0


def _pair(item: Any) -> tuple[Decimal, Decimal] | None:
    """Matching key: (quantity, unit_price); None when either is missing."""
    quantity, unit_price = getattr(item, "quantity", None), getattr(item, "unit_price", None)
    if quantity is None or unit_price is None:
        return None
    return (quantity, unit_price)


def _pairs_equal(a: tuple[Decimal, Decimal], b: tuple[Decimal, Decimal], tolerance: Decimal) -> bool:
    return (
        abs(a[0] - b[0]) <= tolerance and abs(a[1] - b[1]) <= tolerance
    )


def line_items_f1_score(
    pred_items: list[Any],
    true_items: list[Any],
    tolerance: Decimal = DEFAULT_MONEY_TOLERANCE,
) -> float:
    """Line-item F1, greedy-matching predicted items to truth items by
    (quantity, unit_price) within tolerance."""
    if not pred_items and not true_items:
        return 1.0

    remaining = list(true_items)
    matches = 0
    for pred in pred_items:
        pred_key = _pair(pred)
        if pred_key is None:
            continue
        for index, true_item in enumerate(remaining):
            true_key = _pair(true_item)
            if true_key is not None and _pairs_equal(pred_key, true_key, tolerance):
                matches += 1
                remaining.pop(index)
                break

    precision = matches / len(pred_items) if pred_items else 0.0
    recall = matches / len(true_items) if true_items else 0.0
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ── Benchmark aggregation ────────────────────────────────────────────────

SCORED_FIELDS = (
    "vendor_name",
    "invoice_number",
    "currency",
    "invoice_date",
    "due_date",
    "subtotal",
    "tax_amount",
    "total_amount",
)


@dataclass
class DocumentScore:
    fixture_id: str
    description: str
    field_scores: dict[str, float] = field(default_factory=dict)
    line_item_f1: float = 0.0
    fallback_used: bool = False
    fallback_expected: bool = False
    latency_ms: int = 0
    validation_codes: list[str] = field(default_factory=list)
    expected_validation_codes: list[str] = field(default_factory=list)
    exceptions: list[str] = field(default_factory=list)

    @property
    def unexpected_fallback(self) -> bool:
        return self.fallback_used != self.fallback_expected

    @property
    def validation_mismatch(self) -> bool:
        return sorted(self.validation_codes) != sorted(self.expected_validation_codes)


@dataclass
class BenchmarkAggregate:
    document_count: int
    accuracy_percent: dict[str, float]
    line_item_f1_percent: float
    fallback_invocation_rate_percent: float
    average_latency_ms: float
    unhandled_exception_count: int
    unexpected_fallback_count: int
    validation_mismatch_count: int


def aggregate_benchmark(scores: list[DocumentScore]) -> BenchmarkAggregate:
    document_count = len(scores)
    if document_count == 0:
        raise ValueError("benchmark needs at least one scored document")

    accuracy_percent = {
        name: round(
            100.0 * sum(score.field_scores.get(name, 0.0) for score in scores) / document_count,
            1,
        )
        for name in SCORED_FIELDS
    }
    line_item_f1_percent = round(
        100.0 * sum(score.line_item_f1 for score in scores) / document_count, 1
    )

    return BenchmarkAggregate(
        document_count=document_count,
        accuracy_percent=accuracy_percent,
        line_item_f1_percent=line_item_f1_percent,
        fallback_invocation_rate_percent=round(
            100.0 * sum(1 for score in scores if score.fallback_used) / document_count, 1
        ),
        average_latency_ms=round(
            sum(score.latency_ms for score in scores) / document_count, 1
        ),
        unhandled_exception_count=sum(len(score.exceptions) for score in scores),
        unexpected_fallback_count=sum(1 for score in scores if score.unexpected_fallback),
        validation_mismatch_count=sum(1 for score in scores if score.validation_mismatch),
    )


def format_report(
    aggregate: BenchmarkAggregate,
    scores: list[DocumentScore],
    subtitle: str | None = None,
) -> str:
    """ASCII scorecard for console output."""
    lines = [
        "=" * 72,
        f"FOLEXO EXTRACTION BENCHMARK — {aggregate.document_count} document(s)",
    ]
    if subtitle:
        lines.append(subtitle)
    lines += [
        "=" * 72,
        f"{'fixture':<28} {'F1':>6} {'fallback':>9} {'ms':>6}  issues",
        "-" * 72,
    ]
    for score in scores:
        lines.append(
            f"{score.fixture_id:<28.28} {score.line_item_f1:>6.2f} "
            f"{'yes' if score.fallback_used else 'no':>9} "
            f"{score.latency_ms:>6d}  "
            f"{','.join(score.validation_codes) or '-'}"
        )
    lines.append("-" * 72)
    lines.append("ACCURACY")
    labels = {
        "vendor_name": "Vendor Name Accuracy",
        "invoice_number": "Invoice Number Accuracy",
        "total_amount": "Total Amount Accuracy",
        "invoice_date": "Date Accuracy (invoice)",
        "due_date": "Date Accuracy (due)",
        "currency": "Currency Accuracy",
        "subtotal": "Subtotal Accuracy",
        "tax_amount": "Tax Amount Accuracy",
    }
    for name in SCORED_FIELDS:
        lines.append(f"  {labels.get(name, name):<28}: {aggregate.accuracy_percent[name]:>6.1f}%")
    lines.append(f"  {'Line Item Extraction F1':<28}: {aggregate.line_item_f1_percent:>6.1f}%")
    lines.append("PIPELINE")
    lines.append(
        f"  {'Fallback Invocation Rate':<28}: {aggregate.fallback_invocation_rate_percent:>6.1f}%"
    )
    lines.append(
        f"  {'Average Latency / Document':<28}: {aggregate.average_latency_ms:>6.1f} ms"
    )
    lines.append(
        f"  {'Unhandled Exceptions':<28}: {aggregate.unhandled_exception_count:>6d}"
    )
    lines.append(
        f"  {'Unexpected Fallback Runs':<28}: {aggregate.unexpected_fallback_count:>6d}"
    )
    lines.append(
        f"  {'Validation Mismatches':<28}: {aggregate.validation_mismatch_count:>6d}"
    )
    lines.append("=" * 72)
    return "\n".join(lines)
