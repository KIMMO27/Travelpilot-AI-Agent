# Travel AI Agent — Evaluation Evidence (2026-11-14)

These results were observed in the developer's Mac terminal and need to remain reproducible. They are **not population-level quality benchmarks**.

## Automated testing

- Stage 6: **36 unit/integration-style automated tests passed** (`python -m unittest discover -s tests -v`).
- The tests cover code paths including model-provider fallback simulation, tool selection, memory, session isolation, trace behavior, itinerary structure and LangGraph failure handling.

## Stage 7 evaluation (`eval_travel_stage7.py --live`)

| Scenario | Expected | Observed | Result |
| --- | --- | --- | --- |
| Melbourne 3-day coffee-oriented itinerary | Success | Success | PASS |
| Unsupported city | Safe failure | Failed outcome | PASS |
| Unsupported duration | Safe failure | Failed outcome | PASS |

**3 / 3 scenarios passed.** In the *single successful Melbourne scenario*:

- Candidate POI slot coverage: **9 / 9 = 100%**.
- Days with route estimates: **3 / 3 = 100%**.
- Answer OSM links consistent with retrieved/validated tool output: **True**.
- Budget price compliance: **Not verified**.

## Interpretation and cautions

- The success case completed in approximately **0.01 s** in the terminal sample because cached responses were available. This **is not live API latency**, nor a general performance guarantee.
- Offline fixture check passed separately, using **synthetic** data solely to verify metric calculations.
- The evaluation disables the optional model greeting; it assesses the grounded LangGraph planning workflow, **not free-form LLM answer quality**.
- No manual ground-truth audit of POI opening status, customer satisfaction test, route optimality comparison, live hotel prices or booking availability was performed.
- Better next experiments: 20+ varied prompts across allowed cities/durations, cold-cache vs warm-cache timing, failed-routing injection, human review of itinerary relevance, and user studies.

Raw local run: `evaluation/reports/stage7_live.json` (private local artifact; ignored by Git by default).
