"""Lazo de prueba continuo del parser de intenciones de WhatsApp (Horizonte 1).

Carga el corpus sintético ``simulation/datasets/whatsapp_intents_benchmark.jsonl`` — 625 muestras
calibradas: selecciones numéricas, dialectos de México/Colombia/Argentina/Chile/Caribe, emojis,
erratas de tecleo, pedidos regulatorios de baja (STOP/BAJA/UNSUBSCRIBE) e inyecciones adversarias —
y evalúa el parser REAL del producto
(``apps/server/src/aethercal/server/services/whatsapp_interactive.py``) contra él, generando la
matriz de confusión y las métricas por acción.

Certifica dos propiedades, y la primera es la que manda:

1. ==La tasa de falsos positivos de ``OPT_OUT`` es EXACTAMENTE 0.== Un falso positivo de supresión
   es un mensaje etiquetado como "confirma" o "cancela" que el parser convierte en una baja: la
   tabla ``phone_suppressions`` es a nivel de instancia, sobrevive al borrado de datos del huésped
   y no tiene camino de vuelta. Es la única clasificación del parser que no es simétrica, y por eso
   el orden de sus reglas es una propiedad de seguridad, no de estilo.
2. La exactitud global se mantiene en o por encima de :data:`MIN_ACCURACY`.

Uso: ``uv run python simulation/whatsapp_intent_loop.py`` (o con ``--dataset`` explícito). Sale 0
solo si certifica; cualquier corrida no certificada sale 1, para que el lazo pueda colgarse de un
job de CI sin que nadie lea la salida.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, TypedDict

from aethercal.server.services.whatsapp_interactive import parse_reply_action

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
_logger = logging.getLogger("whatsapp_intent_loop")

ACTION_LABELS: Final[tuple[str, ...]] = (
    "CONFIRM_ATTENDANCE",
    "CANCEL",
    "OPT_OUT",
    "UNKNOWN",
)
"""Las cuatro acciones canónicas, en el orden en que se imprime la matriz."""

OPT_OUT = "OPT_OUT"

OPT_OUT_FALSE_POSITIVE_BUDGET: Final[int] = 0
"""Presupuesto DURO de falsos positivos de ``OPT_OUT``: cero, y sin excepciones."""

MIN_ACCURACY: Final[float] = 0.98
"""Piso de exactitud global. Medido HOY en 1.0 (625/625): el piso deja margen a un muestreo nuevo
sin dejar de ser una compuerta."""

MAX_PRINTED_MISCLASSIFICATIONS: Final[int] = 25


class IntentSample(TypedDict):
    """Un registro del corpus, con el mismo esquema en que vive en el JSONL."""

    text: str
    expected_action: str
    category: str
    dialect_or_case: str


@dataclass(frozen=True, slots=True)
class ClassMetrics:
    """Precisión, exhaustividad y F1 de una acción sobre el corpus."""

    action: str
    precision: float
    recall: float
    f1: float
    support: int


@dataclass(frozen=True, slots=True)
class Misclassification:
    """Una muestra que el parser clasificó mal, con lo que predijo."""

    sample: IntentSample
    predicted: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    """El resultado completo de una corrida del lazo contra un corpus."""

    dataset: Path
    total: int
    confusion: Mapping[str, Mapping[str, int]]
    metrics: Mapping[str, ClassMetrics]
    misclassified: tuple[Misclassification, ...]
    opt_out_false_positives: tuple[IntentSample, ...]
    opt_out_false_negatives: tuple[IntentSample, ...]

    @property
    def accuracy(self) -> float:
        """Proporción de muestras cuya acción predicha coincide con la esperada."""
        return (self.total - len(self.misclassified)) / self.total if self.total else 0.0

    @property
    def opt_out_false_positive_rate(self) -> float:
        """Proporción del corpus que NO era una baja y el parser suprimió."""
        return len(self.opt_out_false_positives) / self.total if self.total else 0.0

    @property
    def certified(self) -> bool:
        """¿La corrida pasa las dos compuertas del lazo?"""
        return not certification_failures(self)


def default_dataset_path() -> Path:
    """El corpus canónico, resuelto desde la raíz del repo (no desde el directorio de trabajo)."""
    return (
        Path(__file__).resolve().parents[1]
        / "simulation"
        / "datasets"
        / ("whatsapp_intents_benchmark.jsonl")
    )


def load_benchmark(path: Path) -> list[IntentSample]:
    """Lee el JSONL validando el esquema: un registro roto debe romper el lazo, no colarse.

    Un corpus que pierde una línea en silencio es exactamente el no-op que este lazo existe para
    detectar: la exactitud subiría sola y nadie lo sabría.
    """
    samples: list[IntentSample] = []
    with path.open("r", encoding="utf-8") as handle:
        for number, line in enumerate(handle, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            try:
                record = json.loads(stripped)
            except json.JSONDecodeError as error:
                raise ValueError(f"{path}:{number}: JSON inválido: {error}") from error
            if not isinstance(record, dict):
                raise ValueError(f"{path}:{number}: se esperaba un objeto JSON")
            action = record.get("expected_action")
            if action not in ACTION_LABELS:
                raise ValueError(
                    f"{path}:{number}: expected_action desconocido: {action!r} "
                    f"(esperado uno de {ACTION_LABELS})"
                )
            for field in ("text", "category", "dialect_or_case"):
                if not isinstance(record.get(field), str):
                    raise ValueError(f"{path}:{number}: {field!r} debe ser texto")
            samples.append(
                IntentSample(
                    text=record["text"],
                    expected_action=action,
                    category=record["category"],
                    dialect_or_case=record["dialect_or_case"],
                )
            )
    if not samples:
        raise ValueError(f"{path}: el corpus está vacío")
    return samples


def evaluate(samples: Sequence[IntentSample], *, dataset: Path) -> Evaluation:
    """Corre el parser real contra el corpus y arma la matriz de confusión y las métricas."""
    confusion: Counter[tuple[str, str]] = Counter()
    misclassified: list[Misclassification] = []
    false_positives: list[IntentSample] = []
    false_negatives: list[IntentSample] = []

    for sample in samples:
        predicted = parse_reply_action(sample["text"]).name
        confusion[(sample["expected_action"], predicted)] += 1
        if predicted != sample["expected_action"]:
            misclassified.append(Misclassification(sample=sample, predicted=predicted))
            if predicted == OPT_OUT:
                false_positives.append(sample)
            if sample["expected_action"] == OPT_OUT:
                false_negatives.append(sample)

    matrix = {
        expected: {predicted: confusion[(expected, predicted)] for predicted in ACTION_LABELS}
        for expected in ACTION_LABELS
    }
    return Evaluation(
        dataset=dataset,
        total=len(samples),
        confusion=matrix,
        metrics=_class_metrics(matrix),
        misclassified=tuple(misclassified),
        opt_out_false_positives=tuple(false_positives),
        opt_out_false_negatives=tuple(false_negatives),
    )


def _class_metrics(matrix: Mapping[str, Mapping[str, int]]) -> Mapping[str, ClassMetrics]:
    """Precisión/recall/F1 por acción, con el cero explícito (no ``0/0``)."""
    metrics: dict[str, ClassMetrics] = {}
    for action in ACTION_LABELS:
        true_positive = matrix[action][action]
        support = sum(matrix[action].values())
        predicted_total = sum(matrix[expected][action] for expected in ACTION_LABELS)
        precision = true_positive / predicted_total if predicted_total else 0.0
        recall = true_positive / support if support else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        metrics[action] = ClassMetrics(
            action=action,
            precision=precision,
            recall=recall,
            f1=f1,
            support=support,
        )
    return metrics


def certification_failures(evaluation: Evaluation) -> tuple[str, ...]:
    """Las razones por las que la corrida NO certifica, en orden de gravedad."""
    failures: list[str] = []
    false_positives = len(evaluation.opt_out_false_positives)
    if false_positives > OPT_OUT_FALSE_POSITIVE_BUDGET:
        failures.append(
            f"falsos positivos de OPT_OUT: {false_positives} "
            f"(presupuesto {OPT_OUT_FALSE_POSITIVE_BUDGET}): "
            + ", ".join(repr(sample["text"]) for sample in evaluation.opt_out_false_positives[:5])
        )
    if evaluation.accuracy < MIN_ACCURACY:
        failures.append(
            f"exactitud {evaluation.accuracy:.4f} por debajo del piso {MIN_ACCURACY:.2f}"
        )
    return tuple(failures)


def format_report(evaluation: Evaluation) -> str:
    """El reporte legible: matriz, métricas por acción y veredicto de la compuerta."""
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("LAZO DE INTENCIONES WHATSAPP — parser real contra corpus sintético")
    lines.append("=" * 78)
    lines.append(f"Corpus : {evaluation.dataset}")
    lines.append(f"Muestras: {evaluation.total}")
    lines.append("-" * 78)
    lines.append("MATRIZ DE CONFUSIÓN (filas = esperado, columnas = predicho)")
    header = "esperado \\ predicho".ljust(24) + "".join(label.rjust(19) for label in ACTION_LABELS)
    lines.append(header)
    for expected in ACTION_LABELS:
        row = expected.ljust(24) + "".join(
            str(evaluation.confusion[expected][predicted]).rjust(19) for predicted in ACTION_LABELS
        )
        lines.append(row)
    lines.append("-" * 78)
    lines.append("MÉTRICAS POR ACCIÓN")
    lines.append(
        "acción".ljust(24)
        + "precisión".rjust(12)
        + "recall".rjust(12)
        + "f1".rjust(10)
        + "soporte".rjust(10)
    )
    for action in ACTION_LABELS:
        metric = evaluation.metrics[action]
        lines.append(
            action.ljust(24)
            + f"{metric.precision:.4f}".rjust(12)
            + f"{metric.recall:.4f}".rjust(12)
            + f"{metric.f1:.4f}".rjust(10)
            + str(metric.support).rjust(10)
        )
    lines.append("-" * 78)
    false_positives = len(evaluation.opt_out_false_positives)
    false_negatives = len(evaluation.opt_out_false_negatives)
    lines.append(
        f"OPT_OUT — falsos positivos: {false_positives} "
        f"({evaluation.opt_out_false_positive_rate:.2%}) "
        f"[presupuesto {OPT_OUT_FALSE_POSITIVE_BUDGET}]"
    )
    lines.append(f"OPT_OUT — falsos negativos: {false_negatives} (bajas que se escaparon)")
    lines.append(f"EXACTITUD GLOBAL: {evaluation.accuracy:.4f} (piso {MIN_ACCURACY:.2f})")
    lines.append("-" * 78)

    if evaluation.misclassified:
        lines.append(f"MAL CLASIFICADAS ({len(evaluation.misclassified)}):")
        for miss in evaluation.misclassified[:MAX_PRINTED_MISCLASSIFICATIONS]:
            sample = miss.sample
            lines.append(
                f"  [{sample['expected_action']} -> {miss.predicted}] "
                f"({sample['category']}/{sample['dialect_or_case']}) {sample['text']!r}"
            )
        remaining = len(evaluation.misclassified) - MAX_PRINTED_MISCLASSIFICATIONS
        if remaining > 0:
            lines.append(f"  ... y {remaining} más")
        lines.append("-" * 78)

    failures = certification_failures(evaluation)
    if failures:
        lines.append("VEREDICTO: NO CERTIFICADO")
        for failure in failures:
            lines.append(f"  - {failure}")
    else:
        lines.append("VEREDICTO: CERTIFICADO (OPT_OUT sin falsos positivos y exactitud en rango)")
    lines.append("=" * 78)
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    """Entrada de línea de comandos: evalúa el corpus y devuelve 0 solo si certifica."""
    parser = argparse.ArgumentParser(
        description=(
            "Lazo continuo del parser de intenciones WhatsApp: matriz de confusión y "
            "certificación de falsos positivos de OPT_OUT."
        )
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=default_dataset_path(),
        help=(
            "Corpus JSONL a evaluar "
            "(por defecto: simulation/datasets/whatsapp_intents_benchmark.jsonl)"
        ),
    )
    arguments = parser.parse_args(argv)
    dataset = arguments.dataset.resolve()
    if not dataset.is_file():
        _logger.error("No existe el corpus: %s", dataset)
        return 2

    evaluation = evaluate(load_benchmark(dataset), dataset=dataset)
    print(format_report(evaluation))
    if not evaluation.certified:
        _logger.error("La corrida NO certificó: %s", "; ".join(certification_failures(evaluation)))
        return 1
    _logger.info(
        "Certificado: %d/%d muestras, 0 falsos positivos de OPT_OUT",
        evaluation.total - len(evaluation.misclassified),
        evaluation.total,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
