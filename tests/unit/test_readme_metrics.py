from __future__ import annotations

from scripts.readme_metrics import render


def test_metrics_section_only_shows_reported_numbers() -> None:
    empty = render(None, None, None)
    assert "not measured yet" in empty and "%" not in empty

    triage = (
        "2026-12-01.json",
        {
            "meta": {"evaluated per config": 50, "cutoff": "2026-06-01", "hardware": "M3 Pro"},
            "results": [
                {
                    "config": {
                        "model": "qwen2.5:7b-instruct",
                        "prompt_version": "v2",
                        "retrieval_variant": "hybrid_rerank",
                    },
                    "label_micro_f1": 0.5123,
                    "dup_recall_at_5": 0.4,
                    "groundedness": None,
                    "citations_correct": 0.9,
                    "p50_ms": 12000,
                    "p95_ms": 30000,
                    "issues_per_hour": 210.4,
                }
            ],
        },
    )
    redteam = (
        "2026-12-01-redteam.json",
        {
            "summary": {"passed": 50, "cases": 50},
            "meta": {"llm_provider": "ollama", "model": "qwen2.5:7b-instruct"},
        },
    )
    text = render(triage, None, redteam)
    assert (
        "| qwen2.5:7b-instruct / v2 / hybrid_rerank | 0.512 | 40.0% | n/a | 90.0% | 12.0 s / 30.0 s | 210 |"
        in text
    )
    assert "docs/eval/2026-12-01.md" in text
    assert "50/50 attacks contained" in text and "model `qwen2.5:7b-instruct`" in text
