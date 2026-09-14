"""Flask CLI 指令：flask richmenu setup / flask rbac show / flask user set-role"""

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


def register_cli(app: Flask) -> None:
    for group in (richmenu_cli, rbac_cli, user_cli):
        app.cli.add_command(group)
