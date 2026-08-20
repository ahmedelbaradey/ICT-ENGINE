"""ICTFeatureVector — the flat, model-ready projection of ICTMarketState (R2-07).

Full catalogue in ``docs/ict/features.md``; the state layer is in
``docs/ict/market_state.md``. Read those first.

**The vector is a projection, not a store.** ``ICTMarketState`` stays the rich
structured truth and the vector is derived from it, so a model-shaped compromise here
can never degrade the underlying representation. Nothing in this module reads a
detector, a frame or a timestamp comparison — it reads a state that was already built
point-in-time, which is why it cannot leak: there is no future to reach for.

Three encoding rules, all consequential:

* **``0`` and missing are different.** Zero is a real price distance. ``as_dict``
  emits ``None`` and ``as_row`` emits ``math.nan`` for missing; **never zero**.
* **Every distance is in instrument points**, named ``*_points``. Prices and points
  are never mixed, and no ATR or volatility normalisation is introduced — no approved
  contract defines one, and inventing it here would smuggle a modelling hypothesis
  into a representation layer.
* **Column order is ``FEATURE_NAMES``**, a module-level tuple that IS the schema. Same
  input, same order, same dtypes, every run.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from enum import StrEnum

from .cisd import DeliveryState
from .contract import Direction, EventType
from .dealing_range import RangeZone
from .market_state import ICTMarketState, MarketBias
from .structure import StructureState

#: Bumped whenever a feature is added, removed, or its MEANING changes. A dataset
#: records it so results can be tied to the exact definitions that produced them.
FEATURE_VERSION = "r2-07.1"

#: Stable categorical encodings. Declared here, never derived from data order — a
#: label encoder fitted on observed values would silently renumber between datasets.
DIRECTION_CODES: dict[str, int] = {"bearish": -1, "neutral": 0, "bullish": 1}
STRUCTURE_STATE_CODES: dict[str, int] = {"undefined": 0, "bullish": 1, "bearish": -1}
DELIVERY_STATE_CODES: dict[str, int] = {"undefined": 0, "bullish": 1, "bearish": -1}
ZONE_CODES: dict[str, int] = {"discount": -1, "equilibrium": 0, "premium": 1}
BIAS_CODES: dict[str, int] = {"unknown": 0, "bearish": -1, "neutral": 0, "bullish": 1}
BREAK_TYPE_CODES: dict[str, int] = {"bos": 1, "mss": 2, "choch": 3}


def _code(table: dict[str, int], value: StrEnum | None) -> int | None:
    """A categorical code, or ``None`` when the category itself is absent.

    ``None`` rather than a reserved integer: every integer in these tables is a real
    category, and reusing one for "missing" would make the two indistinguishable to a
    model. ``BIAS_CODES`` maps both ``unknown`` and ``neutral`` to 0 deliberately —
    they are different *facts*, kept apart in the state, but a linear model has no use
    for two distinct zeroes, and the state remains the place to tell them apart.
    """
    if value is None:
        return None
    return table.get(str(value.value))


@dataclass(frozen=True)
class ICTFeatureVector:
    """A flat, numeric, versioned projection of one ``ICTMarketState``. Immutable.

    Shaped for XGBoost / LightGBM / logistic regression as-is, and shaped so Kronos
    features can be concatenated later without restructuring. **No label, no target,
    no future** — labelling is Phase 3's concern and is kept strictly separate.
    """

    # --- identity ----------------------------------------------------------
    symbol: str
    timeframe: str
    as_of: datetime
    feature_version: str = FEATURE_VERSION

    # --- price / session ---------------------------------------------------
    close: float | None = None
    distance_from_true_daily_open_points: float | None = None
    distance_from_equilibrium_points: float | None = None
    percentage_position: float | None = None
    is_premium: int | None = None
    is_discount: int | None = None
    is_equilibrium: int | None = None
    dealing_range_width_points: float | None = None
    dealing_range_direction_code: int | None = None

    # --- structure ---------------------------------------------------------
    structure_state_code: int | None = None
    structure_direction_code: int | None = None
    latest_break_type_code: int | None = None
    latest_break_direction_code: int | None = None
    has_bos: int = 0
    has_mss: int = 0
    has_choch: int = 0
    bos_count: int = 0
    mss_count: int = 0
    choch_count: int = 0
    bars_since_structural_break: int | None = None
    distance_from_structural_level_points: float | None = None

    # --- liquidity ---------------------------------------------------------
    buy_side_liquidity_count: int = 0
    sell_side_liquidity_count: int = 0
    nearest_buy_side_points: float | None = None
    nearest_sell_side_points: float | None = None
    has_recent_sweep: int = 0
    latest_sweep_side_code: int | None = None
    latest_sweep_is_rejection: int | None = None
    bars_since_sweep: int | None = None

    # --- imbalance ---------------------------------------------------------
    bullish_fvg_count: int = 0
    bearish_fvg_count: int = 0
    nearest_bullish_fvg_points: float | None = None
    nearest_bearish_fvg_points: float | None = None
    ifvg_count: int = 0
    latest_ifvg_direction_code: int | None = None
    bpr_count: int = 0

    # --- institutional / composites ---------------------------------------
    bullish_order_block_count: int = 0
    bearish_order_block_count: int = 0
    bullish_breaker_count: int = 0
    bearish_breaker_count: int = 0
    latest_breaker_direction_code: int | None = None
    rdrb_count: int = 0
    has_cisd: int = 0
    delivery_state_code: int | None = None
    bars_since_cisd: int | None = None
    unicorn_count: int = 0
    latest_unicorn_direction_code: int | None = None

    # --- session / temporal ------------------------------------------------
    trading_day_age_minutes: float | None = None
    session_elapsed_minutes: float | None = None
    minute_of_session: int | None = None
    active_session_count: int = 0
    day_of_week: int = 0
    hour_of_day: int = 0

    # --- derived bias ------------------------------------------------------
    bias_code: int | None = None
    bullish_evidence_count: int = 0
    bearish_evidence_count: int = 0

    # ------------------------------------------------------------------ build

    @classmethod
    def from_state(cls, state: ICTMarketState) -> ICTFeatureVector:
        """Project a state. Reads only the state — never a frame, never a detector."""
        s, liq = state.structure, state.liquidity
        imb, inst = state.imbalance, state.institutional
        comp, pd_ctx = state.composites, state.premium_discount
        sess, bias, daily = state.session, state.bias, state.daily_open

        zone = pd_ctx.zone
        close = state.bar.close

        distance_from_level = None
        if s.latest_break_level is not None:
            point = _point_value(state.symbol)
            distance_from_level = float((close - s.latest_break_level) / point)

        return cls(
            symbol=state.symbol,
            timeframe=state.timeframe,
            as_of=state.as_of,
            close=close,
            distance_from_true_daily_open_points=daily.distance_points,
            distance_from_equilibrium_points=pd_ctx.distance_from_equilibrium_points,
            # Carried through UNCLAMPED — outside [0, 1] is the common case, because
            # R2-06 anchors on the BROKEN structural level.
            percentage_position=pd_ctx.percentage_position,
            is_premium=_flag(zone, RangeZone.PREMIUM),
            is_discount=_flag(zone, RangeZone.DISCOUNT),
            is_equilibrium=_flag(zone, RangeZone.EQUILIBRIUM),
            dealing_range_width_points=pd_ctx.width_points,
            dealing_range_direction_code=_code(DIRECTION_CODES, pd_ctx.direction),
            structure_state_code=_code(STRUCTURE_STATE_CODES, s.state),
            structure_direction_code=_code(DIRECTION_CODES, s.direction),
            latest_break_type_code=_code(BREAK_TYPE_CODES, s.latest_break_type),
            latest_break_direction_code=_code(DIRECTION_CODES, s.latest_break_direction),
            has_bos=int(s.latest_bos_id is not None),
            has_mss=int(s.latest_mss_id is not None),
            has_choch=int(s.latest_choch_id is not None),
            bos_count=s.bos_count,
            mss_count=s.mss_count,
            choch_count=s.choch_count,
            bars_since_structural_break=s.bars_since_break,
            distance_from_structural_level_points=distance_from_level,
            buy_side_liquidity_count=liq.buy_side_count,
            sell_side_liquidity_count=liq.sell_side_count,
            nearest_buy_side_points=liq.nearest_buy_side_points,
            nearest_sell_side_points=liq.nearest_sell_side_points,
            has_recent_sweep=int(liq.latest_sweep_level_id is not None),
            latest_sweep_side_code=_sweep_side_code(liq.latest_sweep_side),
            latest_sweep_is_rejection=(
                None if liq.latest_sweep_is_rejection is None else int(liq.latest_sweep_is_rejection)
            ),
            bars_since_sweep=liq.bars_since_sweep,
            bullish_fvg_count=imb.bullish_fvg_count,
            bearish_fvg_count=imb.bearish_fvg_count,
            nearest_bullish_fvg_points=imb.nearest_bullish_fvg_points,
            nearest_bearish_fvg_points=imb.nearest_bearish_fvg_points,
            ifvg_count=imb.ifvg_count,
            latest_ifvg_direction_code=_code(DIRECTION_CODES, imb.latest_ifvg_direction),
            bpr_count=imb.bpr_count,
            bullish_order_block_count=inst.bullish_order_block_count,
            bearish_order_block_count=inst.bearish_order_block_count,
            bullish_breaker_count=inst.bullish_breaker_count,
            bearish_breaker_count=inst.bearish_breaker_count,
            latest_breaker_direction_code=_code(DIRECTION_CODES, inst.latest_breaker_direction),
            rdrb_count=comp.rdrb_count,
            has_cisd=int(comp.latest_cisd_id is not None),
            delivery_state_code=_code(DELIVERY_STATE_CODES, comp.delivery_state),
            bars_since_cisd=comp.bars_since_cisd,
            unicorn_count=comp.unicorn_count,
            latest_unicorn_direction_code=_code(DIRECTION_CODES, comp.latest_unicorn_direction),
            trading_day_age_minutes=sess.trading_day_age_minutes,
            session_elapsed_minutes=sess.session_elapsed_minutes,
            minute_of_session=sess.minute_of_session,
            active_session_count=len(sess.active_sessions),
            day_of_week=sess.day_of_week,
            hour_of_day=sess.hour_of_day,
            bias_code=_code(BIAS_CODES, bias.bias),
            bullish_evidence_count=bias.bullish_score,
            bearish_evidence_count=bias.bearish_score,
        )

    # ------------------------------------------------------------ serialisation

    def as_dict(self) -> dict:
        """Deterministic mapping in ``FEATURE_NAMES`` order. Missing is ``None``."""
        out: dict = {
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "as_of": self.as_of.isoformat(),
            "feature_version": self.feature_version,
        }
        for name in FEATURE_NAMES:
            out[name] = getattr(self, name)
        return out

    def as_row(self) -> list[float]:
        """Numeric row in ``FEATURE_NAMES`` order. Missing is ``nan``, never zero."""
        return [_as_float(getattr(self, name)) for name in FEATURE_NAMES]

    @classmethod
    def from_dict(cls, payload: dict) -> ICTFeatureVector:
        """Inverse of :meth:`as_dict`. ``from_dict(v.as_dict()) == v`` exactly."""
        data = dict(payload)
        data["as_of"] = datetime.fromisoformat(data["as_of"])
        known = {spec.name for spec in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    @staticmethod
    def column_names() -> tuple[str, ...]:
        """The schema. Same tuple every run, independent of the data."""
        return FEATURE_NAMES


def _flag(zone: RangeZone | None, wanted: RangeZone) -> int | None:
    """``None`` when there is no range at all — not ``0``.

    Zero here would assert "price is NOT in premium", which is a claim; the absence of
    a dealing range supports no claim either way.
    """
    return None if zone is None else int(zone is wanted)


def _sweep_side_code(side) -> int | None:
    """Buy-side taken is a bearish-leaning fact, sell-side taken a bullish one."""
    if side is None:
        return None
    return {"buy_side": -1, "sell_side": 1}.get(str(side.value))


def _as_float(value) -> float:
    if value is None:
        return math.nan
    if isinstance(value, bool):
        return float(int(value))
    return float(value)


def _point_value(symbol_value: str) -> float:
    from ..domain import Symbol

    return Symbol(symbol_value).spec.point_value


#: **The schema.** Column order for ``as_row`` and ``as_dict``; declared explicitly
#: rather than derived from ``dataclass`` fields so a field reordering cannot silently
#: renumber an existing dataset's columns.
FEATURE_NAMES: tuple[str, ...] = (
    # price / session
    "close",
    "distance_from_true_daily_open_points",
    "distance_from_equilibrium_points",
    "percentage_position",
    "is_premium",
    "is_discount",
    "is_equilibrium",
    "dealing_range_width_points",
    "dealing_range_direction_code",
    # structure
    "structure_state_code",
    "structure_direction_code",
    "latest_break_type_code",
    "latest_break_direction_code",
    "has_bos",
    "has_mss",
    "has_choch",
    "bos_count",
    "mss_count",
    "choch_count",
    "bars_since_structural_break",
    "distance_from_structural_level_points",
    # liquidity
    "buy_side_liquidity_count",
    "sell_side_liquidity_count",
    "nearest_buy_side_points",
    "nearest_sell_side_points",
    "has_recent_sweep",
    "latest_sweep_side_code",
    "latest_sweep_is_rejection",
    "bars_since_sweep",
    # imbalance
    "bullish_fvg_count",
    "bearish_fvg_count",
    "nearest_bullish_fvg_points",
    "nearest_bearish_fvg_points",
    "ifvg_count",
    "latest_ifvg_direction_code",
    "bpr_count",
    # institutional / composites
    "bullish_order_block_count",
    "bearish_order_block_count",
    "bullish_breaker_count",
    "bearish_breaker_count",
    "latest_breaker_direction_code",
    "rdrb_count",
    "has_cisd",
    "delivery_state_code",
    "bars_since_cisd",
    "unicorn_count",
    "latest_unicorn_direction_code",
    # session / temporal
    "trading_day_age_minutes",
    "session_elapsed_minutes",
    "minute_of_session",
    "active_session_count",
    "day_of_week",
    "hour_of_day",
    # derived bias
    "bias_code",
    "bullish_evidence_count",
    "bearish_evidence_count",
)


def feature_vectors(states: list[ICTMarketState]) -> list[ICTFeatureVector]:
    """Project a list of states, preserving order."""
    return [ICTFeatureVector.from_state(s) for s in states]


def vectors_to_frame(vectors: list[ICTFeatureVector]):
    """A DataFrame in ``FEATURE_NAMES`` order, with the identity columns first.

    Kept here so every consumer gets the same column order rather than each building
    its own — the column order IS the schema.
    """
    import pandas as pd

    if not vectors:
        return pd.DataFrame(columns=["symbol", "timeframe", "as_of", "feature_version", *FEATURE_NAMES])
    return pd.DataFrame([v.as_dict() for v in vectors])


__all__ = [
    "BIAS_CODES",
    "BREAK_TYPE_CODES",
    "DELIVERY_STATE_CODES",
    "DIRECTION_CODES",
    "FEATURE_NAMES",
    "FEATURE_VERSION",
    "STRUCTURE_STATE_CODES",
    "ZONE_CODES",
    "DeliveryState",
    "Direction",
    "EventType",
    "ICTFeatureVector",
    "MarketBias",
    "RangeZone",
    "StructureState",
    "feature_vectors",
    "vectors_to_frame",
]
