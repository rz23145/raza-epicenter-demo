# Cross-sectional validation

Scan date: 2026-09-09
Positives (plus_positive) with features: 36
Negatives (non_plus_negative) with features: 40
Base rate: 0.474

## Headline numbers

- AUC of ceiling_score: 0.494
- Bootstrap 95% CI (1000 resamples, seed 7): [0.368, 0.613]
- Precision at top decile: 0.571, recall: 0.111
- At threshold 3.0: precision 0.364, recall 0.111, flagged 11

## Single-feature AUCs (size control)

- product_count alone: 0.658
- workaround_app_count alone: 0.501

## AUC within product_count quartiles

- q1_smallest: 0.700
- q2: 0.353
- q3: 0.488
- q4_largest: 0.238

## Reading these numbers: the workaround inversion

Plus positives may show fewer workaround apps than the highest scoring
negatives, because a store that upgraded replaced its workarounds with the
native feature. If workaround features show AUC below 0.5 on this
Plus-vs-non-Plus task, that is consistent with the thesis, not against it:
the workaround is a signature of strain BEFORE the upgrade, and this
cross-section observes stores AFTER their plan status settled. The
cross-sectional test is therefore a weak test of the workaround signal. The
volume proxies (catalog size, review scale) are the components this test can
meaningfully validate. The real test of the workaround signal is the Wayback
backtest, which observes migrators before their migration month.

## Diagnostic logistic regression

5-fold CV AUC: 0.650 +/- 0.121 (5 folds)

Coefficients (standardized inputs, nulls imputed to zero,
diagnostic only, the index uses the transparent score):

- workaround_app_count: -0.227
- workaround_capability_count: -0.095
- workaround_b2b: -0.371
- workaround_multistore: +0.000
- workaround_launch: +0.000
- workaround_discount: +0.000
- workaround_sso: +0.238
- workaround_intl: +0.000
- product_count: +0.971
- catalog_growth_yoy: +0.225
- products_last_90d: -0.630
- review_count_max: +0.593
- hreflang_alt_count: +1.294
