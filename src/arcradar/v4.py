"""Uniswap v4 on Arc: resolving which pool is which, and what each one actually traded.

v4 is a singleton. Every pool lives inside one `PoolManager` and is identified by a `PoolId`, so
there is no per-pool contract to call `token0()` on. The only way to learn a pool's pair is to have
seen its `Initialize` event. That makes the pair map a piece of infrastructure rather than a lookup,
and it is the reason this package builds and caches one.

📛 **The map has to cover the whole chain, not a convenient prefix of it.** A first attempt scanned
60,000 blocks after the first pool was created, which resolved 5 of the 30 busiest pools. The
selection step then ANDed three conditions together and printed one line: no pool qualified. ⇒ A
limit of the scan is indistinguishable, in the output, from a fact about the venue. Scanning all
179,521 pools and caching the result fixed it; the top twenty then resolved twenty for twenty.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from . import abi, chain
from .rpc import ArcRpc


@dataclass(frozen=True)
class Pair:
    """A v4 pool's immutable key, as its `Initialize` event declared it."""

    pool_id: str
    currency0: str
    currency1: str
    fee: int

    @property
    def fee_percent(self) -> float:
        return self.fee / 10_000.0

    def usdc_side(self) -> int | None:
        """Which side is canonical USDC: 0, 1, or None.

        📛 **Read it; never assume it.** A draft of this assumed USDC was token1 at 6 decimals,
        which is the layout the v3 pools happen to have. In v4 the currencies are sorted by address,
        and USDC's precompile address starts with 0x36, so it is usually token0 -- with an 18-decimal
        token opposite it. Dividing by the wrong side's decimals made every volume 10**12 too large.
        """
        canonical = chain.USDC.lower()
        if self.currency0.lower() == canonical:
            return 0
        if self.currency1.lower() == canonical:
            return 1
        return None


@dataclass(frozen=True)
class Swap:
    """One v4 swap, already resolved to its pair and to a USD notional where that is possible."""

    pool_id: str
    block: int
    timestamp: float
    amount0: int
    amount1: int
    sqrt_price_x96: int
    notional_usd: float | None


def _meta_path(cache: Path) -> Path:
    return cache.with_suffix(".meta.json")


def pair_map(
    rpc: ArcRpc,
    head: int,
    cache: Path,
    *,
    fallback_from: int | None = None,
    on_window: Any = None,
) -> dict[str, Pair]:
    """Every v4 pool ever initialised, keyed by `PoolId`, cached to disk and EXTENDED on each call.

    Pools are only ever added, so an old cache is incomplete rather than wrong -- and incomplete is
    the dangerous kind here: a swap in a pool the map has never seen has no pair, so it has no USD
    notional, so it drops out of the ranking with no error at all. ⇒ Every call scans `Initialize`
    from where the cache stopped up to `head`, and records where that is in a sidecar file.

    📛 **Where the cache stopped is RECORDED, never inferred from the file's mtime.** The first
    cache here was copied from another directory, so its mtime was the copy time -- seven hours
    after the scan that produced it. Resuming from the mtime would have skipped every pool created
    in those seven hours, permanently, and nothing downstream could have noticed.

    A cache with no sidecar (one written before sidecars existed) resumes from `fallback_from`,
    which the caller must choose to be safely EARLY. Re-scanning an overlap costs a few windows;
    the entries are keyed by pool id, so an overlap cannot double-count.

    📛 **The sidecar records BOTH ends, and the floor is where the PoolManager was deployed.** Every
    cache before 2026-09-24 started at public mainnet on the belief that no v4 pool predates it;
    that belief was never measured and was wrong by nineteen million blocks. A sidecar whose
    `covered_from` is later than `UNISWAP_V4_DEPLOY_BLOCK` is therefore back-filled, so the gap
    closes itself instead of depending on someone remembering it exists.
    """
    stored: dict[str, list[Any]] = {}
    # Every cache written before sidecars recorded a start began at public mainnet.
    covered_from, covered_to = chain.PUBLIC_MAINNET_BLOCK, chain.UNISWAP_V4_DEPLOY_BLOCK - 1
    if cache.exists():
        stored = cast("dict[str, list[Any]]", json.loads(cache.read_text()))
        meta = _meta_path(cache)
        if meta.exists():
            recorded = json.loads(meta.read_text())
            covered_from = int(recorded.get("covered_from", chain.PUBLIC_MAINNET_BLOCK))
            covered_to = int(recorded["covered_to"])
        elif fallback_from is not None:
            covered_to = fallback_from - 1
        else:
            raise RuntimeError(
                f"{cache} has no sidecar recording where it stopped; pass fallback_from, chosen "
                "safely before the cache was built, rather than guessing from the file's mtime"
            )
    else:
        covered_from = chain.UNISWAP_V4_DEPLOY_BLOCK

    out: dict[str, Pair] = {
        key: Pair(pool_id=key, currency0=str(v[0]), currency1=str(v[1]), fee=int(v[2])) for key, v in stored.items()
    }
    ranges = []
    if covered_from > chain.UNISWAP_V4_DEPLOY_BLOCK:
        ranges.append((chain.UNISWAP_V4_DEPLOY_BLOCK, covered_from - 1))
    ranges.append((max(covered_to + 1, chain.UNISWAP_V4_DEPLOY_BLOCK), head))
    ranges = [(lo, hi) for lo, hi in ranges if lo <= hi]
    if not ranges:
        return out

    lost_before = len(rpc.lost_windows)
    logs = []
    for lo, hi in ranges:
        # ⚠️ `on_window` is not optional in spirit: the pre-mainnet back-fill is ~3,800 windows at
        # roughly a second each, and without progress a slow scan and a hung one look identical.
        # The first back-fill ran for over half an hour saying nothing but "alive".
        logs += rpc.scan_logs(
            [chain.V4_INITIALIZE_TOPIC], lo, hi, address=chain.UNISWAP_V4_POOL_MANAGER, on_window=on_window
        )
    # ⚠️ A window lost here is a hole in the map, so the sidecar must not claim it was covered.
    # Recording `head` regardless would make the next run skip the hole forever.
    lost = len(rpc.lost_windows) - lost_before
    if lost:
        raise RuntimeError(f"pair map extension lost {lost} windows; not recording coverage")
    for log in logs:
        topics = cast("list[str]", log["topics"])
        if len(topics) < 4:
            continue
        pool_id = topics[1]
        out[pool_id] = Pair(
            pool_id=pool_id,
            currency0=abi.address(topics[2]),
            currency1=abi.address(topics[3]),
            fee=int(abi.word(str(log["data"]), 0), 16),
        )
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps({k: [v.currency0, v.currency1, v.fee] for k, v in out.items()}))
    _meta_path(cache).write_text(json.dumps({"covered_from": chain.UNISWAP_V4_DEPLOY_BLOCK, "covered_to": head}))
    return out


def decode_swaps(logs: list[dict[str, Any]], pairs: dict[str, Pair], anchors: dict[int, int]) -> list[Swap]:
    """Turn raw `Swap` logs into swaps, pricing the USDC leg where there is one.

    `anchors` maps a window's first block to its timestamp; times inside a window are interpolated
    from its own anchor rather than from a single global one. Arc's block time is stable to within
    0.4% so the drift inside one 42-minute window is under a second, while across the full history
    it would be tens of minutes.
    """
    out: list[Swap] = []
    for log in logs:
        topics = cast("list[str]", log["topics"])
        if len(topics) < 2:
            continue
        pool_id = topics[1]
        data = str(log["data"])
        # ⭐ Three words, not six. v4's Swap carries six data words and v3's carries five, but both
        # put amount0, amount1 and sqrtPriceX96 in the first three -- which is why this one decoder
        # serves both generations. Requiring six here silently dropped every v3 log: no error, no
        # warning, just a chain that appeared to have no v3 activity at all.
        if len(data) < 2 + abi.WORD * 3:
            continue
        amount0 = abi.signed(abi.word(data, 0))
        amount1 = abi.signed(abi.word(data, 1))
        sqrt_price = int(abi.word(data, 2), 16)
        block = int(str(log["blockNumber"]), 16)

        anchor_block = max((b for b in anchors if b <= block), default=None)
        timestamp = 0.0
        if anchor_block is not None:
            timestamp = anchors[anchor_block] + (block - anchor_block) * chain.SECONDS_PER_BLOCK

        notional: float | None = None
        pair = pairs.get(pool_id)
        if pair is not None:
            side = pair.usdc_side()
            if side == 0:
                notional = abs(amount0) / 10**chain.USDC_DECIMALS
            elif side == 1:
                notional = abs(amount1) / 10**chain.USDC_DECIMALS

        out.append(
            Swap(
                pool_id=pool_id,
                block=block,
                timestamp=timestamp,
                amount0=amount0,
                amount1=amount1,
                sqrt_price_x96=sqrt_price,
                notional_usd=notional,
            )
        )
    return out
