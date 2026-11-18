"""How varied a run of seeds is: counts by bucket, and a warning when one bucket
holds more than 80 % of the designs."""
from __future__ import annotations

from collections import Counter

DOMINANT = 0.8


def _buckets(d: dict) -> dict[str, str]:
    risers = [e for e in d["elements"] if e["type"] == "riser"]
    styles = {e["params"]["style"] for e in risers}
    return {"risers": str(len(risers)),
            "shared": "shared" if any(len(e["attached_zones"]) > 1 for e in risers) else "not shared",
            "style": "/".join(sorted(styles)) if styles else "none",
            "sleeved": "sleeved" if any(e["params"]["sleeved"] for e in risers) else "unsleeved",
            "ingate_placement": d["provenance"]["knob_values"].get("ingate_placement", "?")}


def diversity(designs: list[dict], rejected: list[dict]) -> dict:
    counts: dict[str, Counter] = {}
    for d in designs:
        for k, v in _buckets(d).items():
            counts.setdefault(k, Counter())[v] += 1
    reasons = Counter(r for rej in rejected for r in rej["reasons"])
    warnings = []
    n = len(designs)
    for k, c in counts.items():
        top, m = c.most_common(1)[0]
        if n and m / n > DOMINANT and len(c) >= 1 and n > 1:
            warnings.append(f"{m}/{n} designs ({100 * m / n:.0f} %) fall in {k} = {top}")
    return {"designs": n, "rejected": len(rejected), "by": {k: dict(v) for k, v in counts.items()},
            "rejection_reasons": dict(reasons), "warnings": warnings}


def format_report(rep: dict) -> str:
    lines = [f"{rep['designs']} designs, {rep['rejected']} rejected"]
    for k, c in rep["by"].items():
        lines.append(f"  {k:<17}" + ", ".join(f"{v}: {n}" for v, n in sorted(c.items())))
    if rep["rejection_reasons"]:
        lines.append("  rejected because " + ", ".join(f"{r}: {n}" for r, n in
                                                      sorted(rep["rejection_reasons"].items(), key=lambda x: -x[1])))
    lines += [f"  WARNING: {w}" for w in rep["warnings"]]
    return "\n".join(lines)


def print_report(rep: dict) -> None:
    print(format_report(rep))
