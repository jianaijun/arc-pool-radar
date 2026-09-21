"""The small amount of ABI decoding this package needs, written once so it is only debugged once.

Three of the bugs found while building this were decoding bugs, they were the same bug three times,
and each one was written fresh in a new file while a correct version of it sat in a neighbouring
one. None of them raised. Each produced a number. That is the entire argument for this module
existing: the failure mode of ABI decoding is a plausible quantity, so the decoder has to be a
single shared thing that can be tested in one place.
"""

from __future__ import annotations

from typing import Final

WORD: Final[int] = 64  # one 32-byte ABI word as hex characters


def word(data: str, index: int) -> str:
    """The n-th 32-byte word of a `0x`-prefixed data blob."""
    body = data[2:] if data.startswith("0x") else data
    return body[index * WORD : (index + 1) * WORD]


def signed(hex_word: str) -> int:
    """One 32-byte word as a two's-complement signed integer.

    📛 The rule is the WORD's width, never the Solidity type's. Uniswap v4 declares its swap amounts
    `int128`, and the obvious reading of that -- test the sign bit at 2**127, subtract 2**128 -- is
    wrong, because the ABI sign-extends a negative `int128` across the whole 32-byte slot. Under the
    wrong rule every negative amount came back around 10**77, so every pool's volume was
    astronomical.

    ⭐ The tell was not the magnitude. Numbers that are merely huge look like numbers. The tell was
    that thresholds of $1k, $100k, $1M and $10M all selected exactly 1,713 pools -- four different
    questions with one answer is impossible, and it points at the decoder rather than at the market.
    """
    value = int(hex_word, 16)
    return value - (1 << 256) if value >= (1 << 255) else value


def address(hex_word: str) -> str:
    """The low 20 bytes of a word, as a lowercase address."""
    return "0x" + hex_word[-40:].lower()


def sqrt_price_to_price(sqrt_price_x96: int, decimals0: int, decimals1: int) -> float:
    """Uniswap's `sqrtPriceX96` as a human price of token0 denominated in token1.

    The encoded quantity is sqrt(token1/token0) in RAW units, scaled by 2**96. Squaring gives raw
    token1 per raw token0; the decimal shift is what makes it a price.

    ⚠️ Getting the direction of that shift wrong yields a number off by a power of ten, not an
    error, which is why every caller of this is expected to run the result past a sanity band.
    """
    raw = (sqrt_price_x96 / (1 << 96)) ** 2
    return raw * 10 ** (decimals0 - decimals1)


def selector_call(selector: str, *words: str) -> str:
    """Calldata for a view function: a 4-byte selector followed by pre-padded 32-byte words."""
    return selector + "".join(w.rjust(WORD, "0") for w in words)


def decode_string(data: str) -> str:
    """A returned `string`, tolerating the contracts that answer with a `bytes32` instead.

    ⚠️ Both shapes occur on Arc and neither is wrong. A `bytes32` symbol is a single word of padded
    ASCII; a `string` is an offset, a length and then the bytes. Reading a `bytes32` as a `string`
    gives an absurd offset and an empty answer -- and an empty symbol is easy to mistake for a token
    that declines to name itself, which is a very different and much more interesting fact.
    """
    body = data[2:] if data.startswith("0x") else data
    if not body:
        return ""
    if len(body) == WORD:
        return bytes.fromhex(body).rstrip(b"\x00").decode("utf-8", "replace")
    try:
        offset = int(body[0:WORD], 16) * 2
        length = int(body[offset : offset + WORD], 16) * 2
        return bytes.fromhex(body[offset + WORD : offset + WORD + length]).decode("utf-8", "replace")
    except (ValueError, IndexError):
        return ""
