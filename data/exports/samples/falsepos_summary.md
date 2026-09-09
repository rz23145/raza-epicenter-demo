# False positive review summary

Source: data/exports/falsepos_review.csv (30 rows reviewed)

| reviewer_category | count | share_of_flagged |
| --- | ---: | ---: |
| legitimate_ceiling_case | 2 | 0.100 |
| possibly_plus_mislabeled | 1 | 0.050 |
| volume_only_legit | 5 | 0.250 |
| volume_only_dropship | 0 | 0.000 |
| new_store_zero_base | 1 | 0.050 |
| score_zero_not_flagged | 10 | excluded |
| unknown | 11 | 0.550 |

Rows with ceiling_score > 0 (the real flagged set): 20
Share denominator (flagged set excluding score_zero_not_flagged): 20
