"""Shared helpers for assembling knowledge-base context into prompts."""

from __future__ import annotations

from .knowledge_service import KnowledgeService

MAX_REFERENCE_CONTEXT_CHARS = 12000


def build_reference_context(
    *,
    knowledge_service: KnowledgeService,
    additional_file_ids: list[str],
    user_id: str,
    empty_message: str = "无额外参考资料。",
) -> str:
    """Join selected knowledge files into a compact prompt-friendly context."""
    if not additional_file_ids:
        return empty_message

    blocks: list[str] = []
    total_chars = 0
    for file_id in additional_file_ids:
        record = knowledge_service.get_file(file_id, user_id=user_id)
        if record is None:
            raise ValueError(f"知识库文件不存在：{file_id}")

        if record.file_type == "document":
            body = knowledge_service.get_file_content(file_id, user_id=user_id)
            label = f"文档：{record.filename}"
        else:
            description = (record.description or "").strip()
            tags = knowledge_service.get_file_tags(record)
            tag_text = f"（标签：{'、'.join(tags)}）" if tags else ""
            body = "\n".join(part for part in [description, tag_text] if part).strip()
            label = f"图片：{record.filename}"

        if not body:
            continue

        remaining = MAX_REFERENCE_CONTEXT_CHARS - total_chars
        if remaining <= 0:
            break

        snippet = body[:remaining]
        block = f"{label}\n{snippet}"
        blocks.append(block)
        total_chars += len(block)

    return "\n\n".join(blocks) if blocks else empty_message
