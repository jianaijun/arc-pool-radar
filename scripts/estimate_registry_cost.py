#!/usr/bin/env python
"""What funding this project on Arc actually costs, at the gas price Arc is quoting right now.

    python scripts/estimate_registry_cost.py

Read-only. It sends no transaction, holds no key, and needs no funded account: the only thing it
asks the chain for is the current gas price and a sanity check that Arc runs the standard gas
schedule.

⭐ Run this BEFORE bridging any USDC. The point is to replace a figure somebody felt was about right
with one derived from constants -- the previous estimate for this work was "about $20" and was never
computed; it was roughly 300x too high.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arcradar import cost
from arcradar.rpc import ArcRpc

#: The census sizes worth pricing: every initialised v4 pool, every pool that traded in a day, and
#: the size-gated subset this project actually intends to publish.
SCENARIOS: list[tuple[str, int]] = [
    ("size-gated subset (what we publish)", 500),
    ("every pool that traded in 6h", 2_013),
    ("every pool ever initialised", 179_521),
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=200)
    args = parser.parse_args()

    rpc = ArcRpc()
    gas_price = rpc.gas_price()

    # ⭐ The one check that decides whether the model below is allowed to exist. A bare transfer
    # costing exactly 21,000 means Arc has not altered the gas schedule, so EIP-2929's SSTORE
    # constants apply. If this ever prints anything else, every number underneath is void.
    probe = rpc.estimate_gas(
        {
            "from": "0x000000000000000000000000000000000000dEaD",
            "to": "0x00000000000000000000000000000000000000Ff",
            "value": "0x0",
        }
    )
    standard = probe == cost.TX_BASE_GAS

    print(f"chain          Arc mainnet, block {rpc.block_number():,}")
    print(f"gas price      {gas_price / 1e9:.3f} gwei  =  ${gas_price / cost.WEI_PER_USD * 1e6:.4f} per million gas")
    print(f"gas schedule   bare transfer estimates {probe:,} gas -> {'STANDARD' if standard else 'NON-STANDARD'}")
    if not standard:
        print("\nthe gas schedule is not standard, so the SSTORE constants below do not apply. stopping.")
        return 1

    deploy = cost.deploy_cost(gas_price)
    print(f"\ndeploy PoolRegistry.sol        {deploy.gas:>12,.0f} gas   ${deploy.usd:>8.4f}")
    print(
        f"one pool, first write          {cost.gas_per_pool():>12,.0f} gas   "
        f"${cost.gas_per_pool() * gas_price / cost.WEI_PER_USD:>8.4f}"
    )
    print(
        f"one pool, refresh              {cost.gas_per_pool(first_write=False):>12,.0f} gas   "
        f"${cost.gas_per_pool(first_write=False) * gas_price / cost.WEI_PER_USD:>8.4f}"
    )

    print(f"\n{'census':>38s} {'batches':>8s} {'gas':>14s} {'first write':>13s} {'each refresh':>13s}")
    for label, pools in SCENARIOS:
        fresh = cost.publish_cost(pools, gas_price, batch_size=args.batch_size)
        again = cost.publish_cost(pools, gas_price, batch_size=args.batch_size, first_write=False)
        print(
            f"{label:>38s} {fresh.batches:>8,} {fresh.gas:>14,.0f} {'$' + f'{fresh.usd:,.2f}':>13s} "
            f"{'$' + f'{again.usd:,.2f}':>13s}"
        )

    gated = cost.publish_cost(SCENARIOS[0][1], gas_price, batch_size=args.batch_size)
    total = deploy.usd + gated.usd
    print(
        f"\ndeploy + publish the gated subset = ${total:,.2f}."
        f"\nbridge ${max(2.0, total * 4):,.0f} of USDC and the whole project is funded several times over."
    )
    print(
        "\nNOTE: the DEPLOY line is measured -- eth_estimateGas on the compiled creation bytecode."
        "\nIt replaced a budget that was 52% too high. The PER-POOL lines are still a model, because"
        "\nestimating them needs a DEPLOYED registry: a call to an address with no code does not"
        "\nfail, it returns base-plus-calldata and omits every storage write, which would read as"
        "\n'publishing is free'. `prepare_tx.py publish` refuses to run until the address has"
        "\nbytecode, and prints measured beside modelled once it does."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
