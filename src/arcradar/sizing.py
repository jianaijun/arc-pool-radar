"""Is this pool's price a price, or is it a number the curve is holding because nobody has traded?

An AMM quotes continuously and moves only when someone trades it. So a pool that nobody trades
still shows a price, and that price is simply the last trade's, however old. Compare it against a
live exchange and the difference reads as an arbitrage -- a large, persistent, extremely convincing
one. It is staleness wearing the costume of an opportunity.

⭐ This was measured, not imagined. A pool on Arc reported 69.05% of its fills as net profitable
after fees, the best number anything on the chain produced. It held **$866**. The two pools with
real depth reported 0.00% in both directions.

📛 The tell was structural rather than numeric: the apparent dislocation's median lifetime, 129
seconds, was almost exactly the pool's mean interval between swaps. The "edge" was not decaying at
all; it was being refreshed once per trade and sitting still in between. ⇒ A size gate alone would
have caught this one, but the staleness ratio is what says WHY, and it catches the thin pool that
happens to be above whatever dollar threshold was picked.
"""

from __future__ import annotations

import itertools
import statistics
from dataclasses import dataclass
from enum import Enum
from typing import Final

#: Below this, a pool's quoted price is not evidence of anything. Set from the $866 case, which is
#: an observation and not a theory -- treat it as a floor that is known to be too low rather than a
#: threshold that is known to be right.
DUST_USD: Final[float] = 5_000.0
#: Above this a pool is thick enough that its price reflects arriving flow rather than one trader.
TRADEABLE_USD: Final[float] = 20_000.0
#: A pool whose price refreshes this rarely is being read, not quoted.
STALE_SECONDS: Final[float] = 30.0


class Depth(Enum):
    DUST = "dust"
    THIN = "thin"
    TRADEABLE = "tradeable"


@dataclass(frozen=True)
class Verdict:
    """What a pool's own trade record says about whether its price can be believed."""

    depth: Depth
    volume_usd: float
    swaps: int
    mean_interval_s: float | None
    reason: str

    @property
    def price_is_evidence(self) -> bool:
        return self.depth is Depth.TRADEABLE


def assess(volume_usd: float, timestamps: list[float], window_seconds: float) -> Verdict:
    """Judge a pool from its volume and the spacing of its trades over one window.

    Both inputs matter and they fail differently: volume catches the pool that is small, spacing
    catches the pool that is large but asleep. A pool passing on one and failing the other is
    reported as THIN rather than averaged into a single score, because the two failures call for
    different responses -- one is "too small to trade", the other "this quote is old".
    """
    count = len(timestamps)
    if count < 2:
        return Verdict(Depth.DUST, volume_usd, count, None, "fewer than two trades in the window")

    ordered = sorted(timestamps)
    gaps = [b - a for a, b in itertools.pairwise(ordered) if b > a]
    interval = statistics.fmean(gaps) if gaps else window_seconds

    if volume_usd < DUST_USD:
        return Verdict(
            Depth.DUST, volume_usd, count, interval, f"turned over ${volume_usd:,.0f}, below ${DUST_USD:,.0f}"
        )
    if interval > STALE_SECONDS:
        return Verdict(
            Depth.THIN,
            volume_usd,
            count,
            interval,
            f"quote refreshes every {interval:.0f}s, so any gap to a live venue is mostly age",
        )
    if volume_usd < TRADEABLE_USD:
        return Verdict(Depth.THIN, volume_usd, count, interval, f"turned over ${volume_usd:,.0f}, thin but live")
    return Verdict(
        Depth.TRADEABLE,
        volume_usd,
        count,
        interval,
        f"${volume_usd:,.0f} across {count:,} trades, {interval:.1f}s apart",
    )
