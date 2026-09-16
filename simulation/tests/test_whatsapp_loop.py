"""Tests del lazo continuo de intenciones WhatsApp (``simulation/whatsapp_intent_loop.py``).

El lazo es una compuerta, así que sus tests prueban dos cosas distintas:

* que el corpus canónico CERTIFIQUE hoy — en particular con CERO falsos positivos de ``OPT_OUT``,
  que es la propiedad que el parser no puede violar porque una supresión no tiene vuelta; y
* que la compuerta NO sea vacua: un corpus con un error tiene que hacerla fallar, tanto en
  ``certification_failures`` como en el código de salida de ``main``.

Si estos tests se ponen rojos, la respuesta no es aflojar el umbral: es mirar QUÉ muestra se coló.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from simulation.whatsapp_intent_loop import (
    OPT_OUT,
    Evaluation,
    IntentSample,
    Misclassification,
    certification_failures,
    default_dataset_path,
    evaluate,
    format_report,
    load_benchmark,
    main,
)


def _sample(text: str, expected_action: str) -> IntentSample:
    return IntentSample(
        text=text,
        expected_action=expected_action,
        category="test",
        dialect_or_case="test",
    )


def test_the_canonical_corpus_classifies_every_sample() -> None:
    """La calibración vigente: 625/625. Un rojo aquí es una regresión del parser, no del corpus."""
    evaluation = evaluate(load_benchmark(default_dataset_path()), dataset=default_dataset_path())

    assert evaluation.total == 625
    assert evaluation.misclassified == (), [
        (miss.sample["text"], miss.sample["expected_action"], miss.predicted)
        for miss in evaluation.misclassified
    ]
    assert evaluation.accuracy == 1.0


def test_opt_out_false_positive_rate_is_exactly_zero() -> None:
    """==La propiedad que manda.== Ningún mensaje que NO era una baja puede terminar suprimiendo al
    huésped: ``phone_suppressions`` es de instancia, sobrevive al borrado de datos y no vuelve."""
    evaluation = evaluate(load_benchmark(default_dataset_path()), dataset=default_dataset_path())

    assert evaluation.opt_out_false_positives == ()
    assert evaluation.opt_out_false_positive_rate == 0.0
    assert not any(
        failure.startswith("falsos positivos de OPT_OUT")
        for failure in certification_failures(evaluation)
    )


def test_the_gate_fails_when_a_non_opt_out_sample_is_suppressed() -> None:
    """Anti-vacuidad: la compuerta tiene que morder cuando alguien introduce un falso positivo.

    La muestra se fabrica en vez de buscarla en el corpus: el parser de hoy no convierte ningún
    "confirma" en una baja — esa es justamente la propiedad certificada — así que el único modo de
    probar que la compuerta NO es decorativa es alimentarla con el falso positivo que debe atajar.
    """
    suppressed = _sample("1 - confirmo", "CONFIRM_ATTENDANCE")
    evaluation = Evaluation(
        dataset=Path("memoria.jsonl"),
        total=1,
        confusion={},
        metrics={},
        misclassified=(Misclassification(sample=suppressed, predicted=OPT_OUT),),
        opt_out_false_positives=(suppressed,),
        opt_out_false_negatives=(),
    )

    failures = certification_failures(evaluation)
    assert any(failure.startswith("falsos positivos de OPT_OUT: 1") for failure in failures)
    assert not evaluation.certified


def test_the_report_shows_the_matrix_and_the_certified_verdict() -> None:
    evaluation = evaluate(load_benchmark(default_dataset_path()), dataset=default_dataset_path())

    report = format_report(evaluation)

    assert "MATRIZ DE CONFUSIÓN" in report
    assert "falsos positivos: 0 (0.00%)" in report
    assert "VEREDICTO: CERTIFICADO" in report


def test_main_certifies_the_canonical_corpus() -> None:
    """El modo CLI cierra en 0 solo si certifica."""
    exit_code = main(["--dataset", str(default_dataset_path())])

    assert exit_code == 0


def test_main_returns_nonzero_on_a_corpus_that_does_not_certify(tmp_path: Path) -> None:
    """Un corpus con expectativas falsas no puede salir 0: el lazo no es decorativo."""
    corpus = tmp_path / "corpus_roto.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "text": "1",
                "expected_action": "CANCEL",
                "category": "test",
                "dialect_or_case": "test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["--dataset", str(corpus)]) == 1


def test_load_benchmark_rejects_an_unknown_action(tmp_path: Path) -> None:
    corpus = tmp_path / "corpus_invalido.jsonl"
    corpus.write_text(
        json.dumps(
            {
                "text": "hola",
                "expected_action": "MAYBE",
                "category": "test",
                "dialect_or_case": "test",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="expected_action desconocido"):
        load_benchmark(corpus)
