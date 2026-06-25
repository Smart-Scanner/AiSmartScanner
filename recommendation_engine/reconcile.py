"""RE-3 P0 shadow build + reconciliation (RE-2A §3 P0 gate).

Builds ROs for a set of analyzer results, validates the RE-1 invariants (fail-closed),
optionally persists, and returns a reconciliation summary:
  * counts (total / eligible / rejected + reject-reason histogram),
  * invariant violations among ELIGIBLE ROs  (gate: must be 0),
  * legacy divergence (RO-derived vs original analyzer levels — expected, since the RO
    repairs the dual-system defects).
"""
import logging

from . import store
from .builder import build_recommendation_object
from .projection import project_legacy
from .trade_engine import _f

log = logging.getLogger("recommendation_engine.reconcile")

_GEN_AT = "1970-01-01 00:00:00"  # deterministic placeholder; caller stamps real time post-build


def _invariants_hold(ro) -> bool:
    t = ro.get("trade") or {}
    if not t.get("valid"):
        return False
    e = t["entry"]; sl = t["stop_loss"]["price"]; tg = [x["price"] for x in t["targets"]]
    rr = t["risk_reward"]
    return (sl < e["low"] <= e["ref"] <= e["high"] < tg[0] < tg[1] < tg[2]
            and rr is not None and rr >= 1.5)


def shadow_build(results, scan_id, persist=True, generated_at_utc=_GEN_AT):
    if persist:
        try:
            store.init_recommendation_store()
        except Exception as exc:
            log.warning("[RE3-P0] store init failed (continuing in-memory): %s", exc)
            persist = False

    summary = {"scan_id": scan_id, "total": 0, "eligible": 0, "rejected": 0,
               "invariant_violations": 0, "persisted": 0, "reject_reasons": {},
               "legacy_divergence": {"target": 0, "stop_loss": 0, "rr": 0, "compared": 0},
               "violation_examples": []}

    for result in results or []:
        if not result.get("symbol"):
            continue
        summary["total"] += 1
        ro = build_recommendation_object(result, scan_id=scan_id, generated_at_utc=generated_at_utc)
        eligible = ro["eligibility"]["eligible"]

        if eligible:
            summary["eligible"] += 1
            if not _invariants_hold(ro):
                summary["invariant_violations"] += 1
                if len(summary["violation_examples"]) < 5:
                    summary["violation_examples"].append(ro["meta"]["symbol"])
        else:
            summary["rejected"] += 1
            for g in ro["eligibility"]["gates"]:
                if not g["pass"]:
                    summary["reject_reasons"][g["id"]] = summary["reject_reasons"].get(g["id"], 0) + 1

        # reconciliation vs original analyzer values
        leg = project_legacy(ro)
        ot = _f((result.get("trade") or {}).get("target1"))
        osl = _f((result.get("trade") or {}).get("stop_loss"))
        orr = _f(result.get("risk_reward"))
        d = summary["legacy_divergence"]
        d["compared"] += 1
        if ot is not None and leg["target_price"] is not None and abs(ot - leg["target_price"]) > 0.01:
            d["target"] += 1
        if osl is not None and leg["stop_loss"] is not None and abs(osl - leg["stop_loss"]) > 0.01:
            d["stop_loss"] += 1
        if orr is not None and leg["risk_reward"] is not None and abs(orr - leg["risk_reward"]) > 0.05:
            d["rr"] += 1

        if persist:
            try:
                store.save_recommendation(ro)
                summary["persisted"] += 1
            except Exception as exc:
                log.warning("[RE3-P0] persist failed for %s: %s", ro["meta"]["symbol"], exc)

    log.info("[RE3-P0] shadow build scan=%s total=%d eligible=%d rejected=%d invariant_violations=%d persisted=%d",
             scan_id, summary["total"], summary["eligible"], summary["rejected"],
             summary["invariant_violations"], summary["persisted"])
    return summary
