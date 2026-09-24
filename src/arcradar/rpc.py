"""A JSON-RPC client for Arc's public node, whose only unusual feature is that it counts its losses.

The public RPC rate-limits. An earlier version of this scan lost 51 of 288 windows to HTTP 429 and
still printed a total -- a number that cannot tell "there are no pools here" from "I was not allowed
to look". Every request here retries with backoff, and a window that never succeeds is recorded in
`lost_windows` and reported, so a run that lost coverage says so in its own output instead of
quietly reporting a smaller world.

Standard library only, on purpose: this package has zero dependencies, so a reviewer can read it
top to bottom without resolving anything.
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any, Final, cast

from . import chain

#: Arc's node names the range it will serve inside its own refusal. Parsing that is the difference
#: between a complete census and one that quietly reports a smaller chain -- see `fetch_logs`.
_RANGE_HINT: Final[re.Pattern[str]] = re.compile(r"retry with the range (\d+)-(\d+)")

_HEADERS: Final[dict[str, str]] = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "User-Agent": "arc-pool-radar/0.1",
}


class RpcError(RuntimeError):
    """A call the node refused, or refused often enough that retrying stopped being honest."""


class ArcRpc:
    """One connection's worth of state: a request counter and the windows it could not read."""

    def __init__(self, url: str = chain.RPC_URL, *, attempts: int = 6, min_interval: float = 0.12) -> None:
        self.url = url
        self.attempts = attempts
        #: ⭐ **Pace the requests rather than discover the limit by hitting it.** Backoff alone is a
        #: worse deal than it looks: a refused call still costs a round trip, the retry waits
        #: seconds, and a window refused every time is dropped -- so hammering does not merely slow
        #: a census down, it makes it report a smaller chain. Two runs of the same 2-hour window
        #: without this returned 66.7% and 50.0% coverage. A small fixed gap between calls buys back
        #: the windows, and it is cheaper than the backoff it avoids.
        self.min_interval = min_interval
        self.lost_windows: list[int] = []
        self._id = 0
        self._last_call = 0.0

    def call(self, method: str, params: list[Any]) -> Any:
        """One JSON-RPC call, paced, and retried through 429s and transport errors."""
        for attempt in range(self.attempts):
            gap = self.min_interval - (time.monotonic() - self._last_call)
            if gap > 0:
                time.sleep(gap)
            self._last_call = time.monotonic()
            self._id += 1
            body = json.dumps({"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}).encode()
            request = urllib.request.Request(self.url, data=body, headers=_HEADERS)
            try:
                with urllib.request.urlopen(request, timeout=90) as response:
                    payload = cast("dict[str, Any]", json.loads(response.read()))
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    time.sleep(min(2.0**attempt, 16.0))
                    continue
                raise
            # ⚠️ `RemoteDisconnected` (the node closing the socket without answering) is neither a
            # URLError nor a TimeoutError -- it is a ConnectionError and an HTTPException -- so an
            # earlier version let one transient drop crash a twelve-minute scan. All four are the
            # same situation from here: the request did not complete, ask again.
            except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                time.sleep(min(2.0**attempt, 16.0))
                continue
            if "error" in payload:
                raise RpcError(f"{method}: {payload['error']}")
            return payload["result"]
        raise RpcError(f"{method}: refused after {self.attempts} attempts")

    def block_number(self) -> int:
        return int(str(self.call("eth_blockNumber", [])), 16)

    def gas_price(self) -> int:
        """Wei per gas. Arc's native unit is USDC at 18 decimals, so this divided by 1e18 is USD."""
        return int(str(self.call("eth_gasPrice", [])), 16)

    def block_timestamp(self, number: int) -> int:
        block = self.call("eth_getBlockByNumber", [hex(number), False])
        if not isinstance(block, dict):
            raise RpcError(f"no block {number}")
        return int(str(cast("dict[str, Any]", block)["timestamp"]), 16)

    def eth_call(self, to: str, data: str) -> str:
        return str(self.call("eth_call", [{"to": to, "data": data}, "latest"]))

    def estimate_gas(self, tx: dict[str, Any]) -> int:
        return int(str(self.call("eth_estimateGas", [tx])), 16)

    def fetch_logs(
        self, topics: list[Any], start: int, end: int, *, address: str | None = None
    ) -> list[dict[str, Any]]:
        """Every matching log between two blocks, narrowing the request until the node answers.

        📛 **THE NODE'S REFUSAL IS AN INSTRUCTION, NOT A FAILURE, AND READING IT AS A FAILURE COSTS
        REAL DATA.** Arc's public RPC answers a too-broad `eth_getLogs` with

            -32602  query exceeds max results 2000, retry with the range 21851086-21851301

        which names the exact range it *will* serve. An earlier version of this class caught that as
        an `RpcError`, filed the window under `lost_windows`, and moved on -- so two runs of the same
        two-hour span reported 66.7% and 50.0% coverage and the page displayed "counts here are
        floors, not totals". ⭐ That caveat was honestly worded and attached to a wrong diagnosis:
        nothing was being rate-limited, and every block was available for the asking.

        ⚠️ Note also what the probe showed about the documented caps: windows returning 17,516 and
        19,547 logs were served without complaint, far above the "2000" the error names. The limit
        is neither a fixed block range nor a fixed result count that can be predicted in advance --
        which is the whole argument for asking and then following the answer, instead of computing
        a window size up front and trusting it.
        """
        out: list[dict[str, Any]] = []
        cursor = start
        while cursor <= end:
            stop = end
            while True:
                query: dict[str, Any] = {"fromBlock": hex(cursor), "toBlock": hex(stop), "topics": topics}
                if address:
                    query["address"] = address
                try:
                    logs = self.call("eth_getLogs", [query])
                    break
                except RpcError as exc:
                    hint = _RANGE_HINT.search(str(exc))
                    if hint and cursor <= int(hint.group(2)) < stop:
                        stop = int(hint.group(2))
                        continue
                    if stop > cursor:
                        # No usable hint: halve and ask again. Converges on a single block, which
                        # the node has no grounds to refuse for being too wide.
                        stop = cursor + (stop - cursor) // 2
                        continue
                    raise
            if isinstance(logs, list):
                out.extend(cast("list[dict[str, Any]]", logs))
            cursor = stop + 1
        return out

    def scan_logs(
        self,
        topics: list[Any],
        from_block: int,
        to_block: int,
        *,
        address: str | None = None,
        on_window: Any = None,
    ) -> list[dict[str, Any]]:
        """Every matching log in a range, walked in windows and narrowed where the node asks.

        A window that genuinely cannot be read -- after narrowing all the way down -- is appended to
        `lost_windows` and skipped. Callers that report counts are expected to report that list
        beside them; `coverage()` exists so they can. ⭐ With `fetch_logs` doing the narrowing, a
        non-empty `lost_windows` now means something is actually wrong, rather than meaning the
        query was wide.
        """
        out: list[dict[str, Any]] = []
        starts = list(range(from_block, to_block + 1, chain.MAX_LOG_RANGE))
        for index, start in enumerate(starts):
            end = min(start + chain.MAX_LOG_RANGE - 1, to_block)
            try:
                out.extend(self.fetch_logs(topics, start, end, address=address))
            except RpcError:
                self.lost_windows.append(start)
                continue
            if on_window is not None:
                on_window(index + 1, len(starts), len(out))
        return out

    def scan_logs_anchored(
        self,
        topics: list[Any],
        from_block: int,
        to_block: int,
        *,
        address: str | None = None,
        on_window: Any = None,
    ) -> tuple[list[dict[str, Any]], dict[int, int]]:
        """`scan_logs`, plus one real block timestamp per window, for callers that need times.

        ⭐ **One anchor PER WINDOW, never one for the whole range.** Arc's block time is stable to
        about 0.4%, so interpolating inside a single 5,000-block window drifts well under a second
        -- but interpolating a six-hour range from one endpoint drifts by over a minute, and this
        package measures whether a pool's quote refreshes within 30 seconds. A global anchor would
        not produce an error; it would produce staleness verdicts that are wrong by two windows'
        worth of drift.

        The anchor costs one extra call per window and is only fetched for windows that returned
        logs, because a window with nothing in it has nothing to timestamp.
        """
        out: list[dict[str, Any]] = []
        anchors: dict[int, int] = {}
        starts = list(range(from_block, to_block + 1, chain.MAX_LOG_RANGE))
        for index, start in enumerate(starts):
            end = min(start + chain.MAX_LOG_RANGE - 1, to_block)
            try:
                logs = self.fetch_logs(topics, start, end, address=address)
            except RpcError:
                self.lost_windows.append(start)
                continue
            if logs:
                out.extend(logs)
                anchors[start] = self.block_timestamp(start)
            if on_window is not None:
                on_window(index + 1, len(starts), len(out))
        return out, anchors

    def coverage(self, windows_attempted: int) -> float:
        """The share of windows this run actually read. Print it; do not assume it is 1.0."""
        if windows_attempted <= 0:
            return 1.0
        return 1.0 - len(self.lost_windows) / windows_attempted
