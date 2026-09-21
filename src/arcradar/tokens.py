"""Token metadata, and the reason this package exists: on Arc, a symbol string is not an identity.

A four-day-old chain has no established token list, so the only name most tooling has for a token
is whatever string the token itself returns from `symbol()`. That string is attacker-controlled. It
costs nothing to deploy a token that answers "USDC".

This is not hypothetical here. A census of Uniswap v4 pools on Arc turned up two separate pools in
which BOTH sides call themselves `USDC`, one of them inside the top twenty by volume. Neither side
was the real one, which lives at a precompile address and is the only thing entitled to the name.

⭐ So the rule this module enforces is: **identity is the address; the symbol is a label the token
chose for itself.** `verify()` returns both, and marks the cases where they disagree.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, cast

from . import abi, chain
from .rpc import ArcRpc, RpcError

SYMBOL_SELECTOR: Final[str] = "0x95d89b41"
DECIMALS_SELECTOR: Final[str] = "0x313ce567"
NAME_SELECTOR: Final[str] = "0x06fdde03"

#: Names that a token has to earn by living at the right address. Anything else claiming one of
#: these is flagged. Kept deliberately short: this is a list of things worth impersonating.
RESERVED: Final[dict[str, str]] = {"usdc": chain.USDC}


@dataclass(frozen=True)
class Token:
    """What a token says about itself, beside what its address says about it."""

    address: str
    symbol: str
    decimals: int
    impersonating: str | None = None
    """Set when `symbol` is a reserved name and `address` is not the address entitled to it."""

    @property
    def is_canonical_usdc(self) -> bool:
        return self.address.lower() == chain.USDC.lower()

    @property
    def display(self) -> str:
        """⭐ The symbol is never shown bare once it is suspect -- see the module docstring.

        A UI that prints `USDC` for an impostor has done the attacker's work for them, so the
        rendering rule lives here, next to the check, rather than in whatever page happens to
        display it. A caller cannot forget to apply it, because there is nothing else to print.
        """
        if self.impersonating:
            return f"{self.symbol}⚠ (NOT {self.impersonating}; {self.address[:10]}...)"
        return self.symbol


def fetch(rpc: ArcRpc, token: str) -> Token:
    """Read a token's own account of itself, then check it against its address.

    ⚠️ The canonical USDC is a PRECOMPILE: `eth_getCode` is empty for it. Any liveness check written
    as "does this address have bytecode" rejects the one token on this chain that is certainly real,
    so it is short-circuited here rather than guarded against downstream.
    """
    lowered = token.lower()
    if lowered == chain.USDC.lower():
        return Token(address=lowered, symbol="USDC", decimals=chain.USDC_DECIMALS)

    try:
        symbol = abi.decode_string(rpc.eth_call(token, SYMBOL_SELECTOR))
    except RpcError:
        symbol = ""
    try:
        decimals = int(rpc.eth_call(token, DECIMALS_SELECTOR) or "0x0", 16)
    except (RpcError, ValueError):
        decimals = 18

    owner = RESERVED.get(symbol.strip().lower())
    impersonating = None
    if owner is not None and owner.lower() != lowered:
        impersonating = symbol.strip()
    return Token(address=lowered, symbol=symbol, decimals=decimals, impersonating=impersonating)


class TokenCache:
    """`fetch` memoised in memory and, optionally, on disk.

    ⭐ **The disk half is not an optimisation, it is what makes the census runnable.** Arc's public
    RPC rate-limits, every token costs two `eth_call`s, and a census asks about hundreds of them; a
    cold run spends most of its wall clock in backoff. Token symbols and decimals are immutable, so
    caching them is free of the usual staleness objection -- unlike a pool's volume, which is why
    only this is cached and the trade data is not.
    """

    def __init__(self, rpc: ArcRpc, path: Path | None = None) -> None:
        self._rpc = rpc
        self._path = path
        self._seen: dict[str, Token] = {}
        self._dirty = False
        if path is not None and path.exists():
            stored = cast("dict[str, dict[str, Any]]", json.loads(path.read_text()))
            self._seen = {
                key: Token(
                    address=key,
                    symbol=str(v["symbol"]),
                    decimals=int(v["decimals"]),
                    impersonating=v.get("impersonating"),
                )
                for key, v in stored.items()
            }

    def get(self, token: str) -> Token:
        lowered = token.lower()
        if lowered not in self._seen:
            self._seen[lowered] = fetch(self._rpc, lowered)
            self._dirty = True
        return self._seen[lowered]

    def save(self) -> None:
        """⚠️ Call this even when a run fails part way -- a half-warmed cache is the whole point."""
        if self._path is None or not self._dirty:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(
                {
                    k: {"symbol": t.symbol, "decimals": t.decimals, "impersonating": t.impersonating}
                    for k, t in self._seen.items()
                }
            )
        )
        self._dirty = False
