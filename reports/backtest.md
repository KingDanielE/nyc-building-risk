# Backtest: building risk score

Scored 40,507 Manhattan lots as of 2024-01-01 using only earlier events; outcomes are the 12 months after. Learned models were fit on features as of 2023-01-01 and 2023 outcomes, so 2024 is never seen in training.

## serious_hazard: DOB hazard violation or UNSAFE facade filing

Base rate 1.63% (661 lots).

| Ranker | P@100 | P@250 | P@500 | P@1000 | Recall@500 | Lift@100 | Avg precision |
|---|---|---|---|---|---|---|---|
| learned_v2 (this score) | 34% | 33% | 29% | 21% | 22% | 20.8× | 0.143 |
| gradient boosting (ceiling) | 42% | 38% | 32% | 25% | 24% | 25.7× | 0.215 |
| rules_v1 (hand weights) | 21% | 14% | 11% | 10% | 8% | 12.9× | 0.063 |
| baseline: most violations last 12m | 3% | 4% | 4% | 4% | 3% | 1.8× | 0.026 |
| baseline: same signal last year | 46% | 40% | 33% | 19% | 25% | 28.2× | 0.138 |
| baseline: oldest building | 0% | 0% | 0% | 0% | 0% | 0.0× | 0.012 |
| random | 0% | 2% | 2% | 2% | 1% | 0.0× | 0.016 |

## housing_distress: 10+ new HPD class C violations

Base rate 3.60% (1459 lots).

| Ranker | P@100 | P@250 | P@500 | P@1000 | Recall@500 | Lift@100 | Avg precision |
|---|---|---|---|---|---|---|---|
| learned_v2 (this score) | 92% | 88% | 79% | 65% | 27% | 25.5× | 0.563 |
| gradient boosting (ceiling) | 91% | 86% | 80% | 67% | 27% | 25.3× | 0.573 |
| rules_v1 (hand weights) | 59% | 58% | 54% | 47% | 18% | 16.4× | 0.367 |
| baseline: most violations last 12m | 86% | 84% | 78% | 62% | 27% | 23.9× | 0.521 |
| baseline: same signal last year | 92% | 83% | 75% | 62% | 26% | 25.5× | 0.510 |
| baseline: oldest building | 0% | 0% | 0% | 0% | 0% | 0.0× | 0.029 |
| random | 5% | 5% | 4% | 3% | 1% | 1.4× | 0.037 |

## learned_v2 weights (logistic coefficients on 0..1 components)

| Component | serious_hazard | housing_distress |
|---|---|---|
| `open_c` | +0.70 | +3.92 |
| `c_count` | +0.17 | +4.48 |
| `c_rate` | -0.10 | -1.20 |
| `dob_hazard` | -0.19 | +0.08 |
| `facade_unsafe` | +3.19 | +0.30 |
| `facade_swarmp` | +1.92 | -0.17 |
| `dob_open` | +1.58 | +0.23 |
| `equipment` | +0.44 | +0.54 |
| `trend` | +0.01 | +0.40 |
| `age` | -0.17 | +0.24 |

## Today's top 10 (2026-10-26)

| Rank | BBL | Address | Score | Main risk | Drivers |
|---|---|---|---|---|---|
| 1 | 1020090012 | 151 WEST 140 STREET | 100.0 | serious_hazard | facade UNSAFE; 21 open DOB violations; 35 open class C violations |
| 2 | 1018250045 | 120 WEST 116 STREET | 100.0 | housing_distress | 94 new class C in 12m; 82 open class C violations; 4 boiler/elevator violations (12m) |
| 3 | 1016010027 | 1 WEST 117 STREET | 100.0 | serious_hazard | facade UNSAFE; 13 open DOB violations; 53 open class C violations |
| 4 | 1018510005 | 143 WEST 96 STREET | 100.0 | housing_distress | 96 new class C in 12m; 82 open class C violations; HPD B+C rising (49 -> 116 per 6m) |
| 5 | 1016710031 | 326 EAST 100 STREET | 100.0 | housing_distress | 146 new class C in 12m; 89 open class C violations; HPD B+C rising (150 -> 194 per 6m) |
| 6 | 1006397501 | 151 BANK STREET | 100.0 | serious_hazard | facade UNSAFE; 19 open DOB violations; 5 boiler/elevator violations (12m) |
| 7 | 1020160050 | 2541 ADAM C POWELL BLVD | 100.0 | housing_distress | 144 new class C in 12m; 335 open class C violations; 2 boiler/elevator violations (12m) |
| 8 | 1021350060 | 158-18 RIVERSIDE DRIVE WEST | 100.0 | serious_hazard | facade UNSAFE; 46 open DOB violations; 6 boiler/elevator violations (12m) |
| 9 | 1020730001 | 3458 BROADWAY | 100.0 | serious_hazard | facade UNSAFE; 26 open DOB violations; 26 open class C violations |
| 10 | 1017600001 | 2225 5 AVENUE | 100.0 | housing_distress | 113 new class C in 12m; 78 open class C violations; HPD B+C rising (86 -> 164 per 6m) |

## Caveats

- rules_v1's hand weights were set before seeing outcomes and lost to simple baselines; learned_v2 keeps the explainable form but fits the weights on 2023.
- Static attributes (owner, floors, units) are today's PLUTO, also in the backtest.
- Outcomes are recorded violations, so they reflect where agencies inspect: the score predicts 'will get a serious violation', not 'is unsafe'.
- DOB complaints end 2024-04-15, so learned_v2 and boosting don't use them.
- HPD close dates are approximated by the current-status date.
