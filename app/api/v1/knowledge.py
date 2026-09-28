"""知識庫（RAG）後台 API：/api/v1/admin/knowledge/*，頁面在 /admin/knowledge。

- 讀取：任何院方角色（看得到通用 + 自己院所的文件）
- 新增 / 修改 / 刪除 / 匯入：EDUCATION_CONTENT_MANAGE；通用文件只有 SUPER_ADMIN 能改
- search：試搜，看某個問題會檢索到哪些片段
- jobs：系統資料匯入（衛教 JSONL）與向量計算，背景執行；只有 SUPER_ADMIN（REFERENCE_DATA_MANAGE）
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


# ---- 系統維護工作（衛教 JSONL 匯入 / 向量計算）----
# 放在 /<document_id> 之前只是為了閱讀順序；Flask 的靜態路徑本來就優先於變數路徑


@admin_knowledge_bp.get("/jobs")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
def job_overview():
    """統計（文件 / 片段 / 已有向量）+ 進行中的工作 + 最近幾筆，頁面每幾秒輪詢一次。"""
    return ok(get_container().knowledge_jobs.overview(current_principal()))


@admin_knowledge_bp.get("/jobs/<job_id>")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
def get_job(job_id: str):
    return ok(get_container().knowledge_jobs.get(current_principal(), job_id))


@admin_knowledge_bp.post("/jobs/health-education")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
@rate_limit("knowledge_health_education_import", limit=5, per_seconds=600, by="user")
def start_health_education_import():
    """上傳衛教內文 JSONL（multipart 欄位 file；一行一篇），背景匯入為通用文件。"""
    upload = request.files.get("file")
    if upload is None or not upload.filename:
        raise ValidationError("請上傳 .jsonl 檔（欄位 file）")
    form = request.form
    try:
        min_chars = int(form.get("min_chars") or 200)
        limit = int(form["limit"]) if form.get("limit") else None
    except ValueError as exc:
        raise ValidationError("min_chars / limit 需為整數") from exc
    include_all = (form.get("include_all") or "").lower() in ("1", "true", "on")
    job = get_container().knowledge_jobs.start_health_education(
        current_principal(),
        upload.stream,
        upload.filename,
        min_chars=min_chars,
        include_all=include_all,
        limit=limit,
    )
    return ok(job, status=202)


@admin_knowledge_bp.post("/jobs/embed")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
@rate_limit("knowledge_embed", limit=5, per_seconds=600, by="user")
def start_embed():
    """替還沒有向量的片段計算 embedding（背景執行、可中斷重跑）。"""
    body = request.get_json(silent=True) or {}
    try:
        batch = int(body.get("batch") or 200)
    except (TypeError, ValueError) as exc:
        raise ValidationError("batch 需為整數") from exc
    job = get_container().knowledge_jobs.start_embed(current_principal(), batch=batch)
    return ok(job, status=202)


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
