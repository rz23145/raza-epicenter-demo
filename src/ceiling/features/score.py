"""Transparent additive ceiling score.

Not a fitted model: a PM can read the weights in settings.yaml. Rules:
- Each workaround capability contributes its weight when flagged.
- Contributions from capabilities where no verified signature matched are
  halved (unverified_weight_multiplier) and marked in the components dict.
- international_markets counts only when at least intl_min_apps intl apps
  matched, because each intl app alone is weak.
- high_growth requires catalog_growth_yoy >= threshold and products_last_90d
  >= threshold. Missing inputs contribute zero, marked null_input.
- review_scale is stepped at the 1k and 5k thresholds.

Returns (total, components). Components always sum to the total; a test
enforces it.
"""

from __future__ import annotations

from collections.abc import Mapping

from ceiling.settings import ScoreSettings

NOTE_NULL = "null_input"
NOTE_UNVERIFIED = "unverified_half_weight"


def _num(features: Mapping[str, object], key: str) -> float | None:
    value = features.get(key)
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def ceiling_score(
    features: Mapping[str, object], score_settings: ScoreSettings
) -> tuple[float, dict[str, dict[str, object]]]:
    weights = score_settings.weights
    thresholds = score_settings.thresholds
    multiplier = score_settings.unverified_weight_multiplier
    components: dict[str, dict[str, object]] = {}

    capability_verified_raw = features.get("capability_verified")
    capability_verified: dict[str, bool] = (
        {str(k): bool(v) for k, v in capability_verified_raw.items()}
        if isinstance(capability_verified_raw, dict)
        else {}
    )

    def add(
        name: str,
        active: bool,
        weight: float,
        verified: bool | None = None,
        note: str | None = None,
    ) -> None:
        contribution = weight if active else 0.0
        if active and verified is False:
            contribution *= multiplier
            note = NOTE_UNVERIFIED
        components[name] = {
            "contribution": contribution,
            "weight": weight,
            "active": active,
            "verified": verified,
            "note": note,
        }

    capability_components = [
        ("workaround_b2b", "b2b_wholesale"),
        ("workaround_multistore", "multistore_sync"),
        ("workaround_launch", "launch_scheduling"),
        ("workaround_discount", "discount_logic"),
        ("workaround_sso", "sso_login"),
    ]
    for name, capability in capability_components:
        active = features.get(name) == 1
        add(
            name,
            active,
            weights.get(name, 0.0),
            verified=capability_verified.get(capability) if active else None,
        )

    intl_min = int(thresholds.get("intl_min_apps", 2))
    intl_count = _num(features, "workaround_intl_app_count") or 0
    intl_active = features.get("workaround_intl") == 1 and intl_count >= intl_min
    add(
        "workaround_intl",
        intl_active,
        weights.get("workaround_intl", 0.0),
        verified=(
            capability_verified.get("international_markets") if intl_active else None
        ),
    )

    growth = _num(features, "catalog_growth_yoy")
    last_90 = _num(features, "products_last_90d")
    if growth is None or last_90 is None:
        add("high_growth", False, weights.get("high_growth", 0.0), note=NOTE_NULL)
    else:
        active = growth >= thresholds.get(
            "high_growth_yoy", 0.5
        ) and last_90 >= thresholds.get("high_growth_last_90d", 20)
        add("high_growth", active, weights.get("high_growth", 0.0))

    product_count = _num(features, "product_count")
    if product_count is None:
        add("large_catalog", False, weights.get("large_catalog", 0.0), note=NOTE_NULL)
    else:
        add(
            "large_catalog",
            product_count >= thresholds.get("large_catalog_products", 500),
            weights.get("large_catalog", 0.0),
        )

    review_max = _num(features, "review_count_max")
    if review_max is None:
        add("review_scale", False, weights.get("review_scale_5k", 0.0), note=NOTE_NULL)
    elif review_max >= thresholds.get("review_scale_5k", 5000):
        add("review_scale", True, weights.get("review_scale_5k", 2.0))
    elif review_max >= thresholds.get("review_scale_1k", 1000):
        add("review_scale", True, weights.get("review_scale_1k", 1.0))
    else:
        add("review_scale", False, weights.get("review_scale_1k", 1.0))

    contributions: list[float] = []
    for component in components.values():
        value = component["contribution"]
        assert isinstance(value, float)
        contributions.append(value)
    total = sum(contributions)
    return total, components
