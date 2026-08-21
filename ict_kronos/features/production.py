"""The production universe — what may reach a trading decision, and what may not.

Full semantics in ``docs/features/production_universe.md``.

The production system trades **completed 1H, 4H and Daily candles** on EURUSD and
XAUUSD. Nothing else. 1m, 5m and 15m remain fully supported by every detector, because
they are how the engine is regression-tested and how research questions get asked — but
they are **refused** at the production dataset boundary.

That refusal is a guard, not a filter. It raises rather than silently dropping, because
the failure it exists to prevent is a lower timeframe reaching a model *unnoticed*: an
extra timeframe in a training set does not look like an error, it looks like more data.

.. code-block:: text

    EURUSD x {1H, 4H, 1D}     production: dataset, features, targets, training, decisions
    XAUUSD x {1H, 4H, 1D}

    * x {1m, 5m, 15m}         research and regression ONLY -- never production

The same rule covers the TP/SL ambiguity: a same-bar double touch may **not** be
resolved by looking at a finer timeframe. That would be a lower timeframe entering a
production label through the back door, and the honest answer stays ``UNRESOLVED``.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..domain import Symbol, Timeframe
from .dataset import Dataset, DatasetBuilder, DatasetSpec
from .targets import TargetSpec

#: The production trading universe. Fixed, not configurable — a configurable production
#: universe is one environment variable away from training on 1-minute bars.
PRODUCTION_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.H1, Timeframe.H4, Timeframe.D1)
PRODUCTION_SYMBOLS: tuple[Symbol, ...] = (Symbol.EURUSD, Symbol.XAUUSD)

#: Supported everywhere else in the engine; refused here. Named explicitly so the
#: exclusion is a statement rather than the absence of a statement.
RESEARCH_ONLY_TIMEFRAMES: tuple[Timeframe, ...] = (Timeframe.M1, Timeframe.M5, Timeframe.M15)


class ProductionUniverseError(ValueError):
    """Raised when something outside the production universe reaches a production path."""


def is_production_timeframe(timeframe: Timeframe) -> bool:
    return timeframe in PRODUCTION_TIMEFRAMES


def assert_production_pair(symbol: Symbol, timeframe: Timeframe) -> None:
    """Refuse anything outside ``PRODUCTION_SYMBOLS x PRODUCTION_TIMEFRAMES``.

    Raising rather than filtering is deliberate. A silently dropped combination looks
    exactly like a combination that produced no rows, and the two need very different
    responses.
    """
    if symbol not in PRODUCTION_SYMBOLS:
        raise ProductionUniverseError(
            f"{symbol.value} is not in the production universe "
            f"{tuple(s.value for s in PRODUCTION_SYMBOLS)}"
        )
    if timeframe in RESEARCH_ONLY_TIMEFRAMES:
        raise ProductionUniverseError(
            f"{timeframe.value} is a research/regression timeframe and must never reach a "
            "production dataset, feature vector, target, training run or trading decision. "
            f"Production trades {tuple(t.value for t in PRODUCTION_TIMEFRAMES)}."
        )
    if timeframe not in PRODUCTION_TIMEFRAMES:
        raise ProductionUniverseError(
            f"{timeframe.value} is not a production timeframe "
            f"{tuple(t.value for t in PRODUCTION_TIMEFRAMES)}"
        )


@dataclass(frozen=True)
class ProductionTargetParameters:
    """Target parameters for one instrument × timeframe. Evidence-backed, not tuned.

    Two facts force these to be per-pair rather than global, and both were measured on
    real bars rather than assumed:

    * **A point is not a unit of volatility.** 50 points is 0.0005 on EURUSD and five
      cents on gold. One number applied to both instruments asks two different questions.
    * **Bar range grows with timeframe.** A barrier that a 1H bar rarely spans is one a
      Daily bar almost always spans, and a barrier inside the typical bar range makes
      almost every label ``SAME_BAR_AMBIGUITY``.

    Parameters are chosen from the *measured bar range* of the instrument and timeframe —
    never from which value produced the most resolved labels. Maximising resolution is
    how a label gets tuned into meaninglessness.
    """

    symbol: Symbol
    timeframe: Timeframe
    #: DIRECTION threshold, in instrument points.
    threshold_points: float
    #: TP_BEFORE_SL barrier distances, in instrument points.
    take_profit_points: float
    stop_loss_points: float
    #: Horizons, in bars. Short on Daily because a month holds ~22 Daily bars.
    horizons: tuple[int, ...]
    #: What the numbers were derived from, carried so a reviewer never has to guess.
    rationale: str = ""

    def as_dict(self) -> dict:
        return {
            "symbol": self.symbol.value,
            "timeframe": self.timeframe.value,
            "threshold_points": self.threshold_points,
            "take_profit_points": self.take_profit_points,
            "stop_loss_points": self.stop_loss_points,
            "horizons": list(self.horizons),
            "rationale": self.rationale,
        }


#: Derived from the MEASURED bar range of each pair on the 2026-07 real-data month.
#:
#: The rule, applied uniformly and stated once so nothing is chosen case by case:
#:
#: * ``take_profit_points = stop_loss_points = median bar range`` of the pair. A barrier
#:   at one median range is one a typical bar does not span in BOTH directions, which is
#:   what keeps same-bar ambiguity confined to genuinely exceptional bars instead of
#:   swallowing the label set. On XAUUSD with a flat 50-point barrier it swallowed 86%.
#: * ``threshold_points = median |close - open|`` of the pair — the typical single-bar
#:   move. A move larger than that is a directional statement; a smaller one is NEUTRAL.
#:
#: **Derived from volatility, never from outcome.** No value here was chosen because it
#: produced more resolved labels, and maximising resolution is precisely how a label gets
#: tuned into meaninglessness.
#:
#: **Honest limitation:** these come from the same month the pipeline was validated on.
#: They are a volatility scale, not a fitted parameter, but they should be re-derived
#: from a longer history before any modelling claim rests on them.
PRODUCTION_TARGET_PARAMETERS: tuple[ProductionTargetParameters, ...] = (
    ProductionTargetParameters(
        symbol=Symbol.EURUSD,
        timeframe=Timeframe.H1,
        threshold_points=36.0,
        take_profit_points=85.0,
        stop_loss_points=85.0,
        horizons=(1, 2, 4, 8),
        rationale="2026-07 median 1H range 85 pts, median |close-open| 36 pts",
    ),
    ProductionTargetParameters(
        symbol=Symbol.EURUSD,
        timeframe=Timeframe.H4,
        threshold_points=74.0,
        take_profit_points=179.0,
        stop_loss_points=179.0,
        horizons=(1, 2, 4),
        rationale="2026-07 median 4H range 179 pts, median |close-open| 74 pts",
    ),
    ProductionTargetParameters(
        symbol=Symbol.EURUSD,
        timeframe=Timeframe.D1,
        threshold_points=182.0,
        take_profit_points=462.0,
        stop_loss_points=462.0,
        horizons=(1, 2),
        rationale="2026-07 median 1D range 462 pts, median |close-open| 182 pts; "
        "short horizons because a month holds ~22 daily bars",
    ),
    ProductionTargetParameters(
        symbol=Symbol.XAUUSD,
        timeframe=Timeframe.H1,
        threshold_points=5360.0,
        take_profit_points=13840.0,
        stop_loss_points=13840.0,
        horizons=(1, 2, 4, 8),
        rationale="2026-07 median 1H range 13840 pts (13.84 USD), median |close-open| 5360 pts",
    ),
    ProductionTargetParameters(
        symbol=Symbol.XAUUSD,
        timeframe=Timeframe.H4,
        threshold_points=10550.0,
        take_profit_points=27885.0,
        stop_loss_points=27885.0,
        horizons=(1, 2, 4),
        rationale="2026-07 median 4H range 27885 pts (27.89 USD), median |close-open| 10550 pts",
    ),
    ProductionTargetParameters(
        symbol=Symbol.XAUUSD,
        timeframe=Timeframe.D1,
        threshold_points=28675.0,
        take_profit_points=78960.0,
        stop_loss_points=78960.0,
        horizons=(1, 2),
        rationale="2026-07 median 1D range 78960 pts (78.96 USD), median |close-open| 28675 pts",
    ),
)


def parameters_for(symbol: Symbol, timeframe: Timeframe) -> ProductionTargetParameters:
    """The parameters for one production pair. Refuses anything outside the universe."""
    assert_production_pair(symbol, timeframe)
    for params in PRODUCTION_TARGET_PARAMETERS:
        if params.symbol is symbol and params.timeframe is timeframe:
            return params
    raise ProductionUniverseError(
        f"no target parameters declared for {symbol.value}/{timeframe.value}; production "
        "parameters are derived from measured bar range, never defaulted"
    )


def build_production_dataset(
    frame,
    symbol: Symbol,
    timeframe: Timeframe,
    spec: DatasetSpec,
    *,
    builder: DatasetBuilder | None = None,
    instants: list | None = None,
) -> Dataset:
    """Build a dataset, refusing anything outside the production universe first."""
    assert_production_pair(symbol, timeframe)
    return (builder or DatasetBuilder()).build(frame, symbol, timeframe, spec, instants=instants)


__all__ = [
    "PRODUCTION_SYMBOLS",
    "PRODUCTION_TIMEFRAMES",
    "RESEARCH_ONLY_TIMEFRAMES",
    "PRODUCTION_TARGET_PARAMETERS",
    "ProductionTargetParameters",
    "ProductionUniverseError",
    "TargetSpec",
    "assert_production_pair",
    "build_production_dataset",
    "is_production_timeframe",
    "parameters_for",
]
