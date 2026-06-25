"""RE-3 legacy projection (RE-2A §0.1 / M1-M2).

`project_legacy(ro)` derives the LEGACY-shaped trade fields from the canonical RO so that,
from P1 onward, every legacy consumer reads values that are byte-identical to the RO
(legacy == projection(RO)). In P0 it is used only for reconciliation (RO-derived vs the
original analyzer values), proving the RO can reproduce the legacy surface.
"""


def project_legacy(ro: dict) -> dict:
    """Return legacy top-level + trade-dict fields derived purely from the RO."""
    trade = ro.get("trade") or {}
    entry = trade.get("entry") or {}
    sl = (trade.get("stop_loss") or {}).get("price")
    tgs = trade.get("targets") or []
    t = [x.get("price") for x in tgs]
    while len(t) < 3:
        t.append(None)
    entry_ref = entry.get("ref")
    return {
        # System-A top-level shape
        "price": (ro.get("inputs_snapshot") or {}).get("cmp"),
        "target_price": t[0],
        "stop_loss": sl,
        "risk_reward": trade.get("risk_reward"),
        # System-B trade-dict shape
        "trade": {
            "entry_low": entry.get("low"), "entry_high": entry.get("high"),
            "stop_loss": sl, "target1": t[0], "target2": t[1], "target3": t[2],
            "risk_reward": trade.get("risk_reward"),
        },
        "_entry_ref": entry_ref,
    }
