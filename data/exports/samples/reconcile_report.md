# Reconciliation against disclosures

Inputs, each cited to the row in disclosures.csv:
- total MRR: 221 USD millions (2026Q2, https://www.stocktitan.net/sec-filings/SHOP/8-k-shopify-inc-reports-material-event-a0b40f87136b.html, 8-K Ex. 99.1 (2026-08-05), Selected Business Performance Information table, June 30 2026 column; canonical Shopify URL: https://www.shopify.com/news/shopify-q2-2026-financial-results)
- Plus share of MRR: 34 percent (2026Q2, https://www.fool.com/earnings/call-transcripts/2026/08/12/shopify-shop-q2-2026-earnings-call-transcript/, CFO prepared remarks, Q2 2026 earnings call: 'Plus MRR represented 34% of MRR, also growing 19% year-over-year.' Not disclosed in the press release itself.)
- Advanced list price: 399 USD/month (2026Q2, https://www.shopify.com/pricing, Current pricing page (checked 2026-09-09), Compare all features table, Pay monthly row. Pay-yearly price is 299 USD/mo.)
- Plus list price: 2300 USD/month (2026Q2, https://www.shopify.com/pricing, Current pricing page (checked 2026-09-09), Pay monthly row: 'Starts at 2,300 USD/mo'. Page does not tie this price to a 1- or 3-year term; FAQ says Plus is sold on 1- or 3-year subscriptions.)

Subscription revenue delta from one Advanced-to-Plus upgrade at list: 2300 - 399 = 1901 USD/month
Upgrades needed to move Plus share of MRR by one percentage point (approximation: denominator held fixed): 0.01 x 221,000,000 / 1901 = 1,163
Trailing change in Plus share (2025Q2 -> 2026Q2): 35% -> 34% = -1.00pp
Implied annual upgrade count consistent with that change (attributing the whole change to Advanced-to-Plus upgrades at list): -1.00pp x 221,000,000 / 100 / 1901 = -1,163

## Panel bound
Panel bound not computed. It needs a computed index (share_ceiling) and a self-serve base: either a selfserve_merchant_count disclosure row or an explicit --assumed-base argument, stated as an assumption.
