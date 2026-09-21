#!/usr/bin/env python
"""Everything this package can check without a chain, a key, or a deployed contract.

    python scripts/selftest.py

⭐ The point is the ABI encoder. `encode_publish` produces calldata that nothing will validate until
it is already a transaction: a field in the wrong slot, a tuple in the wrong order or an offset off
by one word all yield a hex string of exactly the right shape. So it is decoded back here and
compared against what went in, field by field -- the round trip is the only thing standing between a
transposed pair of same-width fields and a registry full of confident wrong answers.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from arcradar import abi, keccak, solidity

CASES: list[tuple[str, tuple[str, int, int, int, int, str, int]]] = [
    (
        "0x" + "11" * 32,
        (
            "0x3600000000000000000000000000000000000000",
            100,
            0b101001,
            6,
            18,
            "0x171a4217b86a807a64eb94757db6849fb4bdbaa0",
            273_143,
        ),
    ),
    (
        "0x" + "ab" * 32,
        (
            "0x8e98a62a995a50eca9979bfa016f91bf36a8f9d9",
            10_000,
            0b010011,
            18,
            6,
            "0x3600000000000000000000000000000000000000",
            866,
        ),
    ),
]


def check(label: str, got: object, want: object) -> bool:
    ok = got == want
    print(f"  {'ok ' if ok else 'FAIL'} {label:<34s} {got!r}{'' if ok else f'  != {want!r}'}")
    return ok


def main() -> int:
    keccak.selftest()
    print("keccak256 vectors                     ok")
    print(f"publish selector                      0x{keccak.selector(solidity.PUBLISH_SIGNATURE).hex()}")

    ids = [case[0] for case in CASES]
    facts = [case[1] for case in CASES]
    census_block, total = 21_856_205, 477
    data = solidity.encode_publish(ids, facts, census_block, total)

    body = data[10:]  # strip 0x and the 4-byte selector
    passed = True
    passed &= check("head[0] ids offset", int(abi.word("0x" + body, 0), 16), 128)
    passed &= check("head[1] facts offset", int(abi.word("0x" + body, 1), 16), 128 + 32 + len(ids) * 32)
    passed &= check("head[2] censusBlock", int(abi.word("0x" + body, 2), 16), census_block)
    passed &= check("head[3] total", int(abi.word("0x" + body, 3), 16), total)
    passed &= check("ids length", int(abi.word("0x" + body, 4), 16), len(ids))

    for index, pool_id in enumerate(ids):
        passed &= check(f"ids[{index}]", "0x" + abi.word("0x" + body, 5 + index), pool_id)

    facts_base = 5 + len(ids) + 1
    passed &= check("facts length", int(abi.word("0x" + body, facts_base - 1), 16), len(facts))
    for index, fact in enumerate(facts):
        at = facts_base + index * 7
        token0, fee, flags, dec0, dec1, token1, volume = fact
        passed &= check(f"facts[{index}].token0", abi.address(abi.word("0x" + body, at)), token0.lower())
        passed &= check(f"facts[{index}].fee", int(abi.word("0x" + body, at + 1), 16), fee)
        passed &= check(f"facts[{index}].flags", int(abi.word("0x" + body, at + 2), 16), flags)
        passed &= check(f"facts[{index}].decimals0", int(abi.word("0x" + body, at + 3), 16), dec0)
        passed &= check(f"facts[{index}].decimals1", int(abi.word("0x" + body, at + 4), 16), dec1)
        passed &= check(f"facts[{index}].token1", abi.address(abi.word("0x" + body, at + 5)), token1.lower())
        passed &= check(f"facts[{index}].volumeUsd", int(abi.word("0x" + body, at + 6), 16), volume)

    # ⚠️ Length is a weak check on its own, but a wrong one proves an offset is wrong, and the two
    # offsets above are the fields no field-by-field comparison can catch on its own.
    expected_len = 2 + 8 + (4 + 1 + len(ids) + 1 + len(facts) * 7) * 64
    passed &= check("calldata length", len(data), expected_len)

    print("\n" + ("all checks passed" if passed else "SOMETHING FAILED -- do not sign anything"))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
