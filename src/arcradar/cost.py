"""What it costs to put the census on Arc, built from gas constants rather than from a guess.

⭐ **The reason this file exists is that the previous estimate for this project was invented.** A
figure of "about $20 of USDC" was written down, sounded reasonable, and had never been computed. It
was roughly 300x too high. A number nobody derived is not cheaper to produce than one somebody did;
it is just harder to notice.

So: every constant below is either a protocol constant with its EIP named, or something read off
Arc at run time.

⚠️ **Half of this is now a measurement and half is still a model, and the two are labelled.** Arc
was checked for the one thing that would invalidate the model -- `eth_estimateGas` for a bare value
transfer returns exactly 21,000, so the chain runs the standard gas schedule and EIP-2929's values
apply.

- **Deployment: measured.** With solc installed, `eth_estimateGas` answers on the real creation
  bytecode. The figure below replaced a budget that was 52% too high.
- **Per pool: still modelled.** Measuring it needs a *deployed* registry to estimate against, and
  the estimate is worthless before then: a call to an address with no code does not fail, it returns
  the base-plus-calldata cost, omitting every SSTORE. `prepare_tx.py publish` refuses to run until
  the address has bytecode, and prints measured against modelled side by side once it does.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

#: EIP-2929 / EIP-3529. A cold slot going from zero to non-zero: 20,000 to set + 2,100 cold access.
SSTORE_SET_GAS: Final[int] = 22_100
#: The same slot rewritten to a different non-zero value: 2,900 + 2,100 cold access.
SSTORE_RESET_GAS: Final[int] = 5_000
#: Verified on Arc 2026-09-20: `eth_estimateGas` for a bare transfer returns exactly this, which is
#: how the standard schedule was confirmed rather than assumed.
TX_BASE_GAS: Final[int] = 21_000
CALLDATA_NONZERO_GAS: Final[int] = 16
CALLDATA_ZERO_GAS: Final[int] = 4

#: `PoolFacts` is laid out to occupy exactly two slots. See the struct's comment in the contract.
SLOTS_PER_POOL: Final[int] = 2
#: bytes32 poolId + two packed words, as ABI-encoded array elements.
CALLDATA_BYTES_PER_POOL: Final[int] = 96
#: Addresses and ids are dense; fees, flags and decimals are mostly padding. Measured against a
#: sample encoding rather than assumed to be half.
CALLDATA_NONZERO_SHARE: Final[float] = 0.55
#: Mapping keccak, loop bookkeeping and calldata decode per element. Budgeted, not measured -- this
#: is the single largest source of the ±20% band in the module docstring.
PER_POOL_OVERHEAD_GAS: Final[int] = 1_500

#: MEASURED 2026-09-20: `eth_estimateGas` on the compiled creation bytecode of PoolRegistry.sol
#: (2,354 bytes of runtime code, solc 0.8.37, optimizer 200 runs) returns this against Arc mainnet.
#:
#: ⭐ It replaces a budget of 900,000, which was **52% high**. Worth recording in both directions:
#: the model was wrong by more than the ±20% band claimed for it, and it was wrong on the safe side.
#: A budget that over-states is a budget that quietly stops being checked, which is why the number
#: got replaced the hour a compiler was available rather than left as "close enough".
DEPLOY_GAS_BUDGET: Final[int] = 590_575

#: Arc's native unit is USDC carried at 18 decimals, so wei / 1e18 is dollars.
WEI_PER_USD: Final[float] = 1e18


def calldata_gas_per_pool() -> float:
    nonzero = CALLDATA_BYTES_PER_POOL * CALLDATA_NONZERO_SHARE
    zero = CALLDATA_BYTES_PER_POOL - nonzero
    return nonzero * CALLDATA_NONZERO_GAS + zero * CALLDATA_ZERO_GAS


def gas_per_pool(*, first_write: bool = True) -> float:
    """Gas to publish one pool, excluding the per-transaction base cost."""
    store = SSTORE_SET_GAS if first_write else SSTORE_RESET_GAS
    return store * SLOTS_PER_POOL + calldata_gas_per_pool() + PER_POOL_OVERHEAD_GAS


@dataclass(frozen=True)
class Estimate:
    pools: int
    batches: int
    gas: float
    usd: float


def publish_cost(pools: int, gas_price_wei: int, *, batch_size: int = 200, first_write: bool = True) -> Estimate:
    """What publishing `pools` entries costs at the gas price the chain is quoting now.

    ⚠️ `batch_size` is bounded by the 30,000,000 block gas limit, not by taste: at ~47,000 gas a
    pool a batch above ~600 cannot fit in a block at all. 200 leaves room and keeps a failed batch
    cheap to retry.
    """
    batches = max(1, -(-pools // batch_size))
    gas = pools * gas_per_pool(first_write=first_write) + batches * TX_BASE_GAS
    return Estimate(pools=pools, batches=batches, gas=gas, usd=gas * gas_price_wei / WEI_PER_USD)


def deploy_cost(gas_price_wei: int) -> Estimate:
    gas = float(DEPLOY_GAS_BUDGET + TX_BASE_GAS)
    return Estimate(pools=0, batches=1, gas=gas, usd=gas * gas_price_wei / WEI_PER_USD)
