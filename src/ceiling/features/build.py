"""Per store, per scan feature vector.

Feature keys, all present in every vector:

workaround_app_count        int, distinct app_keys matched, capability != checkout_customization
workaround_capability_count int, distinct capabilities covered
workaround_b2b              0/1
workaround_multistore       0/1
workaround_launch           0/1
workaround_discount         0/1
workaround_sso              0/1
workaround_intl             0/1
workaround_intl_app_count   int, distinct international_markets apps (the score
                            counts intl only when this is >= the configured minimum)
workaround_verified_count   int, matches whose signature is verified
capability_verified         dict of capability -> bool, true when at least one
                            verified signature matched for that capability; used
                            by the score to halve unverified contributions
product_count               int or None
catalog_growth_yoy          float or None, (last_365d - prior_365d) / max(prior_365d, 1)
products_last_90d           int or None
variant_count               int or None
vendor_count                int or None
available_share             float or None
median_price                float or None
review_count_max            int or None, max across vendors
review_vendor_count         int
has_reviews_vendor          0/1
hreflang_alt_count          int
theme_name                  str or None
products_json_available     0/1

Plus fingerprints are never inputs here. The builder does not accept them and
a test fails if any fingerprint key appears in the output.
"""

from __future__ import annotations

from ceiling.detect.extract import ExtractResult
from ceiling.detect.products import CatalogResult
from ceiling.detect.reviews import ReviewReading
from ceiling.detect.signatures import SignatureMatch

_CAPABILITY_FLAGS = {
    "b2b_wholesale": "workaround_b2b",
    "multistore_sync": "workaround_multistore",
    "launch_scheduling": "workaround_launch",
    "discount_logic": "workaround_discount",
    "sso_login": "workaround_sso",
    "international_markets": "workaround_intl",
}


def build_features(
    app_matches: list[SignatureMatch],
    catalog: CatalogResult | None,
    reviews: list[ReviewReading],
    home_extract: ExtractResult | None,
    products_json_available: bool,
) -> dict[str, object]:
    counted = [m for m in app_matches if m.capability != "checkout_customization"]
    capabilities = {m.capability for m in counted if m.capability}

    capability_verified: dict[str, bool] = {}
    for cap in capabilities:
        capability_verified[cap] = any(
            m.verified for m in counted if m.capability == cap
        )

    features: dict[str, object] = {
        "workaround_app_count": len({m.key for m in counted}),
        "workaround_capability_count": len(capabilities),
        "workaround_verified_count": sum(1 for m in counted if m.verified),
        "workaround_intl_app_count": len(
            {m.key for m in counted if m.capability == "international_markets"}
        ),
        "capability_verified": capability_verified,
    }
    for capability, flag in _CAPABILITY_FLAGS.items():
        features[flag] = 1 if capability in capabilities else 0

    if catalog is not None and catalog.products_json_available:
        last = catalog.products_created_last_365d
        prior = catalog.products_created_prior_365d
        growth: float | None = None
        if last is not None and prior is not None:
            growth = (last - prior) / max(prior, 1)
        features.update(
            {
                "product_count": catalog.product_count,
                "catalog_growth_yoy": growth,
                "products_last_90d": catalog.products_created_last_90d,
                "variant_count": catalog.variant_count,
                "vendor_count": catalog.vendor_count,
                "available_share": catalog.available_share,
                "median_price": catalog.median_price,
            }
        )
    else:
        features.update(
            {
                "product_count": None,
                "catalog_growth_yoy": None,
                "products_last_90d": None,
                "variant_count": None,
                "vendor_count": None,
                "available_share": None,
                "median_price": None,
            }
        )

    counts = [r.review_count for r in reviews if r.review_count is not None]
    detected_vendors = {r.vendor for r in reviews if r.vendor != "none"}
    features.update(
        {
            "review_count_max": max(counts) if counts else None,
            "review_vendor_count": len(detected_vendors),
            "has_reviews_vendor": 1 if detected_vendors else 0,
            "hreflang_alt_count": (
                len(home_extract.hreflang_alternates) if home_extract else 0
            ),
            "theme_name": home_extract.theme_name if home_extract else None,
            "products_json_available": 1 if products_json_available else 0,
        }
    )
    return features
