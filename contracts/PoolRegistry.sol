// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

/// @title PoolRegistry
/// @notice On-chain, callable answers to "is this Arc pool what it claims to be, and is its price
///         worth believing?" -- published from an off-chain census of every Uniswap v3 and v4 pool
///         on Arc.
///
/// @dev WHY THIS IS ON CHAIN AT ALL. The census could live in a JSON file, and the front end reads
///      one. It is published here as well so that OTHER contracts can ask before they route: a
///      router, an aggregator or an agent can call `isTradeable` in the same transaction it is
///      about to swap in. A file cannot be read from inside a transaction.
///
/// @dev WHY ONLY A SUBSET IS PUBLISHED. Arc has 179,521 initialised v4 pools. Writing all of them
///      costs about 2 storage slots each, which at Arc's 21.3 gwei is roughly $0.001 per pool and
///      therefore about $183 for the full set -- for a registry in which the overwhelming majority
///      of entries would say "dust, ignore". Only pools that cleared the size gate are published;
///      `total` and `published` are both exposed so a caller can see the difference rather than
///      mistake an absent pool for a judged one.
///
/// @dev ABSENCE IS NOT A VERDICT. `check` on an unknown pool returns a zeroed struct with
///      `flags == 0`. Callers must treat flags==0 as UNKNOWN, never as safe. `isTradeable` does
///      this for them, which is why it exists as a separate function rather than as a comment.
contract PoolRegistry {
    uint8 public constant FLAG_KNOWN = 1 << 0;
    uint8 public constant FLAG_DUST = 1 << 1;
    uint8 public constant FLAG_THIN = 1 << 2;
    uint8 public constant FLAG_TRADEABLE = 1 << 3;
    /// @notice One side of this pool returns a reserved symbol from an address not entitled to it.
    uint8 public constant FLAG_SYMBOL_IMPOSTOR = 1 << 4;
    /// @notice This pool's non-USDC side has a centralised-exchange market, so it has a reference price.
    uint8 public constant FLAG_HAS_CEX_LEG = 1 << 5;

    /// @dev Laid out to occupy exactly two storage slots: 160+24+8+8+8 bits, then 160+96.
    ///      The layout is the cost, so it is stated here rather than left to the compiler to
    ///      surprise someone with later.
    struct PoolFacts {
        address token0;
        uint24 fee;
        uint8 flags;
        uint8 decimals0;
        uint8 decimals1;
        address token1;
        uint96 volumeUsd;
    }

    address public publisher;
    /// @notice Pools written to this registry.
    uint256 public published;
    /// @notice Pools the off-chain census saw, including the ones not worth a slot.
    uint256 public total;
    /// @notice Block height the published census was taken at.
    uint256 public censusBlock;

    mapping(bytes32 => PoolFacts) private _facts;

    event Published(uint256 count, uint256 censusBlock);
    event PublisherChanged(address indexed from, address indexed to);

    error NotPublisher();
    error LengthMismatch();

    constructor() {
        publisher = msg.sender;
    }

    modifier onlyPublisher() {
        if (msg.sender != publisher) revert NotPublisher();
        _;
    }

    function setPublisher(address next) external onlyPublisher {
        emit PublisherChanged(publisher, next);
        publisher = next;
    }

    /// @notice Write a batch of pool facts. Re-publishing an existing pool overwrites it, which is
    ///         cheaper than a fresh write and is the normal path for a refreshed census.
    function publish(bytes32[] calldata ids, PoolFacts[] calldata facts, uint256 censusBlock_, uint256 total_)
        external
        onlyPublisher
    {
        if (ids.length != facts.length) revert LengthMismatch();
        uint256 fresh = 0;
        for (uint256 i = 0; i < ids.length; ++i) {
            if (_facts[ids[i]].flags == 0) ++fresh;
            PoolFacts calldata f = facts[i];
            _facts[ids[i]] = PoolFacts({
                token0: f.token0,
                fee: f.fee,
                flags: f.flags | FLAG_KNOWN,
                decimals0: f.decimals0,
                decimals1: f.decimals1,
                token1: f.token1,
                volumeUsd: f.volumeUsd
            });
        }
        published += fresh;
        censusBlock = censusBlock_;
        total = total_;
        emit Published(ids.length, censusBlock_);
    }

    /// @notice Everything the registry knows about a pool. `flags == 0` means UNKNOWN, not safe.
    function check(bytes32 poolId) external view returns (PoolFacts memory) {
        return _facts[poolId];
    }

    /// @notice True only for a pool this registry has judged AND judged tradeable.
    /// @dev The two conditions are separate on purpose: an unknown pool and a dust pool are
    ///      different mistakes, and a caller that conflates them will route into the dust one.
    function isTradeable(bytes32 poolId) external view returns (bool) {
        uint8 flags = _facts[poolId].flags;
        return flags & FLAG_KNOWN != 0 && flags & FLAG_TRADEABLE != 0;
    }

    /// @notice True when the registry has seen this pool and found a side lying about its symbol.
    function hasImpostorSymbol(bytes32 poolId) external view returns (bool) {
        uint8 flags = _facts[poolId].flags;
        return flags & FLAG_KNOWN != 0 && flags & FLAG_SYMBOL_IMPOSTOR != 0;
    }
}
