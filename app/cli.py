"""Flask CLI 指令：richmenu / rbac / user / hospital / pharmacy / drug / reminder / triage /
knowledge / assistant"""

from __future__ import annotations

import csv
import json
import time
from datetime import datetime
from pathlib import Path

import click
from flask import Flask
from flask.cli import AppGroup

from app.core.container import get_container
from app.domain.enums import Role
from app.domain.principal import Principal
from app.domain.rbac import AccessPolicy
from app.line.richmenu import RichMenuManager

richmenu_cli = AppGroup("richmenu", help="Rich Menu 管理")
hospital_cli = AppGroup("hospital", help="院所管理")
rbac_cli = AppGroup("rbac", help="權限矩陣")
user_cli = AppGroup("user", help="使用者管理")
pharmacy_cli = AppGroup("pharmacy", help="藥局資料（開放資料匯入）")
drug_cli = AppGroup("drug", help="健保用藥資料（開放資料匯入）")
reminder_cli = AppGroup("reminder", help="服藥提醒排程")
triage_cli = AppGroup("triage", help="急診動態分流（健保署急診即時訊息）")
knowledge_cli = AppGroup("knowledge", help="AI 知識庫（RAG 檢索來源）")
assistant_cli = AppGroup("assistant", help="AI 對話助理（本機測試）")


def _read_csv(csv_path: Path) -> list[dict]:
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = csv_path.read_text(encoding="big5", errors="replace")
    return list(csv.DictReader(text.splitlines()))


@richmenu_cli.command("setup")
@click.option(
    "--image",
    "image_path",
    required=True,
    type=click.Path(exists=True, path_type=Path),
    help="2500x1686 PNG/JPG",
)
@click.option("--keep-existing", is_flag=True, help="不刪除既有 Rich Menu")
def richmenu_setup(image_path: Path, keep_existing: bool) -> None:
    rich_menu_id = RichMenuManager(get_container().line_client).setup_default(
        image_path, replace_existing=not keep_existing
    )
    click.echo(f"Default rich menu set: {rich_menu_id}")


@rbac_cli.command("show")
def rbac_show() -> None:
    click.echo(json.dumps(AccessPolicy.matrix(), ensure_ascii=False, indent=2))


@user_cli.command("set-role")
@click.argument("user_id")
@click.argument("role", type=click.Choice([r.value for r in Role]))
@click.option("--hospital-id", default=None)
def user_set_role(user_id: str, role: str, hospital_id: str | None) -> None:
    """以系統管理員身分指派角色（初始化第一位管理員時使用）。"""
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    user = get_container().users.assign_role(actor, user_id, Role(role), hospital_id)
    click.echo(f"{user.id} -> {user.role.value} ({user.hospital_id})")


@hospital_cli.command("add")
@click.argument("hospital_id")
@click.argument("name")
@click.option("--region", default=None, help="縣市 / 區域")
@click.option("--phone", default=None, help="聯絡電話")
def hospital_add(hospital_id: str, name: str, region: str | None, phone: str | None) -> None:
    """新增院所（初始化時使用；之後可由 SUPER_ADMIN 在後台「院所設定」新增）。"""
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    hospital = get_container().hospitals.create(
        actor, {"id": hospital_id, "name": name, "region": region, "contact_phone": phone}
    )
    click.echo(f"{hospital['id']}: {hospital['name']}")


@hospital_cli.command("import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--only-hospitals", is_flag=True, help="略過診所，只匯入醫院")
def hospital_import(csv_path: Path, only_hospitals: bool) -> None:
    """從 CSV 匯入院所（自家範本或健保署特約醫事機構格式）。"""
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    rows = _read_csv(csv_path)
    result = get_container().hospitals.import_rows(actor, rows, only_hospitals=only_hospitals)
    click.echo(
        f"新增 {result['created']}、更新 {result['updated']}、略過 {result['skipped']}、"
        f"錯誤 {len(result['errors'])}"
    )
    for err in result["errors"][:20]:
        click.echo(f"  第 {err['row']} 列：{err['message']}")


@hospital_cli.command("list")
def hospital_list() -> None:
    for h in get_container().hospitals.list(active_only=False):
        status = "" if h["active"] else "（已停用）"
        click.echo(f"{h['id']:<12} {h['name']}{status}  {h.get('region') or ''}")


@pharmacy_cli.command("import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
def pharmacy_import(csv_path: Path) -> None:
    """匯入藥局：健保署「特約醫事機構」（只取藥局列）或衛福部「藥局基本資料」。

    範例：
      flask --app wsgi pharmacy import data/pharmacy/A21030000I-D21005-001.csv
      flask --app wsgi pharmacy import data/pharmacy/35_2.csv
    """
    result = get_container().pharmacies.import_pharmacies(_read_csv(csv_path))
    click.echo(f"新增 {result['created']}、更新 {result['updated']}、略過 {result['skipped']}")


@pharmacy_cli.command("stats")
def pharmacy_stats() -> None:
    stats = get_container().pharmacies.stats()
    click.echo(f"藥局 {stats['pharmacies']} 筆、藥品 {stats['drugs']} 筆")


@pharmacy_cli.command("find")
@click.argument("address")
def pharmacy_find(address: str) -> None:
    """依地址（縣市 + 鄉鎮市區）測試找藥局，例如：flask pharmacy find 台北市松山區"""
    result = get_container().pharmacies.find_by_address(address)
    click.echo(f"{result['city']}{result['district'] or ''}（{result['scope']}）")
    for p in result["pharmacies"]:
        flag = "營業中" if p["open_now"] else "非營業" if p["open_now"] is False else "?"
        click.echo(f"  {p['name']:<16} {flag:<4} {p['address']}  {p['phone'] or ''}")


@drug_cli.command("import")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
def drug_import(csv_path: Path) -> None:
    """匯入健保用藥品項（data/pharmacy/A21030000I-E41001-001.csv）；同代號只留最新一列。"""
    result = get_container().pharmacies.import_drugs(_read_csv(csv_path))
    click.echo(f"新增 {result['created']}、更新 {result['updated']}、略過 {result['skipped']}")


@reminder_cli.command("run-due")
@click.option("--now", "now_text", default=None, help="模擬時間（ISO 8601，含時區），預設現在")
def reminder_run_due(now_text: str | None) -> None:
    """推播到期的服藥提醒與慢箋領藥提醒。外部 cron 每 5 分鐘執行一次。"""
    now = datetime.fromisoformat(now_text) if now_text else None
    summary = get_container().reminders.run_due(now)
    click.echo(
        f"已送 {summary['sent']}、略過 {summary['skipped']}、失敗 {summary['failed']}、"
        f"慢箋提醒 {summary['refill_sent']}"
    )


@triage_cli.command("poll")
@click.option("--loop", is_flag=True, help="常駐執行（沒有 cron 時用），每 --interval 秒抓一次")
@click.option("--interval", default=900, show_default=True, help="--loop 的間隔秒數")
def triage_poll(loop: bool, interval: int) -> None:
    """抓取健保署急診即時資料 → 存快照 → 評估壅塞 → 通知訂閱者。cron 每 15 分鐘執行一次。"""
    from app.core.exceptions import ExternalServiceError

    while True:
        try:
            summary = get_container().triage.poll()
            click.echo(
                f"{datetime.now():%Y-%m-%d %H:%M:%S} 抓到 {summary['fetched']} 家、新增 "
                f"{summary['added']} 筆（{summary['sysdate']}）、清除 {summary['purged']}；"
                f"建議 開立 {summary['opened']} / 更新 {summary['updated']} / 解除 "
                f"{summary['resolved']}，通知 {summary['notified']} 則"
            )
        except ExternalServiceError as exc:
            click.echo(f"{datetime.now():%Y-%m-%d %H:%M:%S} 抓取失敗：{exc.message}", err=True)
            if not loop:
                raise SystemExit(1) from exc
        if not loop:
            return
        time.sleep(max(60, interval))


@triage_cli.command("board")
@click.option("--area", default=None, help="地區代碼（01 台北市…）或縣市名稱；預設全國")
def triage_board(area: str | None) -> None:
    """印出目前急診看板（會在快照過舊時即時補抓）。"""
    from app.domain.triage_rules import AREA_CODES, LEVEL_EMOJI

    code = AREA_CODES.get((area or "").replace("臺", "台"), area)
    board = get_container().triage.board(area_code=code)
    stale = "（資料可能過舊）" if board["stale"] else ""
    click.echo(f"更新 {board['reported_at_label'] or '—'}{stale}")
    for h in board["hospitals"]:
        flag = " 119滿床" if h["full_119"] else ""
        click.echo(
            f"  {LEVEL_EMOJI[h['level']]} {h['area_name']} {h['hospital_name']:<10}"
            f"（{h['tier_label']}）看診 {h['wait_see']:>3} 住院 {h['wait_admit']:>3} "
            f"ICU {h['wait_icu']:>2}{flag}"
        )


@triage_cli.command("advisories")
@click.option("--all", "show_all", is_flag=True, help="含已解除")
def triage_advisories(show_all: bool) -> None:
    """列出分流建議。"""
    rows = get_container().triage.list_advisories(status=None if show_all else "open")
    if not rows:
        click.echo("目前沒有分流建議")
    for a in rows:
        click.echo(
            f"[{a['status']}] {a['area_name']} 開立 {a['opened_at']} 通知 {a['notify_count']} 次"
        )
        click.echo(a["report"])
        click.echo("")


@knowledge_cli.command("import")
@click.argument("path", type=click.Path(exists=True, path_type=Path))
@click.option("--hospital-id", default=None, help="匯成某院所專屬文件；預設通用")
def knowledge_import(path: Path, hospital_id: str | None) -> None:
    """匯入 Markdown（單檔或整個目錄），同檔名重匯會更新。

    範例：flask knowledge import data/knowledge
    """
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    result = get_container().knowledge.import_path(actor, path, hospital_id=hospital_id)
    click.echo(f"新增 {result['created']}、更新 {result['updated']}、錯誤 {len(result['errors'])}")
    for err in result["errors"]:
        click.echo(f"  {err['file']}：{err['message']}")


@knowledge_cli.command("list")
def knowledge_list() -> None:
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    for doc in get_container().knowledge.list(actor):
        scope = doc["hospital_id"] or "通用"
        status = "" if doc["active"] else "（停用）"
        click.echo(f"{doc['id'][:8]}  {doc['category_label']:<6} {scope:<8} {doc['title']}{status}")


@knowledge_cli.command("search")
@click.argument("query")
def knowledge_search(query: str) -> None:
    """試搜：看某句話會檢索到哪些片段（關鍵字 + 語意混合排序）。"""
    from app.domain.retrieval import hit_label

    for index, hit in enumerate(get_container().knowledge.retrieve(query), start=1):
        click.echo(f"[K{index}] {hit_label(hit)}（{hit['score']}）\n{hit['content'][:200]}\n")


@knowledge_cli.command("import-health-education")
@click.argument("jsonl_path", type=click.Path(exists=True, path_type=Path))
@click.option("--min-chars", default=200, show_default=True, help="內文最少字數")
@click.option("--all", "include_all", is_flag=True, help="連沒有疾病主題分類的文章也匯入")
@click.option("--limit", default=None, type=int, help="最多匯入幾篇（測試用）")
@click.option("--embed", is_flag=True, help="匯入後立即計算向量（需 EMBEDDING_MODEL）")
def knowledge_import_health_education(
    jsonl_path: Path, min_chars: int, include_all: bool, limit: int | None, embed: bool
) -> None:
    """匯入醫院衛教內文（data/衛教內文資料/health_education_content.jsonl）為通用知識文件。

    可重跑：已匯入的網址與內文重複的文章會略過。
    """
    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    knowledge = get_container().knowledge
    result = knowledge.import_health_education(
        actor,
        jsonl_path,
        min_chars=min_chars,
        classified_only=not include_all,
        limit=limit,
        progress=lambda created, read: click.echo(f"  已匯入 {created} 篇（讀取 {read} 行）"),
    )
    click.echo(
        f"新增 {result['created']}、略過 {result['skipped']}、重複 {result['duplicates']}"
        f"（共讀取 {result['read']} 行）"
    )
    if embed:
        click.echo(f"向量計算完成 {knowledge.embed_pending()} 個片段")


@knowledge_cli.command("embed")
@click.option("--batch", default=200, show_default=True)
@click.option("--limit", default=None, type=int, help="本次最多處理幾個片段")
def knowledge_embed(batch: int, limit: int | None) -> None:
    """替尚未有向量的片段計算 OpenAI embedding（可重複執行、可中斷）。"""
    done = get_container().knowledge.embed_pending(batch=batch, limit=limit)
    click.echo(f"完成 {done} 個片段")


@knowledge_cli.command("stats")
def knowledge_stats() -> None:
    stats = get_container().knowledge.stats()
    click.echo(
        f"文件 {stats['documents']} 篇、片段 {stats['chunks']} 個、已有向量 {stats['embedded']} 個"
    )


@assistant_cli.command("ask")
@click.argument("text")
@click.option("--user-id", default=None, help="以某位使用者的身分（帶入綁定狀態 / 住址）")
def assistant_ask(text: str, user_id: str | None) -> None:
    """本機直接問 AI 助理，印出 prompt 脈絡摘要與決策 JSON（不經 LINE）。"""
    container = get_container()
    user = container.user_repo.get_by_id(user_id) if user_id else None
    principal = (
        container.users.to_principal(user) if user else Principal(user_id=None, role=Role.GUEST)
    )
    context = container.conversation.build_context(principal, text)
    click.echo(f"知識片段 {len(context.hits)} 個：{[h['title'] for h in context.hits]}")
    for block in context.live_blocks:
        click.echo(block)
    decision = container.conversation.decide(principal, text)
    click.echo(json.dumps(decision.model_dump(), ensure_ascii=False, indent=2))


def register_cli(app: Flask) -> None:
    for group in (
        richmenu_cli,
        rbac_cli,
        user_cli,
        hospital_cli,
        pharmacy_cli,
        drug_cli,
        reminder_cli,
        triage_cli,
        knowledge_cli,
        assistant_cli,
    ):
        app.cli.add_command(group)
