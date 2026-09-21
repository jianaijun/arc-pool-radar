"""Compiling `PoolRegistry.sol` and encoding calls to it, with no web3 library in sight.

Two jobs that are usually a dependency each: drive `solc` through its standard-JSON interface, and
ABI-encode the one function this project calls. Both are small enough to read, and keeping them here
is what lets the README say "zero dependencies" without an asterisk.

⚠️ **Everything in this module stops at an unsigned transaction.** Nothing here holds a key, signs,
or sends. `prepare_tx.py` writes a transaction to a file and the operator signs it in their own
wallet. That is a deliberate boundary, not an unfinished feature.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from .keccak import selector

#: The canonical signature, spelled with the struct flattened into a tuple as the ABI requires.
#: ⚠️ One wrong word here yields four bytes no function answers to -- and the chain reports that as
#: a bare revert, not as "unknown selector".
PUBLISH_SIGNATURE: Final[str] = "publish(bytes32[],(address,uint24,uint8,uint8,uint8,address,uint96)[],uint256,uint256)"

#: Pinned so two builds of the same source give the same bytecode. An unpinned optimiser setting is
#: the reason "it verified yesterday" stops being true.
OPTIMIZER_RUNS: Final[int] = 200
EVM_VERSION: Final[str] = "cancun"


@dataclass(frozen=True)
class Compiled:
    name: str
    abi: list[dict[str, Any]]
    bytecode: str
    """Creation bytecode, `0x`-prefixed: the deployment transaction's whole data field."""
    deployed_size: int


def compile_contract(source: Path, name: str, solc: Path) -> Compiled:
    """Compile one contract through solc's standard-JSON interface."""
    request = {
        "language": "Solidity",
        "sources": {source.name: {"content": source.read_text()}},
        "settings": {
            "optimizer": {"enabled": True, "runs": OPTIMIZER_RUNS},
            "evmVersion": EVM_VERSION,
            "outputSelection": {"*": {"*": ["abi", "evm.bytecode.object", "evm.deployedBytecode.object"]}},
        },
    }
    result = subprocess.run(
        [str(solc), "--standard-json"], input=json.dumps(request), capture_output=True, text=True, check=True
    )
    output = json.loads(result.stdout)

    # ⚠️ solc reports warnings and errors through the same list, so filter by severity rather than
    # by the list being non-empty -- treating every diagnostic as fatal makes the build fail on
    # style notes, and treating none as fatal ships a contract that did not compile.
    fatal = [d for d in output.get("errors", []) if d.get("severity") == "error"]
    if fatal:
        raise RuntimeError("solc: " + "\n".join(d["formattedMessage"] for d in fatal))
    for note in output.get("errors", []):
        print(f"  solc {note['severity']}: {note.get('message', '').splitlines()[0]}")

    contract = output["contracts"][source.name][name]
    return Compiled(
        name=name,
        abi=contract["abi"],
        bytecode="0x" + contract["evm"]["bytecode"]["object"],
        deployed_size=len(contract["evm"]["deployedBytecode"]["object"]) // 2,
    )


def _word(value: int) -> str:
    """One 32-byte ABI word. Negative values are not expected and are rejected rather than wrapped."""
    if value < 0:
        raise ValueError(f"cannot encode negative {value} as an unsigned word")
    return f"{value:064x}"


def _address_word(address: str) -> str:
    return _word(int(address, 16))


def encode_publish(
    ids: list[str],
    facts: list[tuple[str, int, int, int, int, str, int]],
    census_block: int,
    total: int,
) -> str:
    """Calldata for `publish(...)`.

    `facts` entries are `(token0, fee, flags, decimals0, decimals1, token1, volumeUsd)` in the
    struct's declared order. ⚠️ The order is the struct's, not any order that reads nicely: the ABI
    encodes a tuple positionally, so a swapped pair of same-width fields produces a registry full of
    confident wrong answers and no error anywhere.
    """
    if len(ids) != len(facts):
        raise ValueError(f"{len(ids)} ids but {len(facts)} facts")

    count = len(ids)
    # Four head words, then the ids array, then the facts array. Each fact is a STATIC tuple of
    # seven words, so the facts array needs no inner offsets.
    ids_offset = 4 * 32
    facts_offset = ids_offset + 32 + count * 32

    body = _word(ids_offset) + _word(facts_offset) + _word(census_block) + _word(total)
    body += _word(count) + "".join(entry[2:].rjust(64, "0") for entry in ids)
    body += _word(count)
    for token0, fee, flags, decimals0, decimals1, token1, volume in facts:
        body += (
            _address_word(token0)
            + _word(fee)
            + _word(flags)
            + _word(decimals0)
            + _word(decimals1)
            + _address_word(token1)
            + _word(volume)
        )
    return "0x" + selector(PUBLISH_SIGNATURE).hex() + body
