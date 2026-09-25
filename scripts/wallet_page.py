#!/usr/bin/env python
"""Wrap the unsigned transactions in `out/` in a local page that hands them to YOUR wallet.

    python scripts/wallet_page.py out/deploy_tx.json            # -> out/sign.html
    python scripts/wallet_page.py out/publish_*_tx.json

📛 **The page holds no key and signs nothing.** It calls `eth_sendTransaction` on the wallet
extension in your browser; the wallet shows its own confirmation, and the signature -- or the
refusal -- happens there. What the page adds is the checks a JSON file cannot make for itself:

- the connected account must be the `from` the transaction was prepared for, or nothing is sent;
- the wallet must be on chain 5042;
- the account's pending nonce must equal the one the transaction was prepared with. For a deploy
  this is what stops a second, duplicate registry after a page reload; for publish batches it is
  what enforces their order;
- after a deploy it reads back that the new address has code and that `publisher()` is you, rather
  than trusting the receipt's `status` alone.

Open it over http, not file:// -- most wallet extensions do not inject into local files:

    python -m http.server -d out 8778 --bind 127.0.0.1     # then http://127.0.0.1:8778/sign.html

A wallet that lives only in a phone app cannot reach 127.0.0.1 on this machine. Bind the LAN address
instead and open the page in the app's DApp browser on the same Wi-Fi; `out/` holds nothing secret,
but stop the server when done.
"""

# ruff: noqa: E501 -- the long lines are inside the embedded page, where wrapping would change the markup.
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: keccak256("publisher()")[:4], computed with `arcradar.keccak` (not hashlib, which is SHA-3).
PUBLISHER_SELECTOR = "0x8c72c54e"

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>arc-pool-radar: sign</title>
<style>
 body{font:14px/1.5 system-ui,sans-serif;max-width:760px;margin:2rem auto;padding:0 16px;color:#1b1b1b;background:#fafaf8}
 h1{font-size:18px} code{font-size:12px;word-break:break-all}
 .card{border:1px solid #ccc;border-radius:6px;padding:12px 14px;margin:12px 0;background:#fff}
 .ok{color:#17692b} .bad{color:#b3261e;font-weight:600} .dim{color:#666}
 button{font:inherit;padding:6px 14px;margin:4px 6px 4px 0;cursor:pointer}
 button:disabled{cursor:not-allowed;opacity:.5}
 table{border-collapse:collapse} td{padding:1px 10px 1px 0;vertical-align:top}
</style>
<h1>arc-pool-radar &mdash; hand prepared transactions to your wallet</h1>
<p class="dim">This page has no key. Every transaction below is signed, or refused, in your wallet's own
confirmation window. Check the account, the chain (Arc, 5042) and the fee there before approving.</p>
<div class="card">
 <button id="connect">1. Connect wallet</button>
 <button id="chain" disabled>2. Switch to Arc (5042)</button>
 <div id="status" class="dim">not connected</div>
</div>
<div id="txs"></div>
<script>
const TXS = __TXS__;
const CHAIN = "0x13b2";
const PUBLISHER_SELECTOR = "__SEL__";
let eth = null;

// Mobile DApp browsers often inject their provider AFTER the page has loaded, so look for it when
// the button is pressed, not at load time; and say what was seen when nothing is there.
function findProvider() {
  const w = window;
  return w.ethereum || (w.tokenpocket && w.tokenpocket.ethereum) || null;
}
async function waitForProvider() {
  for (let i = 0; i < 30 && !findProvider(); i++) await new Promise((ok) => setTimeout(ok, 100));
  const p = findProvider();
  if (p && p !== eth) {
    eth = p;
    eth.on?.("accountsChanged", () => connect());
    eth.on?.("chainChanged", () => connect());
  }
  return p;
}
function diagnose() {
  const keys = Object.keys(window).filter((k) => /eth|wallet|tp|token|web3/i.test(k));
  return `No wallet provider on this page after 3 s. secure context: ${window.isSecureContext}; ` +
    `wallet-looking globals: [${keys.join(", ") || "none"}]; user agent: ${navigator.userAgent}`;
}
const $ = (id) => document.getElementById(id);
let account = null, onArc = false, busy = false;

function say(text, cls) { const s = $("status"); s.textContent = text; s.className = cls || "dim"; }
const lower = (a) => (a || "").toLowerCase();
const gwei = (hex) => (Number(BigInt(hex)) / 1e9).toFixed(2);

function render() {
  const box = $("txs"); box.innerHTML = "";
  TXS.forEach((t, i) => {
    const tx = t.tx, d = document.createElement("div"); d.className = "card"; d.id = "tx" + i;
    const bytes = (tx.data.length - 2) / 2;
    const upfront = Number(BigInt(tx.gas) * BigInt(tx.maxFeePerGas)) / 1e18;
    d.innerHTML = `<b>${t.name}</b> <span class="dim">${t.note}</span>
     <table>
      <tr><td>from</td><td><code>${tx.from}</code></td></tr>
      <tr><td>to</td><td>${tx.to ? "<code>" + tx.to + "</code>" : "<i>(none: contract creation)</i>"}</td></tr>
      <tr><td>nonce</td><td>${BigInt(tx.nonce)}</td></tr>
      <tr><td>value</td><td>${BigInt(tx.value)}</td></tr>
      <tr><td>data</td><td>${bytes.toLocaleString()} bytes</td></tr>
      <tr><td>gas limit</td><td>${BigInt(tx.gas).toLocaleString()}</td></tr>
      <tr><td>max fee</td><td>${gwei(tx.maxFeePerGas)} gwei (tip ${gwei(tx.maxPriorityFeePerGas)}) &mdash;
          wallet must hold at least ${upfront.toFixed(4)} USDC; actual cost is lower</td></tr>
     </table>
     <button id="send${i}" disabled>Send to wallet for signing</button>
     <div id="out${i}" class="dim"></div>`;
    box.appendChild(d);
    $("send" + i).onclick = () => send(i);
  });
}

function refresh() {
  $("chain").disabled = !account;
  TXS.forEach((t, i) => {
    const b = $("send" + i);
    if (b.dataset.done) return;
    b.disabled = busy || !onArc || lower(account) !== lower(t.tx.from);
  });
}

async function checkChain() {
  const id = await eth.request({ method: "eth_chainId" });
  onArc = lower(id) === CHAIN;
  return id;
}

async function connect() {
  if (!(await waitForProvider())) { say(diagnose(), "bad"); return; }
  const accs = await eth.request({ method: "eth_requestAccounts" });
  account = accs[0];
  const id = await checkChain();
  const want = TXS[0].tx.from;
  if (lower(account) !== lower(want)) {
    say(`Connected ${account}, but these transactions were prepared for ${want}. Switch account in the wallet.`, "bad");
  } else if (!onArc) {
    say(`Connected ${account}. Wallet is on chain ${parseInt(id, 16)}, not Arc: press 2.`, "bad");
  } else {
    say(`Connected ${account} on Arc (5042).`, "ok");
  }
  refresh();
}

async function switchChain() {
  try {
    await eth.request({ method: "wallet_switchEthereumChain", params: [{ chainId: CHAIN }] });
  } catch (e) {
    if (e.code !== 4902) { say("Switch refused: " + (e.message || e), "bad"); return; }
    await eth.request({ method: "wallet_addEthereumChain", params: [{
      chainId: CHAIN, chainName: "Arc", rpcUrls: ["https://rpc.mainnet.arc.io"],
      nativeCurrency: { name: "USDC", symbol: "USDC", decimals: 18 } }] });
  }
  await connect();
}

async function send(i) {
  const t = TXS[i], tx = t.tx, out = $("out" + i);
  busy = true; refresh();
  try {
    await checkChain();
    if (!onArc) throw new Error("wallet left Arc; switch back first");
    const accs = await eth.request({ method: "eth_accounts" });
    if (lower(accs[0]) !== lower(tx.from)) throw new Error("wallet account changed to " + accs[0]);
    const pending = await eth.request({ method: "eth_getTransactionCount", params: [tx.from, "pending"] });
    if (BigInt(pending) !== BigInt(tx.nonce)) {
      throw new Error(`account nonce is ${BigInt(pending)}, this transaction was prepared for ${BigInt(tx.nonce)}. ` +
        (BigInt(pending) > BigInt(tx.nonce)
          ? "It (or another transaction) has already been sent. Do not resend; re-run prepare_tx.py if needed."
          : "Send the earlier transactions first."));
    }
    const params = { from: tx.from, data: tx.data, value: tx.value, gas: tx.gas,
                     maxFeePerGas: tx.maxFeePerGas, maxPriorityFeePerGas: tx.maxPriorityFeePerGas };
    if (tx.to) params.to = tx.to;
    out.className = "dim"; out.textContent = "waiting for your approval in the wallet...";
    const hash = await eth.request({ method: "eth_sendTransaction", params: [params] });
    $("send" + i).dataset.done = "1"; $("send" + i).disabled = true;
    out.innerHTML = `sent: <code>${hash}</code><br>waiting for the receipt...`;
    let r = null;
    while (!r) {
      await new Promise((ok) => setTimeout(ok, 2000));
      r = await eth.request({ method: "eth_getTransactionReceipt", params: [hash] });
    }
    const used = BigInt(r.gasUsed), price = BigInt(r.effectiveGasPrice || tx.maxFeePerGas);
    const paid = (Number(used * price) / 1e18).toFixed(6);
    if (r.status !== "0x1") {
      out.className = "bad";
      out.innerHTML = `REVERTED in block ${BigInt(r.blockNumber)}. tx <code>${hash}</code>, gas used ${used}, paid ${paid} USDC.`;
      return;
    }
    let lines = [`confirmed in block ${BigInt(r.blockNumber)}; gas used ${used.toLocaleString()}; paid ${paid} USDC`,
                 `tx <code>${hash}</code>`];
    if (r.contractAddress) {
      const code = await eth.request({ method: "eth_getCode", params: [r.contractAddress, "latest"] });
      const pub = await eth.request({ method: "eth_call", params: [{ to: r.contractAddress, data: PUBLISHER_SELECTOR }, "latest"] });
      const pubAddr = "0x" + pub.slice(-40);
      const codeOk = code.length > 2, pubOk = lower(pubAddr) === lower(tx.from);
      lines.push(`<b>registry address <code>${r.contractAddress}</code></b>`);
      lines.push(`<span class="${codeOk ? "ok" : "bad"}">code at that address: ${(code.length - 2) / 2} bytes</span>`);
      lines.push(`<span class="${pubOk ? "ok" : "bad"}">publisher() = <code>${pubAddr}</code>${pubOk ? " (you)" : " (NOT you)"}</span>`);
    } else if (tx.to) {
      // A publish: read the registry's own counters back instead of trusting status alone.
      const read = async (sel) => BigInt(await eth.request({ method: "eth_call", params: [{ to: tx.to, data: sel }, "latest"] }));
      lines.push(`registry now reports published() = ${await read("0x8d4d2b0c")}, total() = ${await read("0x2ddbd13a")}, ` +
                 `censusBlock() = ${await read("0x4a60f7fc")}`);
    }
    out.className = "ok"; out.innerHTML = lines.join("<br>");
  } catch (e) {
    out.className = "bad"; out.textContent = "not sent: " + (e.message || e);
  } finally {
    busy = false; refresh();
  }
}

render();
$("connect").onclick = () => connect().catch((e) => say(e.message || String(e), "bad"));
$("chain").onclick = () => switchChain().catch((e) => say(e.message || String(e), "bad"));
</script>
"""


def main(paths: list[str]) -> int:
    if not paths:
        print(__doc__)
        return 2
    txs = []
    for p in paths:
        doc = json.loads(Path(p).read_text())
        txs.append({"name": Path(p).name, "note": doc.get("note", ""), "tx": doc["tx"]})
    senders = {t["tx"]["from"].lower() for t in txs}
    if len(senders) != 1:
        print(f"these files were prepared for different senders: {sorted(senders)}")
        return 1
    # `</` inside an inline <script> would end it; JSON never needs that sequence unescaped.
    blob = json.dumps(txs).replace("</", "<\\/")
    target = ROOT / "out" / "sign.html"
    target.write_text(PAGE.replace("__TXS__", blob).replace("__SEL__", PUBLISHER_SELECTOR))
    print(f"{len(txs)} transaction(s) -> {target}")
    print("serve it:  python -m http.server -d out 8778 --bind 127.0.0.1")
    print("then open: http://127.0.0.1:8778/sign.html  in the browser that has your wallet")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
