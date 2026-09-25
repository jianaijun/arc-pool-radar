# arc-pool-radar

**Is this Arc pool what it claims to be, and is its price worth believing?**

Arc's public mainnet is days old. It has no established token list, no settled set of venues, and
more than 200,000 initialised Uniswap v4 pools (209,300 on 2026-09-24). In that situation the two things a trader, a router or an
agent most needs to know about a pool are the two things nothing on the chain will tell them:

1. **Is this pair what the symbols say it is?** A symbol is a string the token returns about itself.
   It is attacker-controlled and it costs nothing to make it say `USDC`.
2. **Is this price a price?** An AMM quotes continuously and moves only when traded. A pool nobody
   trades still shows a number, and that number compared against a live exchange looks exactly like
   a large, persistent arbitrage.

This package answers both, off-chain for a web page and on-chain for a contract.

---

## The two findings that made it

Neither of these is a hypothetical. Both were found while measuring Arc, and both are reproducible
with the scripts in this repo.

### Three different tokens claim to be USDC

Scanning only the **200 busiest** non-USDC tokens on Arc turns up three separate contracts whose
`symbol()` returns `USDC`. Between them they sit in **134 pools**:

| address | claims | decimals | pools |
|---|---|---|---|
| `0x8e98a62a995a50eca9979bfa016f91bf36a8f9d9` | `USDC` | **18** | 79 |
| `0xb67f50fde86e09b5da963c4251cbd4788b151ed5` | `USDC` | **18** | 29 |
| `0xd100106d403fe0a4a38d14159d7055eed0fe0087` | `USDC` | **18** | 26 |

The real USDC on Arc is a **precompile** at `0x3600...0000` and carries **6** decimals.

⚠️ **That table is a floor, and the way it grew is the point.** A separate pass — ranking pools by
the volume they actually did rather than tokens by how many pools they sit in — surfaced
`0x7366d2ffaf98b6621fa3791d760de2c942dbc95d`, which the first pass had not reached. Each way of
looking finds impostors the other misses, so the honest claim is not "there are three" but **"there
are at least five, and nobody is counting"**. That is the case for a registry rather than a list.

⭐ Note the second harm, which is worse than the first and quieter. All three declare 18 decimals.
Anything that identifies USDC by its symbol and then divides by six decimals is not merely trading
the wrong token — it is computing a price **10¹² off**, and getting a number back rather than an
error.

⚠️ Beware the obvious defence. "Check the token has bytecode" rejects the one token on this chain
that is certainly real, because a precompile has none. **Identity is the address.**

### A pool holding $866 reported the best arbitrage on the chain

Priced against a centralised exchange, one pool showed **69.05%** of its fills net profitable after
fees — comfortably the best figure anything on Arc produced. The two pools with real depth reported
**0.00%** in both directions.

The pool held **$866**.

📛 The tell was not the size, because a size threshold is a number someone picks. The tell was
structural: the apparent dislocation's median lifetime, **129 seconds**, was almost exactly the
pool's **mean interval between swaps**. The edge was not decaying. It was being refreshed once per
trade and sitting perfectly still in between. It was staleness wearing the costume of an
opportunity.

So `sizing.assess()` checks volume **and** trade spacing, and reports them separately, because they
are different failures: one pool is too small to trade, the other is quoting you something old.

---

## What is deployed on Arc

`contracts/PoolRegistry.sol` — the census, published on chain 5042 so that **other contracts** can
ask before they route. A router, an aggregator or an agent can call `isTradeable(poolId)` in the
same transaction it is about to swap in. A JSON file cannot be read from inside a transaction.

```solidity
function check(bytes32 poolId) external view returns (PoolFacts memory);
function isTradeable(bytes32 poolId) external view returns (bool);
function hasImpostorSymbol(bytes32 poolId) external view returns (bool);
```

A v4 pool is keyed by its `PoolId`; a v3 pool, which has no `PoolId`, by its address left-padded to
32 bytes.

⚠️ **Absence is not a verdict.** An unknown pool returns `flags == 0`, which means *unjudged*, never
*safe*. `isTradeable` enforces that distinction so a caller cannot forget it.

| | |
|---|---|
| Registry | [`0xdCb56839F4E4eA80B15499F298E02a858254Bb48`](https://explorer.arc.io/address/0xdCb56839F4E4eA80B15499F298E02a858254Bb48) |
| Live page | https://jianaijun.github.io/arc-pool-radar/ |
| Chain | Arc mainnet, id **5042** |
| Deployed | block 22,628,099, [tx `0x2baab02c…ef5a`](https://explorer.arc.io/tx/0x2baab02c9aae3d84ca26d86d7307fb35df5cb93442f94611d9d9329f32c9ef5a) |
| Source | verified, **exact match** (creation and runtime), on [Sourcify](https://repo.sourcify.dev/5042/0xdCb56839F4E4eA80B15499F298E02a858254Bb48) |
| Published | 109 pools from the census at block 22,538,972, [tx `0x00c2c955…a682`](https://explorer.arc.io/tx/0x00c2c9551dbe23db4851f43e615e3f5acb56a58f6b62b0698e9e4415d1eea682) |

---

## What it costs to run

**What it actually cost**, from the two receipts:

| | gas used | paid |
|---|---|---|
| deploy `PoolRegistry` | 584,767 | 0.011754 USDC |
| publish 109 pools, one transaction | 5,336,196 | 0.107830 USDC |
| **total** | | **0.119584 USDC** |

That is 48,956 gas a pool including the transaction's own overhead; the model below says 46,718 for
the storage alone, **4.8% under**. The model was written before either number existed, and is kept
as it was so the comparison stays honest.

Before deployment, the cost was derived from gas constants rather than estimated by feel:

```
$ python scripts/estimate_registry_cost.py

gas price      21.164 gwei  =  $0.0212 per million gas
gas schedule   bare transfer estimates 21,000 gas -> STANDARD

deploy PoolRegistry.sol             611,575 gas   $  0.0129     <- measured, not modelled
one pool, first write                46,718 gas   $  0.0010
one pool, refresh                    12,518 gas   $  0.0003

                                census  batches            gas   first write  each refresh
   size-gated subset (what we publish)        3     23,421,800         $0.50         $0.13
          every pool that traded in 6h       11     94,273,529         $2.00         $0.54
           every pool ever initialised      898  8,405,648,270       $177.90        $47.96
```

⭐ That last row is the design decision, priced. Publishing all 179,521 pools the chain had then costs **$177.90** for a
registry in which almost every entry would say "dust, ignore". Only the size-gated subset goes on
chain; `published` and `total` are both exposed so a caller sees the difference rather than
mistaking an absent pool for a judged one.

⚠️ **Half of that table is a measurement and half is still a model, and they are labelled rather
than averaged.** The chain was checked for the one thing that would invalidate the model — a bare
transfer estimates at exactly 21,000 gas, so Arc runs the standard schedule and EIP-2929's SSTORE
constants apply.

- **Deploy: measured.** `eth_estimateGas` on the real compiled creation bytecode. It replaced a
  budget of 900,000 that was **52% too high** — outside the ±20% band that budget claimed for
  itself, and wrong on the safe side. A budget that over-states is a budget that quietly stops
  being checked, which is why it was replaced the hour a compiler was available.
- **Per pool: modelled, then measured 4.8% higher** (above). Measuring it needed a *deployed*
  registry, and estimating before then is worse than not estimating: a call to an address with no
  code does not fail, it returns base-plus-calldata and silently omits every storage write.
  `prepare_tx.py publish` refuses to run until the address has bytecode.

---

## Install and run

Python 3.11+. **Zero dependencies** — standard library only, so the whole package can be read top to
bottom without resolving anything, and it cannot rot when a dependency does.

```bash
git clone https://github.com/jianaijun/arc-pool-radar
cd arc-pool-radar

python scripts/selftest.py                        # hashes and ABI encoding, no network needed
python scripts/estimate_registry_cost.py          # what putting this on Arc costs
python scripts/build_snapshot.py --hours 6        # census the chain -> docs/data/snapshot.json
python -m http.server -d docs 8777                # then open http://localhost:8777
```

The page is one HTML file with no build step. Its top strip is **live** — your browser talks to
`rpc.mainnet.arc.io` directly, which is why the whole thing hosts as a static page and has no server
to be down. The table below it is a snapshot, because censusing every pool on the chain is not a
thing to do on page load. Both are labelled on screen as what they are.

Everything in `src/arcradar` is read-only against public endpoints. There is no key material, no
signing, and no order flow anywhere in this repository.

## Deploying it

```bash
python scripts/install_solc.py                                  # pinned 0.8.37, SHA-256 verified
python scripts/prepare_tx.py deploy  --from 0xYourAddress
python scripts/wallet_page.py out/deploy_tx.json               # -> out/sign.html
# open out/sign.html over http(s) in the browser that holds your wallet; approve there
python scripts/prepare_tx.py publish --from 0xYourAddress --registry 0xDeployed
python scripts/wallet_page.py out/publish_*_tx.json
```

📛 **Nothing in this repository signs or sends a transaction.** `prepare_tx.py` compiles, asks Arc
what the call costs, writes a complete unsigned transaction to `out/`, and stops. There is no
`--yes` and no private-key argument, because the safe version of those does not exist.
`wallet_page.py` hands those files to the wallet extension in your browser and nothing else; the
wallet's own confirmation is where they are signed or refused. What the page adds is the checks a
JSON file cannot make: the connected account and chain must match, the account's nonce must equal
the prepared one (which is what stops a reload from deploying a second registry), and afterwards it
reads the registry back rather than trusting the receipt's status.

⚠️ A wallet that lives only in a phone app needs the page over **https**: a mobile DApp browser
injected nothing into the same page served over plain http on the LAN. This deployment was signed
from a phone with the page served through `tailscale serve`, reachable only from the owner's own
devices.

⭐ The compiler is pinned to a version **and a SHA-256 written into the source**, and mismatching
bytes are discarded rather than quarantined. "It came from the official domain over HTTPS" is not a
check. The check is that the bytes hash to a value written down before the download happened.

⚠️ `scripts/selftest.py` runs first and has no network dependency. It decodes the generated calldata
back, field by field, and compares it to what went in — because every way of getting ABI encoding
wrong (a field in the wrong slot, a tuple transposed, an offset off by one word) produces a hex
string of exactly the right shape. `keccak256` gets the same treatment against published vectors:
Python's `hashlib.sha3_256` is **not** keccak, it differs by one padding byte, and it returns a
perfectly good digest that is never the one Ethereum means.

## Layout

| path | what is in it |
|---|---|
| `src/arcradar/chain.py` | Arc's addresses and topics, each with how it was verified on chain |
| `src/arcradar/rpc.py` | JSON-RPC with backoff that **counts the windows it could not read** |
| `src/arcradar/abi.py` | the decoding, written once so it is debugged once |
| `src/arcradar/tokens.py` | symbol-versus-address identity, and the impostor check |
| `src/arcradar/sizing.py` | volume and staleness, reported separately |
| `src/arcradar/v4.py` | the v4 singleton: `PoolId` to pair, and swap decoding |
| `src/arcradar/cost.py` | the gas model above |
| `contracts/PoolRegistry.sol` | what goes on chain |
| `src/arcradar/keccak.py` | keccak256 in pure Python, because `hashlib` has the other one |
| `src/arcradar/solidity.py` | drives `solc`, and ABI-encodes the one call this makes |
| `scripts/build_snapshot.py` | the census that produces the page's data |
| `scripts/prepare_tx.py` | builds unsigned transactions; signs nothing |
| `scripts/wallet_page.py` | a local page that hands those to your wallet, with the checks around it |
| `scripts/selftest.py` | hashes and calldata, checked without a chain |
| `docs/index.html` | the page; one file, no build, no dependencies |

## Why the code is shaped the way it is

Three of the bugs found while building this were decoding bugs, they were the same bug three times,
and each was written fresh in a new file while a correct version of it sat in a neighbouring one.
**None of them raised. Each produced a number.** That is why the decoders live in one module.

⭐ The best of them is worth stating, because the lesson is not about Solidity. Uniswap v4 declares
its swap amounts `int128`, so a sign test at `2**127` looks right — but the ABI sign-extends a
negative `int128` across the whole 32-byte slot, so every negative amount came back around `10**77`.
The tell was not the magnitude; numbers that are merely huge look like numbers. The tell was that
thresholds of $1k, $100k, $1M and $10M all selected **exactly 1,713 pools**. Four different
questions cannot have one answer. The evidence was in the structure of the counts, not in the size
of the values.

⭐ The other one is about honesty rather than arithmetic, and it is the reason `fetch_logs` exists.
Two runs over an identical two-hour window reported **66.7%** and **50.0%** coverage, and the page
duly warned the reader that "counts here are floors, not totals". Nothing was being rate-limited.
Arc's node had been answering

```
-32602  query exceeds max results 2000, retry with the range 21851086-21851301
```

— naming the exact range it *would* serve — and the scanner was filing that instruction under
"refused". Following the hint took coverage to **100%** and the v4 log count from ~37,000 to
**63,745**: roughly forty percent of the chain had been sitting behind an error message that was
really a suggestion.

📛 A correctly worded caveat attached to a wrong diagnosis is worse than no caveat, because it
reads as diligence and cannot be falsified from outside. The fix is not to caveat harder. It is to
make the failing case produce a *different reading* from the working one — which is why
`lost_windows` now only fills up when something is genuinely wrong.

## Licence

MIT.
