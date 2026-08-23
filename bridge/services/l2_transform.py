"""L2/L1/L0 条目 -> MemoryEvent 转换
(L2 命名沿用 Cyrene 旧习惯,实际是支持 3 层: L0 画像 / L1 状态 / L2 事件)"""
from datetime import datetime, timezone


# L0 字段 -> (event_type, default_importance)
# L0 是核心用户画像,importance 高
L0_FIELD_MAP: dict[str, tuple[str, float]] = {
    "nickname": ("FACT", 0.9),
    "preferredName": ("PREFERENCE", 0.9),
    "occupation": ("FACT", 0.85),
    "longTermInterests": ("PREFERENCE", 0.8),
    "permanentNote": ("FACT", 1.0),
    "language": ("FACT", 0.7),
}

# L1 字段
L1_FIELD_MAP: dict[str, tuple[str, float]] = {
    "recentGoals": ("GOAL", 0.8),
    "recentPreferences": ("PREFERENCE", 0.8),
    "currentProject": ("FACT", 0.75),
}


def cyrene_l2_to_memory_event(item: dict) -> dict:
    """将 L0/L1/L2 条目转成下游 MemoryEvent 格式

    Args:
        item: {
            "id": "client-uuid-xxx",
            "layer": "L0" | "L1" | "L2",
            "field": "nickname",  # L0/L1 必填
            "content": "用户昵称是 xilan",
            "event_type": "FACT",  # L2 选填,默认 FACT
            "entities": [...],     # 选填
            "importance": 0.8,     # 选填
            "is_pinned": false,    # 选填
            "source_ts": 1724123456789
        }

    Returns:
        {
            "memory_content": "...",
            "event_type": "FACT",
            "entities": [...],
            "importance_score": 0.8,
            "metadata": {
                "cyrene_layer": "L0",
                "cyrene_id": "...",
                "cyrene_source": "...",
                "source_ts": ...
            }
        }
    """
    layer = item.get("layer", "L2")
    content = (item.get("content") or "").strip()
    if not content:
        raise ValueError(f"item {item.get('id')} has empty content")

    metadata: dict = {
        "cyrene_layer": layer,
        "cyrene_id": item.get("id"),
        "cyrene_source": item.get("source") or "cyrene-global",
        "source_ts": item.get("source_ts") or int(datetime.now(timezone.utc).timestamp() * 1000),
    }

    if layer == "L0":
        field = item.get("field") or "permanentNote"
        event_type, importance = L0_FIELD_MAP.get(field, ("FACT", 0.8))
        metadata["cyrene_field"] = field
        metadata["is_pinned"] = True
        memory_content = f"{field}: {content}"
    elif layer == "L1":
        field = item.get("field") or "currentProject"
        event_type, importance = L1_FIELD_MAP.get(field, ("FACT", 0.7))
        metadata["cyrene_field"] = field
        memory_content = f"{field}: {content}"
    else:  # L2
        event_type = item.get("event_type") or "FACT"
        importance = float(item.get("importance") or 0.6)
        memory_content = content

    if item.get("is_pinned"):
        importance = max(importance, 0.9)
        metadata["is_pinned"] = True

    return {
        "memory_content": memory_content,
        "event_type": event_type,
        "entities": item.get("entities") or [],
        "importance_score": importance,
        "metadata": metadata,
    }
