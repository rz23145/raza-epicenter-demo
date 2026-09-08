"""Score: components sum to total, unverified halves weight, nulls are zero
and labeled."""

from __future__ import annotations

from ceiling.features.score import NOTE_NULL, NOTE_UNVERIFIED, ceiling_score
from ceiling.settings import Settings


def base_features() -> dict[str, object]:
    return {
        "workaround_app_count": 0,
        "workaround_capability_count": 0,
        "workaround_b2b": 0,
        "workaround_multistore": 0,
        "workaround_launch": 0,
        "workaround_discount": 0,
        "workaround_sso": 0,
        "workaround_intl": 0,
        "workaround_intl_app_count": 0,
        "workaround_verified_count": 0,
        "capability_verified": {},
        "product_count": None,
        "catalog_growth_yoy": None,
        "products_last_90d": None,
        "variant_count": None,
        "vendor_count": None,
        "available_share": None,
        "median_price": None,
        "review_count_max": None,
        "review_vendor_count": 0,
        "has_reviews_vendor": 0,
        "hreflang_alt_count": 0,
        "theme_name": None,
        "products_json_available": 0,
    }


def score_settings() -> Settings:
    return Settings.model_validate(
        {
            "contact_email": "a@b.co",
            "score": {
                "weights": {
                    "workaround_b2b": 3.0,
                    "workaround_multistore": 3.0,
                    "workaround_launch": 1.0,
                    "workaround_discount": 1.5,
                    "workaround_sso": 1.5,
                    "workaround_intl": 1.0,
                    "high_growth": 2.0,
                    "large_catalog": 1.0,
                    "review_scale_1k": 1.0,
                    "review_scale_5k": 2.0,
                },
                "thresholds": {
                    "ceiling_score": 3.0,
                    "high_growth_yoy": 0.5,
                    "high_growth_last_90d": 20,
                    "large_catalog_products": 500,
                    "review_scale_1k": 1000,
                    "review_scale_5k": 5000,
                    "intl_min_apps": 2,
                },
                "unverified_weight_multiplier": 0.5,
            },
        }
    )


def test_components_sum_to_total() -> None:
    features = base_features()
    features.update(
        {
            "workaround_b2b": 1,
            "workaround_discount": 1,
            "capability_verified": {"b2b_wholesale": True, "discount_logic": True},
            "product_count": 600,
            "catalog_growth_yoy": 0.8,
            "products_last_90d": 30,
            "review_count_max": 6000,
        }
    )
    total, components = ceiling_score(features, score_settings().score)
    assert total == sum(float(c["contribution"]) for c in components.values())
    # 3.0 b2b + 1.5 discount + 2.0 growth + 1.0 catalog + 2.0 reviews
    assert total == 9.5


def test_unverified_signature_halves_weight() -> None:
    features = base_features()
    features["workaround_b2b"] = 1
    features["capability_verified"] = {"b2b_wholesale": False}
    total, components = ceiling_score(features, score_settings().score)
    assert components["workaround_b2b"]["contribution"] == 1.5
    assert components["workaround_b2b"]["note"] == NOTE_UNVERIFIED
    assert total == 1.5


def test_nulls_contribute_zero_and_are_labeled() -> None:
    total, components = ceiling_score(base_features(), score_settings().score)
    assert total == 0.0
    assert components["high_growth"]["contribution"] == 0.0
    assert components["high_growth"]["note"] == NOTE_NULL
    assert components["large_catalog"]["note"] == NOTE_NULL
    assert components["review_scale"]["note"] == NOTE_NULL


def test_intl_requires_two_apps() -> None:
    features = base_features()
    features.update(
        {
            "workaround_intl": 1,
            "workaround_intl_app_count": 1,
            "capability_verified": {"international_markets": True},
        }
    )
    total, components = ceiling_score(features, score_settings().score)
    assert components["workaround_intl"]["contribution"] == 0.0
    assert total == 0.0

    features["workaround_intl_app_count"] = 2
    total, components = ceiling_score(features, score_settings().score)
    assert components["workaround_intl"]["contribution"] == 1.0
    assert total == 1.0


def test_review_scale_steps() -> None:
    features = base_features()
    features["review_count_max"] = 1200
    total, _ = ceiling_score(features, score_settings().score)
    assert total == 1.0
    features["review_count_max"] = 5200
    total, _ = ceiling_score(features, score_settings().score)
    assert total == 2.0
    features["review_count_max"] = 12
    total, _ = ceiling_score(features, score_settings().score)
    assert total == 0.0
