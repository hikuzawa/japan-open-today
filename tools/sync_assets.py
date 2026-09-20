"""採用した画像を data/assets/ から static/assets/ に**表示する幅まで縮小して**複写する（S5）。

`data/assets/` は取得物のローカルキャッシュで、`static/` はビルドが dist へ複写する置き場。
画像の実体はどちらも git 管理外にして、`assets.json`（出典・ライセンス・取得日）だけをコミットする。
日次の実行では extract のあと build の前にこれを走らせる。

**なぜ縮小するか**（2026-09-20）: 出典から取った実体は原寸（1280x1912 など）で、1 枚 1MB を
超えるものがある。本番の施設ページの LCP は 8.1 秒で、その大半がこの 1 枚だった
（トップ・エリア・交通は 1.6〜2.3 秒）。写真が出る枠は最大でも 400px 幅（PC の右列）なので、
長辺 900px あれば 2 倍の解像度でも足りる。縦長の写真は幅ではなく高さが効くので、
**幅と高さの両方を 900px の箱に収める**（切り抜かず、縦横比はそのまま）。

**縮小してもライセンスと出典は変わらない**。クレジット（作者・ライセンス名・出典ページ・取得日）は
`assets.json` から描くので、この処理は触らない。CC BY 系は改変を許しており、表示のための縮小に
追加の表記は要らない（切り抜きはしない。縦横比はそのまま）。原寸は `data/assets/` に残る。

使い方: uv run python -m tools.sync_assets [--edge 900] [--quality 80]
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from PIL import Image
from sitemill.assets import AssetStore
from sitemill.settings import Workspace

# 長辺の上限。900px の箱に収める（横長は 900x600、縦長は 603x900 あたりになる）
MAX_EDGE = 900
JPEG_QUALITY = 80
# 縮小できる形式。ほかの形式（gif など）はそのまま複写する
RESIZABLE = {".jpg", ".jpeg", ".png", ".webp"}


def fit_box(
    src: Path, dest: Path, *, max_edge: int = MAX_EDGE, quality: int = JPEG_QUALITY
) -> bool:
    """`src` を一辺 `max_edge` の箱に収めて `dest` に書く。縮小したときだけ True。

    縦横比は変えない（切り抜かない）。もとから箱に収まる画像は再エンコードせずに
    そのまま複写する（画質を落とさない）。
    """
    if src.suffix.lower() not in RESIZABLE:
        shutil.copy2(src, dest)
        return False
    with Image.open(src) as im:
        if im.width <= max_edge and im.height <= max_edge:
            shutil.copy2(src, dest)
            return False
        scale = min(max_edge / im.width, max_edge / im.height)
        size = (round(im.width * scale), round(im.height * scale))
        resized = im.resize(size, Image.LANCZOS)
        fmt = (im.format or "").upper()
        if fmt in ("JPEG", "MPO"):
            resized.convert("RGB").save(
                dest, "JPEG", quality=quality, optimize=True, progressive=True
            )
        elif fmt == "PNG":
            resized.save(dest, "PNG", optimize=True)
        elif fmt == "WEBP":
            resized.save(dest, "WEBP", quality=quality, method=6)
        else:
            shutil.copy2(src, dest)
            return False
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--edge", type=int, default=MAX_EDGE, help="複写する画像の長辺の上限")
    parser.add_argument("--quality", type=int, default=JPEG_QUALITY, help="JPEG / WebP の画質")
    args = parser.parse_args()

    ws = Workspace.open(Path.cwd())
    root = ws.data_dir / "assets"
    dest = ws.static_dir / "assets"
    dest.mkdir(parents=True, exist_ok=True)
    copied = skipped = shrunk = 0
    before = after = 0
    keep: set[str] = set()
    for directory in sorted(p for p in root.iterdir() if p.is_dir()) if root.is_dir() else []:
        for asset in AssetStore(root, directory.name).load().values():
            if not asset.usable:
                skipped += 1  # ライセンスを説明できないものは出さない
                continue
            if not asset.local_path.is_file():
                skipped += 1
                continue
            target = dest / asset.local_path.name
            keep.add(target.name)
            source = asset.local_path.stat()
            # 作り直すのは、無いとき・原寸のほうが新しいとき・まだ縮小していない
            # （原寸と同じ大きさで置いてある）とき
            stale = (
                not target.exists()
                or target.stat().st_mtime < source.st_mtime
                or target.stat().st_size == source.st_size
            )
            if stale and fit_box(
                asset.local_path, target, max_edge=args.edge, quality=args.quality
            ):
                shrunk += 1
            before += source.st_size
            after += target.stat().st_size
            copied += 1
    # 採用をやめた画像の実体が残ると、そのまま dist に載って配信される（原寸のままのものもある）
    removed = 0
    for path in dest.iterdir():
        if path.is_file() and path.name not in keep:
            path.unlink()
            removed += 1
    saved = before - after
    if removed:
        print(f"もう使っていない実体を削除 {removed} 件")
    print(
        f"複写 {copied} 件（うち縮小 {shrunk} 件）/ 見送り {skipped} 件 → {dest}\n"
        f"原寸 {before / 1024 / 1024:.1f}MB → 複写後 {after / 1024 / 1024:.1f}MB"
        f"（{saved / 1024 / 1024:.1f}MB 減、長辺 {args.edge}px まで）"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
