"""把資料夾裡的衛教 PDF 直接匯入衛教資訊（知識庫）並計算向量，不經過瀏覽器上傳。

用法（在專案根目錄、已啟用 conda 環境）：

    python scripts/import_pdfs.py                      # 匯入 data/ 下所有 PDF，歸屬「通用」
    python scripts/import_pdfs.py data/心臟科 --department 心臟內科
    python scripts/import_pdfs.py data --hospital 0401180014 --chunk-size large
    python scripts/import_pdfs.py data --no-embed      # 只匯入、切段，不算向量（不呼叫 OpenAI）

跟後台「資料匯入 → 衛教資訊（PDF）」走同一套邏輯（`KnowledgeService.import_pdfs()`）：
一個 PDF 一篇文件、標題取檔名、同範圍同檔名重跑就是更新，所以可以重複執行。
用 `.env` 的設定連資料庫；算向量會呼叫 OpenAI（`EMBEDDING_MODEL`），需要 `OPENAI_API_KEY`。
掃描影像檔（沒有文字層）讀不到字，會列在最後的「無法匯入」清單。
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import create_app  # noqa: E402
from app.core.exceptions import AppError  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.domain.principal import Principal  # noqa: E402
from app.services.knowledge_service import CHUNK_SIZES, DEFAULT_CHUNK_SIZE  # noqa: E402
from config import get_config  # noqa: E402

EMBED_BATCH = 200


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把資料夾裡的衛教 PDF 匯入衛教資訊並計算向量")
    parser.add_argument(
        "folder", nargs="?", default="data", help="PDF 所在資料夾（含子資料夾），預設 data"
    )
    parser.add_argument("--hospital", default=None, help="歸屬的院所代碼；不給 = 通用（所有院所）")
    parser.add_argument("--department", default=None, help="整批套用的科別（可不給）")
    parser.add_argument(
        "--chunk-size",
        default=DEFAULT_CHUNK_SIZE,
        choices=list(CHUNK_SIZES),
        help="片段大小：small 300 / medium 400 / large 600 字",
    )
    parser.add_argument("--no-embed", action="store_true", help="不計算向量（不呼叫 OpenAI）")
    return parser.parse_args()


def find_pdfs(folder: Path) -> list[tuple[Path, str]]:
    """資料夾（含子資料夾）裡的 PDF → (路徑, 檔名)；同檔名只留第一個並提醒。"""
    files: dict[str, Path] = {}
    for path in sorted(folder.rglob("*")):
        if not (path.is_file() and path.suffix.lower() == ".pdf"):
            continue
        if path.name in files:
            print(f"  ! 檔名重複，略過 {path}（已有 {files[path.name]}）")
            continue
        files[path.name] = path
    return [(path, name) for name, path in files.items()]


def embed_all(knowledge: Any, hospital_id: str | None) -> int:
    """替這個範圍還沒有向量的片段計算向量，回傳完成數。"""
    done = 0
    while count := knowledge.embed_pending(
        batch=EMBED_BATCH, limit=EMBED_BATCH, hospital_ids=[hospital_id]
    ):
        done += count
        print(f"  向量 {done} 個", flush=True)
    return done


def main() -> int:
    args = parse_args()
    folder = Path(args.folder)
    if not folder.is_dir():
        print(f"找不到資料夾：{folder}")
        return 1

    # 這是一次性的匯入，不需要（也不應該）順便啟動服藥提醒 / 急診資料的排程
    get_config().SCHEDULER_ENABLED = False
    logging.getLogger("pypdf").setLevel(logging.ERROR)  # 字型、座標之類的解析警告很吵，不影響結果
    app = create_app()

    with app.app_context():
        knowledge = app.extensions["container"].knowledge
        files = find_pdfs(folder)
        if not files:
            print(f"{folder} 裡沒有 PDF")
            return 1
        scope = args.hospital or "通用"
        print(
            f"匯入 {len(files)} 個 PDF → {scope}"
            f"（科別 {args.department or '未指定'}、片段 {args.chunk_size}）"
        )

        def progress(summary: dict[str, Any]) -> None:
            done = summary["created"] + summary["updated"] + len(summary["errors"])
            print(
                f"  [{done}/{len(files)}] 新增 {summary['created']}・更新 {summary['updated']}"
                f"・片段 {summary['chunks']}・失敗 {len(summary['errors'])}",
                flush=True,
            )

        actor = Principal(user_id="system", role=Role.SUPER_ADMIN)
        try:
            result = knowledge.import_pdfs(
                actor,
                files,
                hospital_id=args.hospital,
                department=args.department,
                chunk_size=args.chunk_size,
                progress=progress,
            )
        except AppError as exc:  # 院所代碼不存在、科別太長等參數問題
            print(f"匯入失敗：{exc.message}")
            return 1

        embedded = 0
        if args.no_embed:
            print("略過向量計算（--no-embed）；之後可在後台「資料匯入」按「計算向量」")
        else:
            print("計算向量…")
            try:
                embedded = embed_all(knowledge, args.hospital)
            except AppError as exc:
                print(
                    f"  向量沒有算完：{exc.message}"
                    "（文件與片段已存好，關鍵字檢索可用；修正後重跑即可補算）"
                )

        print(
            f"\n完成：新增 {result['created']} 篇、更新 {result['updated']} 篇、"
            f"片段 {result['chunks']} 個、向量 {embedded} 個"
        )
        if result["errors"]:
            print(f"無法匯入 {len(result['errors'])} 個：")
            for error in result["errors"]:
                print(f"  - {error['file']}：{error['message']}")
        stats = knowledge.stats(hospital_ids=[args.hospital])
        print(
            f"{scope} 目前共有文件 {stats['documents']} 篇、片段 {stats['chunks']} 個、"
            f"已有向量 {stats['embedded']} 個"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
