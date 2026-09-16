#!/usr/bin/env python3
"""
AetherCal - Hugging Face Datasets Query & Inspection Script
Conecta a la API pública de Hugging Face (https://huggingface.co/api/datasets)
y al servidor de datasets (https://datasets-server.huggingface.co) para
inspeccionar datasets de intenciones conversacionales, citas y opt-out.
"""

import json
import sys
import urllib.parse
import urllib.request
from typing import Any

sys.stdout.reconfigure(encoding="utf-8")

HF_API_BASE = "https://huggingface.co/api/datasets"
HF_SERVER_BASE = "https://datasets-server.huggingface.co"
USER_AGENT = "AetherCal-Research/1.0 (AetherLogik Research Engine)"

SEARCH_TOPICS = {
    "citas_agendamiento": ["appointment", "scheduling", "calendar"],
    "intenciones_conversacionales": ["conversational intent", "intent", "dialog act"],
    "mensajeria_sms_whatsapp": ["whatsapp", "sms", "opt-out", "stop"],
    "multilingue_espanol": ["spanish intent", "mtop", "massive"],
}

SELECTED_DATASETS = [
    {
        "id": "SetFit/amazon_massive_intent_es-ES",
        "parent_id": "AmazonScience/massive",
        "config": "default",
        "split": "train",
        "name": "Amazon MASSIVE (Subconjunto Español es-ES)",
    },
    {
        "id": "clinc/clinc_oos",
        "parent_id": "clinc/clinc_oos",
        "config": "plus",
        "split": "train",
        "name": "CLINC 150 / OOS (FastFit/clinc_150)",
    },
    {
        "id": "vidhikatkoria/DA_SGD_Calendar",
        "parent_id": "GEM/schema_guided_dialog",
        "config": "default",
        "split": "train",
        "name": "Schema-Guided Dialogue Calendar (SGD Calendar DA)",
    },
    {
        "id": "ucirvine/sms_spam",
        "parent_id": "ucirvine/sms_spam",
        "config": "plain_text",
        "split": "train",
        "name": "SMS Collection & Opt-Out (UC Irvine)",
    },
    {
        "id": "tasksource/mtop",
        "parent_id": "WillHeld/mtop",
        "config": "mtop",
        "split": "train",
        "name": "Meta MTOP (Multilingual Task-Oriented Parsing)",
    },
]


def make_request(url: str) -> dict[str, Any] | None:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as exc:
        print(f"[ERROR] Falló solicitud a {url}: {exc}", file=sys.stderr)
        return None


def search_hf_api(query: str, limit: int = 10) -> list[dict[str, Any]]:
    url = f"{HF_API_BASE}?search={urllib.parse.quote(query)}&limit={limit}&full=false"
    res = make_request(url)
    return res if isinstance(res, list) else []


def inspect_dataset_rows(
    dataset_id: str, config: str, split: str, offset: int = 0, limit: int = 5
) -> dict[str, Any]:
    query_str = urllib.parse.urlencode(
        {"dataset": dataset_id, "config": config, "split": split, "offset": offset, "limit": limit}
    )
    url = f"{HF_SERVER_BASE}/rows?{query_str}"
    data = make_request(url)
    return data or {}


def main() -> None:
    print("=================================================================")
    print("AetherCal: Exploración y Auditoría de Datasets en Hugging Face")
    print("=================================================================")

    print("\n1. Búsqueda exploratoria por ejes temáticos:")
    for category, queries in SEARCH_TOPICS.items():
        print(f"\n--- Categoría: {category} ---")
        for q in queries:
            results = search_hf_api(q, limit=5)
            print(f"  Consulta '{q}': {len(results)} resultados encontrados")
            for r in results[:3]:
                downloads = r.get("downloads", 0)
                likes = r.get("likes", 0)
                print(f"    - {r.get('id')} (Descargas: {downloads}, Likes: {likes})")

    print("\n2. Inspección estructurada de datasets prioritarios:")
    for ds in SELECTED_DATASETS:
        print(f"\n>>> Inspeccionando {ds['name']} [{ds['id']}] <<<")
        data = inspect_dataset_rows(ds["id"], ds["config"], ds["split"], limit=3)
        features = data.get("features", [])
        rows = data.get("rows", [])
        print(f"    Features: {[f.get('feature_idx') or f.get('name') for f in features]}")
        print(f"    Filas obtenidas: {len(rows)}")
        for idx, row_item in enumerate(rows[:2]):
            sample_str = json.dumps(row_item.get("row"), ensure_ascii=False)[:180]
            print(f"    Muestra {idx + 1}: {sample_str}...")


if __name__ == "__main__":
    main()
