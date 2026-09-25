#!/usr/bin/env python
"""Census every Arc pool that traded recently, judge it, and write the JSON the page reads.

    python scripts/build_snapshot.py                 # last 6 hours
    python scripts/build_snapshot.py --hours 24 --top 800

Read-only. No wallet, no credentials, no transactions.

⭐ **ENTER FROM WHAT TRADED, NOT FROM WHAT EXISTS.** Arc has over 200,000 initialised v4 pools. A list of
pools is not a list of venues -- most of those were created for a token that traded once, at fee
tiers including 5%, 50%, 80% and 90%. So this ranks by the volume a pool actually did inside the
window and judges the top of that, rather than trying to describe a universe that is mostly noise.

📛 **A WINDOW THAT COULD NOT BE READ IS NOT AN EMPTY WINDOW**, so coverage is computed, written into
the snapshot and shown on the page. A run that lost coverage says so rather than printing a total
that cannot tell "nothing here" from "not allowed to look".

⭐ **But first make sure a lost window is really lost.** Two runs of an identical two-hour span here
reported 66.7% and 50.0% coverage, and neither figure was about rate limiting: Arc's node was
answering `-32602 query exceeds max results 2000, retry with the range 21851086-21851301` -- naming
the range it *would* serve -- and the scanner was filing that instruction under "refused". The
caveat shown to the reader was honestly worded and attached to the wrong diagnosis, which is worse
than no caveat, because it is unfalsifiable from the outside. `ArcRpc.fetch_logs` now follows the
node's own hint, and coverage stopped moving.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arcradar import abi, chain, keccak, sizing, tokens, v4
from arcradar.rpc import ArcRpc

# ⚠️ Line-buffered on purpose. This run takes minutes against a rate-limited RPC, and with the
# default block buffering its progress only appears at the end -- which makes a slow run and a hung
# run look exactly the same from outside. The first attempt at this census was killed at nine
# minutes for precisely that reason; it may well have been working.
sys.stdout.reconfigure(line_buffering=True)

#: Flag bits, kept byte-identical to PoolRegistry.sol. ⚠️ If these ever drift, the contract will
#: answer questions with the wrong bit rather than reverting, so they are asserted at the bottom.
FLAG_KNOWN = 1 << 0
FLAG_DUST = 1 << 1
FLAG_THIN = 1 << 2
FLAG_TRADEABLE = 1 << 3
FLAG_SYMBOL_IMPOSTOR = 1 << 4
FLAG_HAS_CEX_LEG = 1 << 5

#: Arc tokens with a centralised-exchange market, so a reference price exists for them. Deliberately
#: tiny: on a chain of this age it is the whole list, and that fact is itself a finding.
CEX_MARKETS: dict[str, str] = {
    "0x171a4217b86a807a64eb94757db6849fb4bdbaa0": "BTCUSDT",  # cirBTC
    "0x128cc466b61f542da60c70e3aa11c10e19b84edb": "ETHUSDT",  # WETH
}

TOKEN0_SELECTOR = "0x0dfe1681"
TOKEN1_SELECTOR = "0xd21220a7"
FEE_SELECTOR = "0xddca3f43"


def _price(sqrt_price_x96: int, decimals0: int, decimals1: int, usdc_side: int | None) -> float | None:
    """The pool's resting price in USDC, or None when neither side is USDC.

    ⚠️ `sqrt_price_to_price` returns token0 denominated in token1, so which side USDC sits on
    decides whether the answer needs inverting. Getting that backwards yields a reciprocal -- a
    perfectly ordinary-looking number, never an error, which is why it is written out rather than
    inlined.
    """
    if usdc_side is None or sqrt_price_x96 == 0:
        return None
    token0_in_token1 = abi.sqrt_price_to_price(sqrt_price_x96, decimals0, decimals1)
    if token0_in_token1 <= 0:
        return None
    # USDC is token1: the answer already is "USDC per token0".
    # USDC is token0: the answer is "token1 per USDC", so invert to price token1.
    return token0_in_token1 if usdc_side == 1 else 1.0 / token0_in_token1


def _v3_pairs(rpc: ArcRpc, pools: list[str]) -> dict[str, v4.Pair]:
    """v3 pools are real contracts, so their pair is three `eth_call`s rather than an event."""
    out: dict[str, v4.Pair] = {}
    for pool in pools:
        try:
            token0 = abi.address(rpc.eth_call(pool, TOKEN0_SELECTOR)[2:])
            token1 = abi.address(rpc.eth_call(pool, TOKEN1_SELECTOR)[2:])
            fee = int(rpc.eth_call(pool, FEE_SELECTOR) or "0x0", 16)
        except Exception:  # a pool that will not answer is left unresolved rather than guessed at
            continue
        out[pool] = v4.Pair(pool_id=pool, currency0=token0, currency1=token1, fee=fee)
    return out


def registry_census_block(rpc: ArcRpc) -> int | None:
    """The census block the deployed registry currently holds; None if it has never been published to."""
    word = int(rpc.eth_call(chain.POOL_REGISTRY, "0x" + keccak.selector("censusBlock()").hex()), 16)
    return word or None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hours", type=float, default=6.0)
    parser.add_argument("--top", type=int, default=500, help="how many pools to judge and publish")
    parser.add_argument("--cache", type=Path, default=Path("cache/v4_pairs.json"))
    parser.add_argument("--token-cache", type=Path, default=Path("cache/tokens.json"))
    parser.add_argument("--max-v3", type=int, default=60, help="busiest v3 pools to resolve pairs for")
    parser.add_argument("--out", type=Path, default=Path("docs/data/snapshot.json"))
    args = parser.parse_args()

    rpc = ArcRpc()
    head = rpc.block_number()
    span = int(args.hours * 3600 / chain.SECONDS_PER_BLOCK)
    start = max(chain.PUBLIC_MAINNET_BLOCK, head - span)
    print(f"Arc block {head:,}; reading {args.hours:g}h back to {start:,}")

    # Only used for a pre-sidecar cache: a point certainly BEFORE that cache was built (the first
    # one was scanned on 2026-09-20), pushed a further ~3h earlier because converting a wall-clock
    # time to a block through an average block time is an estimate. Too early costs a few windows;
    # too late loses pools forever.
    head_ts = rpc.block_timestamp(head)
    safe_ts = dt.datetime(2026, 9, 20, tzinfo=dt.UTC).timestamp()
    fallback = max(chain.PUBLIC_MAINNET_BLOCK, head - int((head_ts - safe_ts) / chain.SECONDS_PER_BLOCK) - 20_000)

    def map_tick(done: int, total: int, found: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"  pair map window {done:,}/{total:,}  {found:,} Initialize so far")

    pairs = v4.pair_map(rpc, head, args.cache, fallback_from=fallback, on_window=map_tick)
    print(f"pair map: {len(pairs):,} v4 pools ever initialised")

    def tick(done: int, total: int, found: int) -> None:
        if done % 3 == 0 or done == total:
            print(f"  window {done}/{total}  {found:,} logs")

    print("scanning v4 swaps...")
    v4_logs, v4_anchors = rpc.scan_logs_anchored(
        [chain.V4_SWAP_TOPIC], start, head, address=chain.UNISWAP_V4_POOL_MANAGER, on_window=tick
    )
    print("scanning v3 swaps...")
    v3_logs, v3_anchors = rpc.scan_logs_anchored([chain.V3_SWAP_TOPIC], start, head, on_window=tick)

    windows_attempted = 2 * (len(range(start, head + 1, chain.MAX_LOG_RANGE)))
    coverage = rpc.coverage(windows_attempted)
    print(f"\ncoverage {coverage:.2%}  ({len(rpc.lost_windows)} of {windows_attempted} windows lost)")

    swaps = v4.decode_swaps(v4_logs, pairs, v4_anchors)
    # ⭐ The reading that separates "the map is complete" from "the map is stale": swaps in pools the
    # map has never heard of. They cannot be priced, so without this line they would simply vanish
    # from the ranking and the census would look smaller, not broken.
    unmapped_pools = {s.pool_id for s in swaps if s.pool_id not in pairs}
    unmapped_swaps = sum(1 for s in swaps if s.pool_id not in pairs)
    print(f"v4 swaps in pools missing from the pair map: {unmapped_swaps:,} across {len(unmapped_pools):,} pools")

    # v3 lives in per-pool contracts, so its "pool id" is the emitting address, and its pair costs
    # three `eth_call`s rather than one map lookup. ⚠️ Against a rate-limited node that is the most
    # expensive step in the whole census, so only the busiest v3 pools are resolved -- and the
    # cutoff is reported below rather than left as an invisible horizon.
    v3_counts = Counter(str(log["address"]).lower() for log in v3_logs)
    v3_pool_ids = [pool for pool, _ in v3_counts.most_common(args.max_v3)]
    print(f"resolving {len(v3_pool_ids)} of {len(v3_counts)} v3 pools that traded (busiest first)")
    v3_pairs = _v3_pairs(rpc, v3_pool_ids)
    # ⚠️ Count the v4 map BEFORE merging v3 into it. A first version reported `len(pairs)` after
    # this line under the label "pools ever initialised" and printed 179,581 -- the v4 census plus
    # however many v3 pools happened to be resolved this run. The number moved with an unrelated
    # flag, which is the signature of a count that is measuring the code rather than the chain.
    v4_pool_count = len(pairs)
    pairs = {**pairs, **v3_pairs}
    for log in v3_logs:
        log["topics"] = [log["topics"][0], str(log["address"]).lower()]
    swaps += v4.decode_swaps(v3_logs, pairs, v3_anchors)
    print(f"{len(swaps):,} swaps across v3 + v4; {len(v3_pairs)} v3 pools resolved")

    volume: dict[str, float] = defaultdict(float)
    times: dict[str, list[float]] = defaultdict(list)
    last_sqrt: dict[str, int] = {}
    for swap in swaps:
        if swap.notional_usd is not None:
            volume[swap.pool_id] += swap.notional_usd
        times[swap.pool_id].append(swap.timestamp)
        last_sqrt[swap.pool_id] = swap.sqrt_price_x96

    ranked = sorted(volume.items(), key=lambda kv: kv[1], reverse=True)[: args.top]
    print(f"{len(volume):,} pools traded with a USDC leg; judging the top {len(ranked):,}\n")

    cache = tokens.TokenCache(rpc, args.token_cache)
    window_seconds = args.hours * 3600
    rows: list[dict[str, Any]] = []
    for index, (pool_id, usd) in enumerate(ranked):
        if index % 25 == 0:
            print(f"  resolving tokens {index}/{len(ranked)}")
            cache.save()
        pair = pairs.get(pool_id)
        if pair is None:
            continue
        token0 = cache.get(pair.currency0)
        token1 = cache.get(pair.currency1)
        verdict = sizing.assess(usd, times[pool_id], window_seconds)

        flags = FLAG_KNOWN
        flags |= {sizing.Depth.DUST: FLAG_DUST, sizing.Depth.THIN: FLAG_THIN}.get(verdict.depth, FLAG_TRADEABLE)
        if token0.impersonating or token1.impersonating:
            flags |= FLAG_SYMBOL_IMPOSTOR
        other = pair.currency1 if pair.usdc_side() == 0 else pair.currency0
        market = CEX_MARKETS.get(other.lower())
        if market:
            flags |= FLAG_HAS_CEX_LEG

        rows.append(
            {
                "pool_id": pool_id,
                "version": "v3" if pool_id in v3_pairs else "v4",
                "fee_bps": pair.fee / 100.0,
                "token0": {
                    "address": token0.address,
                    "symbol": token0.symbol,
                    "decimals": token0.decimals,
                    "impersonating": token0.impersonating,
                },
                "token1": {
                    "address": token1.address,
                    "symbol": token1.symbol,
                    "decimals": token1.decimals,
                    "impersonating": token1.impersonating,
                },
                "volume_usd": round(usd, 2),
                "swaps": verdict.swaps,
                "mean_interval_s": round(verdict.mean_interval_s, 1) if verdict.mean_interval_s else None,
                "depth": verdict.depth.value,
                "reason": verdict.reason,
                "price_usd": _price(last_sqrt.get(pool_id, 0), token0.decimals, token1.decimals, pair.usdc_side()),
                "cex_market": market,
                "flags": flags,
            }
        )

    cache.save()

    impostors = sorted(
        {
            t["address"]: {"address": t["address"], "claims": t["impersonating"], "decimals": t["decimals"]}
            for row in rows
            for t in (row["token0"], row["token1"])
            if t["impersonating"]
        }.values(),
        key=lambda t: str(t["address"]),
    )

    snapshot = {
        "generated_at": dt.datetime.now(dt.UTC).isoformat(timespec="seconds"),
        "chain_id": chain.CHAIN_ID,
        "rpc_url": chain.RPC_URL,
        "head_block": head,
        "window": {"from_block": start, "to_block": head, "hours": args.hours},
        "coverage": {
            "windows": windows_attempted,
            "lost": len(rpc.lost_windows),
            "share": round(coverage, 4),
            "unmapped_v4_swaps": unmapped_swaps,
            "unmapped_v4_pools": len(unmapped_pools),
        },
        "totals": {
            "pools_initialised": v4_pool_count,
            "v3_pools_resolved": len(v3_pairs),
            "pools_traded": len(volume),
            "pools_judged": len(rows),
            "tradeable": sum(1 for r in rows if r["depth"] == "tradeable"),
            "with_cex_leg": sum(1 for r in rows if r["cex_market"]),
            "impostor_pools": sum(1 for r in rows if r["flags"] & FLAG_SYMBOL_IMPOSTOR),
        },
        # ⚠️ `census_block` is what the REGISTRY says, read now, not this snapshot's head: the two
        # differ until someone publishes this snapshot, and the page shows both.
        "registry": {"address": chain.POOL_REGISTRY, "census_block": registry_census_block(rpc)},
        "impostors": impostors,
        "pools": rows,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(snapshot, indent=1))

    t = snapshot["totals"]
    print(f"{'judged':>22s} {t['pools_judged']:,}")
    print(f"{'tradeable':>22s} {t['tradeable']:,}")
    print(f"{'with a CEX leg':>22s} {t['with_cex_leg']:,}")
    print(f"{'pools with an impostor':>22s} {t['impostor_pools']:,}  ({len(impostors)} distinct tokens)")
    for row in impostors:
        print(f"      {row['address']}  claims {row['claims']!r} at {row['decimals']} decimals")
    print(f"\nwrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
