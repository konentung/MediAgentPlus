"""LIFF 專用端點：登入換 JWT、病人自填表（intake）、個人資料、問題回報。

前端流程：
  liff.init({liffId}) → liff.getIDToken() → POST /api/v1/liff/auth → 取得 JWT
  之後所有請求帶 Authorization: Bearer <access_token>

表單流程（頁面在 /liff/intake，見 app/liff_pages.py）：
  GET  /api/v1/liff/intake              schema + 已填資料 + 進度
  PUT  /api/v1/liff/intake/steps/<n>    存單一步驟（body 帶 partial=true 為草稿）
  POST /api/v1/liff/intake/submit       全表驗證 → 存一份原始資料 → binding_status=PENDING
就醫院所（獨立表單，頁面在 /liff/hospitals；每家院所一筆）：
  GET    /api/v1/liff/hospitals/mine                 我加入的院所（含是否已確認 / 已建檔）
  POST   /api/v1/liff/hospitals/mine                 加入一家（body hospital_id；自己加的即確認）
  POST   /api/v1/liff/hospitals/mine/<id>/confirm    回答院方的確認（body accepted: true/false）
  DELETE /api/v1/liff/hospitals/mine/<id>            移除（已建檔的不能移除）
"""

from __future__ import annotations

from flask import Blueprint, request

from app.auth.decorators import (
    current_principal,
    require_auth,
    require_permission,
    require_verified,
)
from app.core.container import get_container
from app.core.exceptions import ValidationError
from app.core.ratelimit import rate_limit
from app.core.responses import ok
from app.domain.enums import Permission
from app.services.feedback_service import CATEGORIES as FEEDBACK_CATEGORIES


def feedback_categories() -> list[dict[str, str]]:
    return [{"value": k, "label": v} for k, v in FEEDBACK_CATEGORIES.items()]


liff_bp = Blueprint("liff", __name__, url_prefix="/liff")


@liff_bp.get("/config")
def liff_config():
    """前端初始化所需的公開設定。"""
    return ok({"liff_id": get_container().liff_auth.liff_id})


def _optional_json_body() -> dict:
    """body 可為空；有給就必須是 JSON 物件。"""  # 新增
    body = request.get_json(silent=True)
    if body is None:
        return {}
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return body


@liff_bp.post("/auth")
@rate_limit("liff_auth", limit=30, per_seconds=300)
def liff_auth():
    body = _optional_json_body()
    container = get_container()
    identity = container.liff_auth.authenticate(
        id_token=body.get("id_token"), access_token=body.get("access_token")
    )
    user = container.users.get_or_create_by_line(
        identity.line_user_id, identity.display_name, identity.picture_url
    )
    principal = container.users.to_principal(user)
    # 沒有 refresh token：到期後前端重新 liff.init() 換一張即可
    tokens = container.jwt.issue(principal)
    return ok({"tokens": tokens.to_dict(), "principal": principal.to_dict()})


@liff_bp.get("/me")
@require_auth()
def liff_me():
    return ok({"principal": current_principal().to_dict()})


@liff_bp.get("/hospitals")
@rate_limit("liff_hospital_search", limit=240, per_seconds=600)
def liff_hospitals():
    """公開的院所資料。`?q=關鍵字`：「我的就醫院所」加入院所的搜尋（含診所，最多 20 筆）；
    不帶 `q`：醫院層級的完整選單。"""
    hospitals = get_container().hospitals
    if "q" in request.args:
        return ok(hospitals.public_search(request.args.get("q")))
    return ok(hospitals.public_list())


@liff_bp.get("/hospitals/mine")
@require_permission(Permission.INTAKE_WRITE_OWN)
def my_hospitals():
    return ok(get_container().patient_hospitals.list_mine(current_principal()))


@liff_bp.post("/hospitals/mine")
@require_permission(Permission.INTAKE_WRITE_OWN)
@rate_limit("liff_hospital_add", limit=30, per_seconds=600, by="user")
def add_my_hospital():
    body = request.get_json(silent=True) or {}
    link = get_container().patient_hospitals.add_mine(current_principal(), body.get("hospital_id"))
    return ok(link, status=201)


@liff_bp.post("/hospitals/mine/<hospital_id>/confirm")
@require_permission(Permission.INTAKE_WRITE_OWN)
def confirm_my_hospital(hospital_id: str):
    """回答院方的「是否在本院就醫」：`accepted` 為 true 記下確認、false 移除這家院所。"""
    body = request.get_json(silent=True) or {}
    accepted = body.get("accepted")
    if not isinstance(accepted, bool):
        raise ValidationError("accepted 需為 true 或 false")
    hospital = get_container().patient_hospitals.confirm(
        current_principal(), hospital_id, accepted=accepted
    )
    return ok(
        {"hospital_id": hospital["id"], "hospital_name": hospital["name"], "accepted": accepted}
    )


@liff_bp.delete("/hospitals/mine/<hospital_id>")
@require_permission(Permission.INTAKE_WRITE_OWN)
def remove_my_hospital(hospital_id: str):
    get_container().patient_hospitals.remove_mine(current_principal(), hospital_id)
    return ok(None)


@liff_bp.get("/intake")
@require_permission(Permission.INTAKE_WRITE_OWN)
def intake_state():
    return ok(get_container().intake.get_form_state(current_principal()))


@liff_bp.put("/intake/steps/<int:step_number>")
@require_permission(Permission.INTAKE_WRITE_OWN)
@rate_limit("intake_save", limit=120, per_seconds=600, by="user")
def intake_save_step(step_number: int):
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出表單資料")
    partial = bool(body.pop("partial", False))
    profile = get_container().intake.save_step(
        current_principal(),
        step_number,
        body,
        partial=partial,
        ip_address=request.remote_addr,
    )
    return ok({"profile": profile})


@liff_bp.post("/intake/submit")
@require_permission(Permission.INTAKE_WRITE_OWN)
def intake_submit():
    return ok(get_container().intake.submit(current_principal()))


@liff_bp.get("/feedback")
@require_permission(Permission.FEEDBACK_SUBMIT)
def feedback_list():
    """表單頁的類別選項與自己送過的回報。"""
    container = get_container()
    return ok(
        {
            "categories": feedback_categories(),
            "items": container.feedback.list_mine(current_principal()),
        }
    )


@liff_bp.post("/feedback")
@require_permission(Permission.FEEDBACK_SUBMIT)
@rate_limit("feedback_submit", limit=10, per_seconds=3600, by="user")
def feedback_submit():
    """問題回報：任何登入的 LINE 使用者都能送，不需填過病人表單。"""
    body = request.get_json(silent=True)
    if not isinstance(body, dict):
        raise ValidationError("請以 JSON 物件送出")
    return ok(get_container().feedback.submit(current_principal(), body))


@liff_bp.get("/profile")
@require_verified()
@require_permission(Permission.PROFILE_READ_OWN)
def profile():
    return ok({"profile": get_container().intake.get_profile(current_principal())})
