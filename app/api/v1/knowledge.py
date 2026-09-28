"""知識庫（RAG）後台 API：/api/v1/admin/knowledge/*，頁面在 /admin/knowledge。

- 讀取：任何院方角色（看得到通用 + 自己院所的文件）
- 新增 / 修改 / 刪除 / 匯入：EDUCATION_CONTENT_MANAGE；通用文件只有 SUPER_ADMIN 能改
- search：試搜，看某個問題會檢索到哪些片段
"""

from __future__ import annotations

from flask import Blueprint, request

from app.auth.decorators import current_principal, require_permission, require_role
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission, Role
from app.services.knowledge_service import CATEGORIES, parse_markdown

admin_knowledge_bp = Blueprint("admin_knowledge", __name__, url_prefix="/admin/knowledge")
STAFF = [r for r in Role if r.is_staff]


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


@admin_knowledge_bp.get("")
@require_role(*STAFF)
def list_documents():
    return ok(get_container().knowledge.list(current_principal()), categories=CATEGORIES)


@admin_knowledge_bp.get("/search")
@require_role(*STAFF)
def search_documents():
    query = (request.args.get("q") or "").strip()
    if not query:
        raise ValidationError("請提供 q")
    return ok(get_container().knowledge.search_preview(current_principal(), query))


@admin_knowledge_bp.get("/<document_id>")
@require_role(*STAFF)
def get_document(document_id: str):
    return ok(get_container().knowledge.get(current_principal(), document_id))


@admin_knowledge_bp.post("")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def create_document():
    return ok(get_container().knowledge.create(current_principal(), _json_body()), status=201)


@admin_knowledge_bp.patch("/<document_id>")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def update_document(document_id: str):
    return ok(get_container().knowledge.update(current_principal(), document_id, _json_body()))


@admin_knowledge_bp.delete("/<document_id>")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def delete_document(document_id: str):
    get_container().knowledge.delete(current_principal(), document_id)
    return ok(None)


@admin_knowledge_bp.post("/import")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
@rate_limit("knowledge_import", limit=30, per_seconds=600, by="user")
def import_markdown():
    """上傳一或多個 Markdown（multipart 欄位 files），front matter 可帶 title / category / tags。"""
    uploads = request.files.getlist("files") or request.files.getlist("file")
    if not uploads:
        raise ValidationError("請上傳 .md 檔（欄位 files）")
    actor = current_principal()
    service = get_container().knowledge
    created = updated = 0
    errors: list[dict] = []
    for upload in uploads:
        name = upload.filename or "upload.md"
        try:
            fields = parse_markdown(
                upload.read().decode("utf-8"), default_title=name.rsplit(".", 1)[0]
            )
            fields["source"] = name
            existing = service.find_by_source(actor, name)
            if existing:
                service.update(actor, existing["id"], fields)
                updated += 1
            else:
                service.create(actor, fields)
                created += 1
        except Exception as exc:  # noqa: BLE001 - 單一檔案失敗不中斷其他
            errors.append({"file": name, "message": getattr(exc, "message", str(exc))})
    return ok({"created": created, "updated": updated, "errors": errors})
