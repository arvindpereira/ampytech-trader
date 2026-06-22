"""Validate that LLM synthesis cites real external_analyst_items."""
import re
from typing import Any, Dict, List, Set

_ITEM_RE = re.compile(r"item:(\d+)")


def _collect_source_ids(obj: Any, found: Set[str]) -> None:
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == "sources" and isinstance(v, list):
                for s in v:
                    if isinstance(s, str):
                        for m in _ITEM_RE.finditer(s):
                            found.add(m.group(1))
                        if s.startswith("item:"):
                            found.add(s.split(":", 1)[1])
            else:
                _collect_source_ids(v, found)
    elif isinstance(obj, list):
        for x in obj:
            _collect_source_ids(x, found)


def validate(synthesis: Dict[str, Any], valid_item_ids: List[int]) -> Dict[str, Any]:
    """Return synthesis with extra caveats for missing item references."""
    valid = {str(i) for i in valid_item_ids}
    cited: Set[str] = set()
    _collect_source_ids(synthesis, cited)
    missing = sorted(cited - valid)
    warnings: List[str] = []
    if missing:
        warnings.append(
            f"Citation validator: referenced item IDs not in source set: {', '.join(missing)}"
        )
    out = dict(synthesis)
    caveats = list(out.get("caveats") or [])
    caveats.extend(warnings)
    out["caveats"] = caveats
    out["_citation_warnings"] = warnings
    return out
