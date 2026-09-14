import os
import re
import tempfile
import zipfile

from django.http import HttpResponse

from core.exports.excel_export import _resolve_passport_abs_path

# ZIP 导出保留护照原图，目录按客人区分；文件名经过清理，避免特殊字符破坏压缩包目录结构。

# =========================================================
# 路径安全：替换 zip 内路径/文件名的非法字符
# =========================================================
_INVALID_PATH_CHARS = re.compile(r"[\\/:*?\"<>|]+")


def _safe_dirname(name: str, fallback: str = "guest") -> str:
    # 把客人姓名转成 zip 内可用的目录名，避免特殊字符导致解压/跨平台问题
    cleaned = (name or "").strip() or fallback
    cleaned = _INVALID_PATH_CHARS.sub("_", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:80] or fallback


def export_selected_guests_passports_zip(
    *, guests_qs, passports_by_guest: dict[int, list], filename: str = "passports.zip"
):
    # 使用可自动落盘的临时文件，数据量较小时节省磁盘访问，数据量较大时避免占满内存。
    # 生成 zip：按客人分目录保存护照原图
    # 目录结构示例：
    # 张三_12/passport_01_20250101.jpg
    # 张三_12/passport_02_20250102.jpg

    # 大文件优先落盘，避免内存占用过高
    tmp = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)

    with zipfile.ZipFile(tmp, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for guest in guests_qs:
            # 以数据库 ID 区分同名客人；没有证件的客人不创建空目录。
            passports = passports_by_guest.get(guest.id, [])
            if not passports:
                continue

            guest_name = getattr(guest, "name", "") or "guest"
            dir_name = f"{_safe_dirname(guest_name)}_{guest.id}"

            for idx, passport in enumerate(passports, start=1):
                # 只打包 MEDIA_ROOT 中已存在的原图，缺失文件不阻断其他客人的导出。
                file_path = getattr(passport, "file_path", "") or ""
                abs_path = _resolve_passport_abs_path(file_path)
                if not abs_path or not os.path.exists(abs_path):
                    continue

                base = _INVALID_PATH_CHARS.sub("_", os.path.basename(abs_path))
                arcname = f"{dir_name}/passport_{idx:02d}_{base}"

                try:
                    zf.write(abs_path, arcname=arcname)
                except Exception:
                    # 单张文件写入失败时直接跳过，不影响整体导出
                    continue

    tmp.seek(0)
    resp = HttpResponse(tmp.read(), content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{filename}"'
    return resp
