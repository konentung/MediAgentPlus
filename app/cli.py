"""Flask CLI 指令：richmenu setup / rbac show / user set-role / hospital add|list"""

from __future__ import annotations

import json
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
    import csv

    actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
    try:
        text = csv_path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = csv_path.read_text(encoding="big5", errors="replace")
    rows = list(csv.DictReader(text.splitlines()))
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


def register_cli(app: Flask) -> None:
    for group in (richmenu_cli, rbac_cli, user_cli, hospital_cli):
        app.cli.add_command(group)
