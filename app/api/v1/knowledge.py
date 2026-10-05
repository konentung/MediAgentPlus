"""衛教資訊（知識庫 / RAG）後台 API：/api/v1/admin/knowledge/*，頁面在 /admin/knowledge。

管理以院所為單位、檢索不分院所（共用池，病人自己院所的內容優先）；
資料來源只有 PDF 上傳與後台手動新增：
- 讀取 / 新增 / 修改 / 刪除：EDUCATION_CONTENT_MANAGE（院所管理員 / 系統管理員）；
  院所管理員只看得到、只能改自己院所的文件，通用與別家的文件只有 SUPER_ADMIN 能存取
- hospitals：各院所的文件 / 片段 / 向量數（SUPER_ADMIN 看全部，院方帳號只有自己院所）
- search：搜尋，看本院病人問某個問題會檢索到哪些片段（所有院所共用、本院優先）
- jobs：PDF 匯入與重建片段（EDUCATION_CONTENT_MANAGE，院所管理員只動自己院所、
  做完自動算向量）與進度查詢；全庫計算向量只有 SUPER_ADMIN（REFERENCE_DATA_MANAGE）
"""

from __future__ import annotations

from flask import Blueprint, current_app, request

from app.auth.decorators import current_principal, require_permission
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission
from app.services.knowledge_job_service import MAX_UPLOAD_FILES
from app.services.knowledge_service import CATEGORIES

admin_knowledge_bp = Blueprint("admin_knowledge", __name__, url_prefix="/admin/knowledge")


def _optional_json_body() -> dict:
    """body 可為空；有給就必須是 JSON 物件。"""  # 新增
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


def _json_body() -> dict:
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


@admin_knowledge_bp.get("")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def list_documents():
    return ok(get_container().knowledge.list(current_principal()), categories=CATEGORIES)


@admin_knowledge_bp.get("/hospitals")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def hospital_overview():
    """各院所有幾篇文件、切成幾個片段、幾個已有向量（通用文件的 `hospital_id` 為 null）。"""
    return ok(get_container().knowledge.hospital_overview(current_principal()))


@admin_knowledge_bp.get("/search")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def search_documents():
    query = (request.args.get("q") or "").strip()
    if not query:
        raise ValidationError("請提供 q")
    return ok(get_container().knowledge.search_preview(current_principal(), query))


# ---- 背景工作（PDF 匯入 / 重建片段 / 向量計算）----
# 放在 /<document_id> 之前只是為了閱讀順序；Flask 的靜態路徑本來就優先於變數路徑


@admin_knowledge_bp.get("/jobs")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def job_overview():
    """統計（文件 / 片段 / 已有向量）+ 進行中的工作 + 最近幾筆，頁面每幾秒輪詢一次。

    院所管理員只看得到自己院所的統計與匯入工作。
    """
    return ok(get_container().knowledge_jobs.overview(current_principal()))


@admin_knowledge_bp.get("/jobs/<job_id>")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
def get_job(job_id: str):
    return ok(get_container().knowledge_jobs.get(current_principal(), job_id))


@admin_knowledge_bp.post("/jobs/pdf-import")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
@rate_limit("knowledge_pdf_import", limit=20, per_seconds=600, by="user")
def start_pdf_import():
    """上傳衛教 PDF（multipart 欄位 files 可多檔），背景擷取文字、切段、算向量。

    一個 PDF 一篇文件（標題 = 檔名，同院所同檔名重傳 = 更新）。表單欄位：
    `department`（整批套用的科別，可空）、`chunk_size`（small / medium / large，預設 medium）、
    `hospital_id`（只有 SUPER_ADMIN 有用：院所代碼，留空 = 通用；院所管理員一律歸自己院所）。
    一次上傳的總大小上限是 `KNOWLEDGE_UPLOAD_MAX_MB`（預設 1 GB），比全站的 128 MB 寬。
    """
    # 修改：一批衛教 PDF 常超過全站的 MAX_CONTENT_LENGTH，只對這個端點放寬。
    # 必須在讀 request.files 之前設定；到這裡已通過權限檢查（decorator 只看 header、不讀 body）
    request.max_content_length = current_app.config["KNOWLEDGE_UPLOAD_MAX_BYTES"]
    request.max_form_parts = MAX_UPLOAD_FILES + 20  # 每個檔案一個 part，外加幾個表單欄位
    uploads = [(f.stream, f.filename) for f in request.files.getlist("files") if f.filename]
    if not uploads:
        raise ValidationError("請上傳 .pdf 檔（欄位 files）")
    form = request.form
    job = get_container().knowledge_jobs.start_pdf_import(
        current_principal(),
        uploads,
        hospital_id=form.get("hospital_id"),
        department=form.get("department"),
        chunk_size=form.get("chunk_size"),
    )
    return ok(job, status=202)


@admin_knowledge_bp.post("/jobs/rechunk")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
@rate_limit("knowledge_rechunk", limit=5, per_seconds=600, by="user")
def start_rechunk():
    """重建片段（背景執行）：院所管理員重建本院文件、做完自動算向量；SUPER_ADMIN 重建全部。

    body 可帶 `chunk_size`（small / medium / large）把範圍內文件都改成這個大小，
    不帶則各文件維持原本的大小。
    """
    body = _optional_json_body()
    job = get_container().knowledge_jobs.start_rechunk(
        current_principal(), chunk_size=body.get("chunk_size") or None
    )
    return ok(job, status=202)


@admin_knowledge_bp.post("/jobs/embed")
@require_permission(Permission.REFERENCE_DATA_MANAGE)
@rate_limit("knowledge_embed", limit=5, per_seconds=600, by="user")
def start_embed():
    """替還沒有向量的片段計算 embedding（背景執行、可中斷重跑）。"""
    body = _optional_json_body()  # 修改
    try:
        batch = int(body.get("batch") or 200)
    except (TypeError, ValueError) as exc:
        raise ValidationError("batch 需為整數") from exc
    job = get_container().knowledge_jobs.start_embed(current_principal(), batch=batch)
    return ok(job, status=202)


@admin_knowledge_bp.get("/<document_id>")
@require_permission(Permission.EDUCATION_CONTENT_MANAGE)
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
