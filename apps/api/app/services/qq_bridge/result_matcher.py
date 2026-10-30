from .contracts import Binding, Incoming, Query
from .policy import rejection_reason


def match_result(binding: Binding, query: Query, events: list[Incoming]) -> str | None:
    """Require an explicit operation and a complete, consistent set of parts."""
    parts: dict[int, str] = {}
    seen: set[str] = set()
    total = None
    for event in events:
        if rejection_reason(binding, query, event) or event.event_id in seen:
            continue
        seen.add(event.event_id)
        if total is not None and total != event.total:
            raise ValueError("同一请求的分段总数不一致")
        total = event.total
        if event.part in parts and parts[event.part] != event.body:
            raise ValueError("同一分段出现冲突内容")
        parts[event.part] = event.body
    if total is None or set(parts) != set(range(1, total + 1)):
        return None
    return "\n".join(parts[index] for index in range(1, total + 1))
