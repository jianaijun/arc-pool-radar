"""Ethereum's keccak256, in pure Python, because the obvious substitute is silently the wrong hash.

📛 **`hashlib.sha3_256` IS NOT KECCAK-256.** They share a permutation and differ in one padding
byte: the finished SHA-3 standard pads with `0x06`, the original Keccak submission Ethereum froze on
pads with `0x01`. So `hashlib` returns a perfectly good 32-byte digest for every input and it is
never the digest Ethereum means. Used for a function selector it produces four bytes no contract
answers to; the transaction does not revert with a helpful message, it hits the fallback or reverts
bare. ⇒ Another member of this codebase's recurring family: **the failure mode is a value, not an
error.**

Implemented here rather than pulled from a package so the "zero dependencies" claim in the README
stays literally true. It is about sixty lines, it is pinned by the test vectors at the bottom, and
`selftest()` runs them -- the scripts call it before they build any calldata, so a broken hash
cannot reach a transaction.
"""

from __future__ import annotations

from typing import Final

_MASK: Final[int] = (1 << 64) - 1
_RATE: Final[int] = 136  # 1088 bits, the rate for a 256-bit digest

_ROUND_CONSTANTS: Final[tuple[int, ...]] = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)  # fmt: skip
_ROTATIONS: Final[tuple[int, ...]] = (1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14,
                                      27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44)  # fmt: skip
_PERMUTATION: Final[tuple[int, ...]] = (10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4,
                                         15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1)  # fmt: skip


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK


def _permute(state: list[int]) -> None:
    for round_constant in _ROUND_CONSTANTS:
        parity = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        theta = [parity[(x - 1) % 5] ^ _rotl(parity[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(0, 25, 5):
                state[x + y] ^= theta[x]

        carry = state[1]
        for index in range(24):
            target = _PERMUTATION[index]
            carry, state[target] = state[target], _rotl(carry, _ROTATIONS[index])

        for y in range(0, 25, 5):
            row = state[y : y + 5]
            for x in range(5):
                state[y + x] = row[x] ^ ((~row[(x + 1) % 5] & _MASK) & row[(x + 2) % 5])

        state[0] ^= round_constant


def keccak256(data: bytes) -> bytes:
    """The 32-byte Ethereum keccak256 digest of `data`."""
    padded = bytearray(data)
    # The one byte that separates this from SHA-3. See the module docstring.
    padded.append(0x01)
    while len(padded) % _RATE != 0:
        padded.append(0x00)
    padded[-1] |= 0x80

    state = [0] * 25
    for offset in range(0, len(padded), _RATE):
        block = padded[offset : offset + _RATE]
        for lane in range(_RATE // 8):
            state[lane] ^= int.from_bytes(block[lane * 8 : lane * 8 + 8], "little")
        _permute(state)
    return b"".join(state[lane].to_bytes(8, "little") for lane in range(4))


def selector(signature: str) -> bytes:
    """The 4-byte function selector for a canonical Solidity signature."""
    return keccak256(signature.encode())[:4]


#: Published vectors. The empty-string digest in particular is the one number in this file that a
#: reader can check against any Ethereum reference without running anything.
_VECTORS: Final[tuple[tuple[bytes, str], ...]] = (
    (b"", "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"),
    (b"abc", "4e03657aea45a94fc7d47ba826c8d667c0d1e6e33a64a036ec44f58fa12d6c45"),
    (
        b"The quick brown fox jumps over the lazy dog",
        "4d741b6f1eb29cb2a9b9911c82f56fa8d73b04959d3d9d222895df6c0b28aa15",
    ),
)


def selftest() -> None:
    """Raise unless every published vector matches. Call this before building any calldata."""
    for payload, expected in _VECTORS:
        got = keccak256(payload).hex()
        if got != expected:
            raise RuntimeError(f"keccak256({payload!r}) = {got}, expected {expected}")
    # ⭐ And the one that actually matters here: a real selector everyone can look up.
    if selector("transfer(address,uint256)").hex() != "a9059cbb":
        raise RuntimeError("selector('transfer(address,uint256)') is not a9059cbb")
