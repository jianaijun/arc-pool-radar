"""Arc mainnet constants, each with the way it was checked rather than the place it was copied from.

Arc is Circle's L1. It went to public mainnet on 2026-09-16, it charges gas in USDC, and its native
USDC lives at a precompile address rather than at a deployed ERC-20. Every address below was read
back off the chain: the factory by making `Quoter.factory()` and `Router.factory()` point at each
other, the token addresses by calling `token0()`/`token1()` on a pool that trades them, the pool's
own birth block by binary search on `eth_getCode`.

None of it was taken from a block explorer or a docs page, and that is deliberate. Writing a token
address from memory is the single cheapest way to produce a number instead of an error: the wrong
address answers `eth_call` with zeroes, the decoder divides by the wrong power of ten, and what
comes out is a plausible price for a pair that does not exist. It happened once while this code was
being written; `token0()` is what caught it.
"""

from typing import Final

RPC_URL: Final[str] = "https://rpc.mainnet.arc.io"
CHAIN_ID: Final[int] = 5042

#: Genesis. Note the gap to the line below: the chain is months older than its public mainnet, and
#: conflating the two makes a four-day-old pool look like it has a four-month history.
GENESIS_DATE: Final[str] = "2026-05-15"
#: The block public mainnet opened, 2026-09-16 01:09:33 UTC.
#:
#: 📛 **This is NOT where v4 pools begin**, and an earlier version of this comment said it was. That
#: was an inference, never a measurement, and it cost 16% of live v4 swap flow: pools initialised
#: during the private-mainnet months still trade, and a pair map scanned from here cannot see them.
#: Use `UNISWAP_V4_DEPLOY_BLOCK` as the floor for anything about v4 pools.
PUBLIC_MAINNET_BLOCK: Final[int] = 21_076_890

#: MEASURED 2026-09-24 by binary search on `eth_getCode` at the PoolManager: first block with code.
#: 2026-05-27 00:02 UTC -- nineteen million blocks before public mainnet.
UNISWAP_V4_DEPLOY_BLOCK: Final[int] = 1_948_056

#: Measured across the chain's whole history; it held between 0.5044 and 0.5079 the entire time.
#: Used only to interpolate a timestamp inside one `eth_getLogs` window, never across the chain.
SECONDS_PER_BLOCK: Final[float] = 0.5062

#: The canonical USDC. ⚠️ A PRECOMPILE, not a deployed contract -- `eth_getCode` returns empty for
#: it, so any "is this a real token" check written as a bytecode check rejects the one token on the
#: chain that is certainly real. This address is the identity test; `symbol()` is not.
USDC: Final[str] = "0x3600000000000000000000000000000000000000"
USDC_DECIMALS: Final[int] = 6

UNISWAP_V3_FACTORY: Final[str] = "0xf0db7b58379503491d857dB50AC9ece64c653918"
UNISWAP_V3_QUOTER: Final[str] = "0x7DfD4F31be6814D2906BDE155c3e1B146EAc1468"
UNISWAP_V4_POOL_MANAGER: Final[str] = "0x8366a39CC670B4001A1121B8F6A443A643e40951"

#: keccak("Swap(address,address,int256,int256,uint160,uint128,int24)") -- Uniswap v3's.
V3_SWAP_TOPIC: Final[str] = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"

#: v4's own Swap and Initialize.
#:
#: These two were identified BY SHAPE ON CHAIN -- `Swap` is the topic carrying 2 indexed and 6 data
#: words, `Initialize` the one carrying 3 and 5 -- and not by hashing a signature string copied from
#: a header file. v4 is a singleton: every pool emits from the one `PoolManager`, so a wrong topic
#: does not return nothing, it returns a different event's logs, which decode into numbers.
V4_SWAP_TOPIC: Final[str] = "0x40e9cecb9f5f1f1c5b9c97dec2917b7ee92e57ba5563708daca94dd84ad7112f"
V4_INITIALIZE_TOPIC: Final[str] = "0xdd466e674ea557f56295e2d0218a125ea4b4f0f6f3307b95f85e6110838d6438"

#: The node refuses a wider range, with or without an address filter (verified 2026-09-20).
MAX_LOG_RANGE: Final[int] = 5_000
