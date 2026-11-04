"""Numbers for the deck's quantile / TopK slides -> thesis/quantile_data.json (read by build_deck.js).

    .venv\\Scripts\\python.exe tools\\quantile_slide_data.py
"""
from __future__ import annotations

import glob
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from ra2opt import load_db  # noqa: E402
from ra2tools.opponent import SLOT, Opponent, threat  # noqa: E402


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    db = load_db()
    out = {"slots": [], "q": {}, "waves": {}}
    for p in (50, 80):
        o = Opponent.load(ROOT / "calibration" / "opponents" / f"hard_q{p}.json")
        out["games"] = o.notes["quantile"]["games"]
        n = o.frames[-1] // SLOT + 1
        out["slots"] = [f"{s * 3}–{s * 3 + 3}" for s in range(n)]
        # mean threat over each slot: what quantile() ranks the games by
        out["q"][str(p)] = [round(sum(threat(db, u, b) for f, u, b in zip(o.frames, o.units, o.buildings)
                                      if s * SLOT <= f < (s + 1) * SLOT)
                                  / max(1, sum(1 for f in o.frames if s * SLOT <= f < (s + 1) * SLOT)), -2)
                            for s in range(n)]
        out["waves"][str(p)] = [{"minute": round(w["frame"] / 1200, 1),
                                 "value": round(threat(db, w["units"], {}))} for w in o.attacks]
    abl = json.loads((ROOT / "results" / "topk_ablation.json").read_text(encoding="utf-8"))
    out["ablation"] = {"top_k": abl["top_k"]}
    for variant, curves in abl["runs"].items():
        finals = [c[-1] for c in curves if c[-1]]
        first = [next((g for g, x in enumerate(c) if x), None) for c in curves]
        out["ablation"][variant] = {
            "runs": len(curves), "solved": len(finals),
            "mean": round(statistics.mean(finals)) if finals else None,
            "sd": round(statistics.pstdev(finals)) if finals else None,
            "first_gen": [f for f in first if f is not None]}
    res = sorted(glob.glob(str(ROOT / "results" / "soviet_vs_hard_quantile_victory_*.json")))[-1]
    d = json.loads(Path(res).read_text(encoding="utf-8"))
    c = d["front"][d["chosen"]]
    out["chosen"] = {"army": c["army"], "spent": round(c["spent"]), "ready": round(c["ready"]),
                     "victory": {k: round(v) if v else None for k, v in c["victory"].items()},
                     "survive": round(c["survive"] * 100), "front": len(d["front"])}
    path = ROOT / "thesis" / "quantile_data.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False))


if __name__ == "__main__":
    main()
