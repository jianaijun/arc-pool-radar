#!/usr/bin/env python
"""Build the unsigned transactions that put this registry on Arc. It does not sign or send them.

    python scripts/prepare_tx.py deploy  --from 0xYourAddress
    python scripts/prepare_tx.py publish --from 0xYourAddress --registry 0xDeployedAddress

📛 **THIS SCRIPT HAS NO KEY, DOES NO SIGNING, AND SENDS NOTHING.** It compiles the contract, asks
Arc what the call would cost, writes a complete unsigned transaction to `out/`, and stops. The
operator signs it in their own wallet and broadcasts it themselves. There is no `--yes` flag and no
private-key argument, because the safe version of those does not exist.

⭐ It also replaces a model with a reading. `cost.py` had to BUDGET the deployment and per-pool gas
because measuring them needs a compiled contract; with solc installed, `eth_estimateGas` answers
for real. The script prints both numbers side by side so the model's error is visible rather than
merely asserted to be small.

⚠️ `--from` must be the address that will sign. Gas estimation runs against that account's state,
and the nonce written into the transaction is that account's -- a transaction prepared for one
address and signed by another is invalid, which at least fails loudly.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arcradar import chain, cost, keccak, solidity
from arcradar.rpc import ArcRpc, RpcError

sys.stdout.reconfigure(line_buffering=True)

ROOT = Path(__file__).resolve().parent.parent
CONTRACT = ROOT / "contracts" / "PoolRegistry.sol"

#: How many pools go into one `publish` call. Bounded by the 30M block gas limit, not by taste: at
#: roughly 47,000 gas a pool, a batch much above 500 cannot fit in a block at all.
DEFAULT_BATCH = 150
#: Headroom over the estimate. A batch that runs out of gas still burns the gas it used.
GAS_MARGIN = 1.25


def _fees(rpc: ArcRpc) -> tuple[int, int]:
    """(maxFeePerGas, maxPriorityFeePerGas) from the chain's own current answers."""
    gas_price = rpc.gas_price()
    try:
        tip = int(str(rpc.call("eth_maxPriorityFeePerGas", [])), 16)
    except Exception:  # a node without the method is not a reason to guess a tip silently
        tip = gas_price // 10
    return gas_price * 2, tip


def _write(path: Path, tx: dict[str, Any], note: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"note": note, "prepared_at": dt.datetime.now(dt.UTC).isoformat(), "tx": tx}, indent=1))
    print(f"  -> {path}")


def _estimate(rpc: ArcRpc, tx: dict[str, Any], label: str) -> int:
    """Ask the chain what this call costs, and translate a revert into a sentence.

    ⚠️ A revert here is nearly always one of two things, and both deserve better than a traceback:
    the address has code but is not this registry, or it is this registry and `msg.sender` is not
    its publisher. `PoolRegistry` reverts with `NotPublisher()` in the second case, which is a
    custom error and therefore arrives as four opaque bytes rather than a message.
    """
    probe = {k: v for k, v in tx.items() if k in ("from", "to", "data", "value")}
    try:
        gas = rpc.estimate_gas(probe)
    except RpcError as exc:
        raise SystemExit(
            f"\nArc refused to estimate {label}: {exc}\n"
            f"  - is {tx.get('to')} really this PoolRegistry?\n"
            f"  - is {tx.get('from')} the address that deployed it? only the publisher may publish."
        ) from exc
    print(f"  {label:<34s} {gas:>12,} gas")
    return gas


def _usd(gas: float, max_fee: int) -> float:
    return gas * max_fee / cost.WEI_PER_USD


def cmd_deploy(args: argparse.Namespace, rpc: ArcRpc, solc: Path) -> int:
    built = solidity.compile_contract(CONTRACT, "PoolRegistry", solc)
    print(f"compiled PoolRegistry: {built.deployed_size:,} bytes of runtime code\n")

    max_fee, tip = _fees(rpc)
    nonce = int(str(rpc.call("eth_getTransactionCount", [args.sender, "pending"])), 16)
    tx: dict[str, Any] = {
        "type": "0x2",
        "chainId": hex(chain.CHAIN_ID),
        "from": args.sender,
        "to": None,
        "nonce": hex(nonce),
        "value": "0x0",
        "data": built.bytecode,
        "maxFeePerGas": hex(max_fee),
        "maxPriorityFeePerGas": hex(tip),
    }
    measured = _estimate(rpc, tx, "deployment, measured")
    print(
        f"  {'deployment, cost.py model':<34s} {cost.DEPLOY_GAS_BUDGET:>12,} gas"
        f"   (model is {cost.DEPLOY_GAS_BUDGET / measured - 1:+.0%})"
    )
    tx["gas"] = hex(int(measured * GAS_MARGIN))

    print(f"\n  at {max_fee / 1e9:.2f} gwei this costs about ${_usd(measured, max_fee):.4f}\n")
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "PoolRegistry.abi.json").write_text(json.dumps(built.abi, indent=1))
    _write(ROOT / "out" / "deploy_tx.json", tx, "unsigned deployment of PoolRegistry; sign in your own wallet")
    print("\nNEXT, AND ONLY YOU CAN DO THESE:")
    print("  1. bridge a couple of USDC to Arc via Circle's official CCTP")
    print("  2. sign out/deploy_tx.json in your wallet and broadcast it")
    print("  3. re-run with `publish --registry <the deployed address>`")
    return 0


def cmd_publish(args: argparse.Namespace, rpc: ArcRpc, solc: Path) -> int:
    snapshot = json.loads(args.snapshot.read_text())
    pools = [p for p in snapshot["pools"] if p["volume_usd"] >= args.min_usd]
    print(f"{len(snapshot['pools']):,} pools in the snapshot; {len(pools):,} above ${args.min_usd:,.0f}\n")
    if not pools:
        print("nothing to publish at that threshold.")
        return 1

    # 📛 **Check the registry EXISTS before trusting a single gas number below.** `eth_estimateGas`
    # for a call to an address with no code does not fail -- there is nothing to revert, so it
    # happily returns the base-plus-calldata cost, a number in the right order of magnitude that is
    # missing every SSTORE the real call would do. It would read as "publishing is cheap". ⇒ The
    # one reading that separates the cases is whether the address has bytecode.
    code = str(rpc.call("eth_getCode", [args.registry, "latest"]))
    if len(code) <= 2:
        print(f"{args.registry} has no bytecode on Arc.")
        print("Deploy the registry first; estimating against an empty address returns a plausible")
        print("number that omits every storage write, which is worse than no number at all.")
        return 1

    max_fee, tip = _fees(rpc)
    nonce = int(str(rpc.call("eth_getTransactionCount", [args.sender, "pending"])), 16)
    batches = [pools[i : i + args.batch] for i in range(0, len(pools), args.batch)]
    total_gas = 0

    for index, batch in enumerate(batches):
        ids = [p["pool_id"] for p in batch]
        facts = [
            (
                p["token0"]["address"],
                round(p["fee_bps"] * 100),
                p["flags"],
                p["token0"]["decimals"],
                p["token1"]["decimals"],
                p["token1"]["address"],
                # ⚠️ uint96 clamp. A volume larger than the field would WRAP silently on chain.
                min(int(p["volume_usd"]), (1 << 96) - 1),
            )
            for p in batch
        ]
        data = solidity.encode_publish(ids, facts, snapshot["head_block"], snapshot["totals"]["pools_traded"])
        tx: dict[str, Any] = {
            "type": "0x2",
            "chainId": hex(chain.CHAIN_ID),
            "from": args.sender,
            "to": args.registry,
            "nonce": hex(nonce + index),
            "value": "0x0",
            "data": data,
            "maxFeePerGas": hex(max_fee),
            "maxPriorityFeePerGas": hex(tip),
        }
        gas = _estimate(rpc, tx, f"batch {index + 1}/{len(batches)} ({len(batch)} pools)")
        tx["gas"] = hex(int(gas * GAS_MARGIN))
        total_gas += gas
        _write(ROOT / "out" / f"publish_{index + 1:02d}_tx.json", tx, f"unsigned publish of {len(batch)} pools")

    per_pool = total_gas / len(pools)
    print(f"\n  {'per pool, measured':<34s} {per_pool:>12,.0f} gas")
    print(
        f"  {'per pool, cost.py model':<34s} {cost.gas_per_pool():>12,.0f} gas"
        f"   (model is {cost.gas_per_pool() / per_pool - 1:+.0%})"
    )
    print(f"\n  {len(batches)} transactions, {total_gas:,} gas, about ${_usd(total_gas, max_fee):.4f} total")
    print("\nSign each out/publish_*_tx.json IN ORDER -- the nonces are sequential.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    # `--from` is declared on a shared parent so it may be written after the subcommand, which is
    # where everyone types it.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--from", dest="sender", required=True, help="the address that will sign")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("deploy", parents=[common])
    publish = sub.add_parser("publish", parents=[common])
    publish.add_argument("--registry", required=True, help="the deployed PoolRegistry address")
    publish.add_argument("--snapshot", type=Path, default=ROOT / "docs" / "data" / "snapshot.json")
    publish.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    publish.add_argument("--min-usd", type=float, default=5_000.0)
    args = parser.parse_args()

    # ⭐ Before any calldata is built. A wrong hash here would produce four plausible bytes.
    keccak.selftest()

    solc_module = __import__("install_solc")
    solc = solc_module.solc_binary()
    rpc = ArcRpc()
    print(f"Arc block {rpc.block_number():,}; signer {args.sender}\n")

    if args.command == "deploy":
        return cmd_deploy(args, rpc, solc)
    return cmd_publish(args, rpc, solc)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
