# Evaluation on held-out lots

Test set: 51839 wafers from 3228 lots that were not used for training or calibration. Engine settings from model/trained.json (checkpoint every 16 dies, call after 3 checkpoints in a row, margins for 90% precision on the calibration lots).

## E1. Class accuracy vs share of the wafer tested (test lots, serpentine order)

| tested | balanced acc. | accuracy | none | Center | Donut | Edge-Loc | Edge-Ring | Loc | Near-full | Random | Scratch |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 10% | 0.568 | 0.566 | 0.56 | 0.61 | 0.63 | 0.29 | 0.91 | 0.15 | 0.78 | 0.76 | 0.43 |
| 20% | 0.607 | 0.625 | 0.63 | 0.58 | 0.64 | 0.41 | 0.90 | 0.21 | 0.83 | 0.80 | 0.46 |
| 30% | 0.663 | 0.715 | 0.73 | 0.62 | 0.81 | 0.47 | 0.91 | 0.26 | 0.88 | 0.84 | 0.44 |
| 40% | 0.694 | 0.700 | 0.70 | 0.67 | 0.79 | 0.52 | 0.93 | 0.34 | 0.93 | 0.85 | 0.52 |
| 50% | 0.750 | 0.762 | 0.76 | 0.86 | 0.86 | 0.56 | 0.94 | 0.42 | 0.90 | 0.90 | 0.56 |
| 60% | 0.775 | 0.805 | 0.81 | 0.91 | 0.88 | 0.62 | 0.93 | 0.49 | 0.90 | 0.91 | 0.53 |
| 70% | 0.806 | 0.832 | 0.84 | 0.90 | 0.93 | 0.64 | 0.93 | 0.55 | 0.93 | 0.94 | 0.59 |
| 80% | 0.826 | 0.850 | 0.86 | 0.91 | 0.95 | 0.66 | 0.94 | 0.58 | 0.95 | 0.94 | 0.65 |
| 90% | 0.837 | 0.867 | 0.87 | 0.91 | 0.94 | 0.69 | 0.94 | 0.61 | 0.95 | 0.94 | 0.67 |
| 100% | 0.863 | 0.885 | 0.89 | 0.91 | 0.95 | 0.82 | 0.95 | 0.63 | 0.98 | 0.96 | 0.68 |

Per-class columns are recall (share of that class's wafers labelled correctly).

## E2. Full-map confusion matrix (rows: true class, columns: engine)

| true \ engine | none | Center | Donut | Edge-Loc | Edge-Ring | Loc | Near-full | Random | Scratch | recall |
|---|---|---|---|---|---|---|---|---|---|---|
| none | 39061 | 452 | 46 | 1214 | 227 | 549 | 0 | 71 | 2310 | 0.889 |
| Center | 57 | 1200 | 17 | 6 | 1 | 13 | 1 | 3 | 21 | 0.910 |
| Donut | 0 | 1 | 205 | 2 | 0 | 3 | 0 | 4 | 0 | 0.953 |
| Edge-Loc | 30 | 20 | 0 | 1341 | 55 | 99 | 0 | 27 | 69 | 0.817 |
| Edge-Ring | 5 | 3 | 2 | 113 | 2806 | 2 | 0 | 1 | 9 | 0.954 |
| Loc | 55 | 50 | 65 | 91 | 1 | 707 | 0 | 15 | 139 | 0.630 |
| Near-full | 0 | 0 | 0 | 0 | 0 | 0 | 40 | 1 | 0 | 0.976 |
| Random | 0 | 2 | 1 | 1 | 0 | 2 | 2 | 268 | 4 | 0.957 |
| Scratch | 44 | 5 | 0 | 22 | 0 | 39 | 0 | 1 | 238 | 0.682 |
| precision | 0.995 | 0.692 | 0.610 | 0.481 | 0.908 | 0.500 | 0.930 | 0.685 | 0.085 | |

## E3. Early calls (test lots, serpentine order)

| class | wafers | early calls | correct | precision | coverage | median share tested (25th-75th pct.) |
|---|---|---|---|---|---|---|
| Center | 1319 | 1059 | 992 | 0.937 | 0.752 | 47% (43-59%) |
| Donut | 215 | 154 | 139 | 0.903 | 0.647 | 47% (37-66%) |
| Edge-Loc | 1641 | 443 | 409 | 0.923 | 0.249 | 49% (28-74%) |
| Edge-Ring | 2941 | 3208 | 2760 | 0.860 | 0.938 | 9% (6-10%) |
| Loc | 1123 | 213 | 197 | 0.925 | 0.175 | 52% (25-75%) |
| Near-full | 41 | 30 | 25 | 0.833 | 0.610 | 26% (22-47%) |
| Random | 280 | 251 | 211 | 0.841 | 0.754 | 26% (15-38%) |
| Scratch | 349 | 0 | 0 | - | 0.000 | - |

All early calls: precision 0.883; normal wafers with a false early call: 0.99%; defect wafers named correctly before the end: 59.8%; for those, median share tested 12% (mean dies saved 73%).

## E4. Early calls with a 4-site (2 x 2) prober order (not seen in training)

| class | wafers | early calls | correct | precision | coverage | median share tested (25th-75th pct.) |
|---|---|---|---|---|---|---|
| Center | 1319 | 1060 | 988 | 0.932 | 0.749 | 50% (43-59%) |
| Donut | 215 | 148 | 135 | 0.912 | 0.628 | 48% (38-66%) |
| Edge-Loc | 1641 | 434 | 397 | 0.915 | 0.242 | 49% (28-70%) |
| Edge-Ring | 2941 | 3192 | 2744 | 0.860 | 0.933 | 9% (6-10%) |
| Loc | 1123 | 218 | 203 | 0.931 | 0.181 | 53% (24-77%) |
| Near-full | 41 | 30 | 25 | 0.833 | 0.610 | 26% (19-47%) |
| Random | 280 | 256 | 210 | 0.820 | 0.750 | 29% (18-39%) |
| Scratch | 349 | 0 | 0 | - | 0.000 | - |

All early calls: precision 0.881; normal wafers with a false early call: 0.98%; defect wafers named correctly before the end: 59.5%; for those, median share tested 13% (mean dies saved 73%).

## E5. Probe-site faults (6,000 test wafers, 4-site order, one damaged site)

Check: a site is flagged when it has tested at least 32 dies and its fail rate exceeds the other sites' by 0.275 or more.

| damaged site fails this share of good dies | flagged (correct site) | wrong site | median share of wafer tested at the flag |
|---|---|---|---|
| 0 (healthy card) | 0.12% false alarms | - | - |
| 0.10 | 1.3% | 0.02% | 21.0% |
| 0.25 | 44.4% | 0.02% | 20.8% |
| 0.50 | 99.6% | 0.00% | 17.0% |
| 1.00 | 99.9% | 0.00% | 16.5% |

## E6. Reference: WM-811K's official split

Same features and training recipe (floating point), trained on the official 'Training' wafers (54355) and tested on the official 'Test' wafers (118595) at 100% tested: balanced accuracy 0.691, accuracy 0.853. The official split is also lot-disjoint but has a different class mix between its halves (for example 8,554 Edge-Ring training wafers vs 1,126 test wafers).
