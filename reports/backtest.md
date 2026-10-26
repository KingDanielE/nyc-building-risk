# Backtest: building risk score

Scored 40,507 Manhattan lots as of 2024-01-01 using only earlier events; outcomes are the 12 months after. Learned models were fit on features as of 2023-01-01 and 2023 outcomes, so 2024 is never seen in training. Which components each model uses was decided on a separate validation split (fit 2022, validate 2023), before the 2024 test was run.

## Component choice (validation year, not the test)

| Added to v2 | serious_hazard AP / P@100 | housing_distress AP / P@100 |
|---|---|---|
| v2 | 0.104 / 23% | 0.510 / 90% |
| +complaints | 0.100 / 19% | 0.510 / 85% |
| +repeat offender | 0.115 / 35% | 0.510 / 90% |
| +landlord & agent | 0.099 / 24% | 0.531 / 92% |
| +all | 0.106 / 28% | 0.530 / 91% |

learned_v3 = v2 + repeat offender for serious_hazard, v2 + landlord & agent for housing_distress.

## serious_hazard: DOB hazard violation or UNSAFE facade filing

Base rate 1.63% (661 lots).

| Ranker | P@100 | P@250 | P@500 | P@1000 | Recall@500 | Lift@100 | Avg precision |
|---|---|---|---|---|---|---|---|
| learned_v3 (this score) | 32% | 34% | 29% | 20% | 22% | 19.6× | 0.146 |
| learned_v2 (previous) | 34% | 33% | 29% | 21% | 22% | 20.8× | 0.143 |
| gradient boosting (ceiling) | 41% | 37% | 29% | 24% | 22% | 25.1× | 0.208 |
| rules_v1 (hand weights) | 21% | 14% | 11% | 10% | 8% | 12.9× | 0.063 |
| baseline: most violations last 12m | 3% | 4% | 4% | 4% | 3% | 1.8× | 0.026 |
| baseline: same signal last year | 50% | 39% | 33% | 19% | 25% | 30.6× | 0.138 |
| baseline: oldest building | 0% | 0% | 0% | 0% | 0% | 0.0× | 0.012 |
| random | 1% | 2% | 1% | 1% | 1% | 0.6× | 0.016 |

## housing_distress: 10+ new HPD class C violations

Base rate 3.60% (1459 lots).

| Ranker | P@100 | P@250 | P@500 | P@1000 | Recall@500 | Lift@100 | Avg precision |
|---|---|---|---|---|---|---|---|
| learned_v3 (this score) | 91% | 88% | 80% | 64% | 28% | 25.3× | 0.572 |
| learned_v2 (previous) | 92% | 88% | 79% | 65% | 27% | 25.5× | 0.563 |
| gradient boosting (ceiling) | 91% | 84% | 79% | 65% | 27% | 25.3× | 0.570 |
| rules_v1 (hand weights) | 59% | 58% | 54% | 47% | 18% | 16.4× | 0.367 |
| baseline: most violations last 12m | 86% | 85% | 78% | 62% | 27% | 23.9× | 0.521 |
| baseline: same signal last year | 92% | 83% | 75% | 62% | 26% | 25.5× | 0.510 |
| baseline: oldest building | 0% | 0% | 0% | 0% | 0% | 0.0× | 0.029 |
| random | 0% | 4% | 2% | 2% | 1% | 0.0× | 0.034 |

## learned_v3 weights (logistic coefficients on 0..1 components)

| Component | serious_hazard | housing_distress |
|---|---|---|
| `age` | -0.14 | -0.14 |
| `agent_c_rate` |  | +1.45 |
| `c_count` | +0.18 | +4.59 |
| `c_rate` | -0.02 | -3.07 |
| `dob_hazard` | -0.27 | -0.10 |
| `dob_open` | +1.69 | +0.19 |
| `equipment` | +0.41 | +0.47 |
| `facade_swarmp` | +1.93 | -0.05 |
| `facade_unsafe` | +2.68 | +0.35 |
| `hazard_any` | -0.32 |  |
| `open_c` | +0.55 | +3.34 |
| `portfolio_c_rate` |  | +1.56 |
| `portfolio_hazards` |  | +0.72 |
| `portfolio_unsafe` |  | +0.04 |
| `trend` | +0.03 | +0.34 |
| `unsafe_and_hazard` | +1.15 |  |

## Today's top 10 (2026-10-26)

| Rank | BBL | Address | Score | Main risk | Drivers |
|---|---|---|---|---|---|
| 1 | 1020730001 | 3458 BROADWAY | 100.0 | serious_hazard | facade UNSAFE; 26 open DOB violations; UNSAFE facade with past hazard violations |
| 2 | 1017600001 | 2225 5 AVENUE | 100.0 | housing_distress | 113 new class C in 12m; 78 open class C violations; managing agent: 26.94 class C per 100 units |
| 3 | 1016710031 | 326 EAST 100 STREET | 100.0 | housing_distress | 146 new class C in 12m; 89 open class C violations; managing agent: 158.7 class C per 100 units |
| 4 | 1004400051 | 417 EAST 12 STREET | 100.0 | serious_hazard | facade UNSAFE; 26 open DOB violations; UNSAFE facade with past hazard violations |
| 5 | 1018250045 | 120 WEST 116 STREET | 100.0 | housing_distress | 94 new class C in 12m; 82 open class C violations; landlord's 7 lots: 61.25 class C per 100 units |
| 6 | 1018670001 | 1101 AMSTERDAM AVENUE | 100.0 | serious_hazard | facade UNSAFE; 71 open DOB violations; UNSAFE facade with past hazard violations |
| 7 | 1020160050 | 2541 ADAM C POWELL BLVD | 100.0 | housing_distress | 144 new class C in 12m; 335 open class C violations; landlord's 2 lots: 14.87 class C per 100 units |
| 8 | 1007590018 | 335 WEST 35 STREET | 100.0 | serious_hazard | facade UNSAFE; 40 open DOB violations; UNSAFE facade with past hazard violations |
| 9 | 1018510005 | 143 WEST 96 STREET | 100.0 | housing_distress | 96 new class C in 12m; 82 open class C violations; landlord's 2 lots: 77.95 class C per 100 units |
| 10 | 1020870050 | 640 WEST 139 STREET | 100.0 | serious_hazard | facade UNSAFE; 8 open DOB violations; UNSAFE facade with past hazard violations |

## Caveats

- rules_v1's hand weights were set before seeing outcomes and lost to simple baselines; learned_v2 keeps the explainable form but fits the weights on 2023.
- Static attributes (owner, floors, units) and landlord portfolios / agents are today's PLUTO and HPD registrations, also in the backtest.
- Outcomes are recorded violations, so they reflect where agencies inspect: the score predicts 'will get a serious violation', not 'is unsafe'.
- Validation features as of 2022-01-01 see only 24 of the 36 months for 3-year windows (the data starts in 2020).
- HPD close dates are approximated by the current-status date.
