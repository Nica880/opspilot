# Eval reports

Reports are written here by:

| Command | Output |
|---|---|
| `make eval-retrieval` | `<date>-retrieval.md/.json` (duplicate recall@k, MRR, latency per retrieval variant) |
| `make eval` / `make eval-full` | `<date>.md/.json` (triage matrix: labels, duplicates, linked answers, judge, latency, throughput) |
| `make redteam` | `<date>-redteam.json` (50 attack cases, pass rate) |

The methodology is in [ADR-008](../adr/0008-evaluation.md). The README only quotes numbers that
appear in a report in this folder.
