import os
import tempfile
from io import BytesIO

from django.conf import settings
from django.http import HttpResponse
from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage

from passport.image_support import open_image

# Excel 导出层只负责把查询结果转换为下载响应；图片路径解析和临时缩略图生成也集中在这里。
# 导出失败时尽量跳过单张损坏图片，让其他客人的数据仍能完成导出。


# =========================================================
# 工具：解析护照图片绝对路径（仅使用 MEDIA_ROOT）
# =========================================================
def _resolve_passport_abs_path(file_path: str) -> str | None:
    # file_path 可能是：
    # - 仅文件名：2025xxxx.jpg
    # - 相对路径：passports/2025xxxx.jpg
    # - 绝对路径（位于 MEDIA_ROOT 下）
    if not file_path:
        return None
    file_path = str(file_path).strip()
    if not file_path:
        return None
    passport_root = getattr(settings, "MEDIA_ROOT", None)
    if not passport_root:
        return None
    # 只接受已有文件；候选路径均从 MEDIA_ROOT 派生，避免导出任意服务器文件。
    if os.path.isabs(file_path) and os.path.exists(file_path):
        return file_path
    filename = os.path.basename(file_path)
    candidates = [
        os.path.join(passport_root, "passports", filename),
        os.path.join(passport_root, file_path.lstrip("/")),
    ]
    for p in candidates:
        if os.path.exists(p):
            return p
    return None


# =========================================================
# 工具：生成 Excel 兼容的 PNG 缩略图
# =========================================================
def _make_thumb_png(abs_path: str, max_size=(220, 140)) -> str:
    # Excel 对 JPEG/HEIC 等格式兼容不稳定
    # 统一转为 RGB PNG，并生成临时文件
    with open_image(abs_path) as source:
        img = source.convert("RGB")
        img.thumbnail(max_size)
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        img.save(tmp.name, format="PNG")
        tmp.close()
    return tmp.name


# =========================================================
# 导出：客人信息 + 护照缩略图到 Excel
# =========================================================
def export_guests_with_passports_xlsx(
    guests_qs,
    passports_by_guest: dict[int, list],
    filename: str = "guests.xlsx",
):
    # 表头先根据本批订单中的最大护照数量确定，保证每一列在所有订单中位置一致。
    # 先计算订单中的最大证件数，统一表头列数，避免不同订单的列结构不一致。
    wb = Workbook()
    ws = wb.active
    ws.title = "Guests"
    headers = [
        "ID",
        "姓名",
        "职业",
        "电话",
        "房源",
        "门锁密码",
        "人数",
        "入住日期",
        "退房日期",
        "创建时间",
        "护照数量",
    ]
    max_passports = 0
    for plist in passports_by_guest.values():
        if plist:
            max_passports = max(max_passports, len(plist))
    for i in range(max_passports):
        headers.append(f"护照{i + 1}")
    ws.append(headers)
    ws.column_dimensions["A"].width = 8
    ws.column_dimensions["B"].width = 22
    ws.column_dimensions["C"].width = 24
    ws.column_dimensions["D"].width = 16
    ws.column_dimensions["E"].width = 14
    ws.column_dimensions["F"].width = 12
    ws.column_dimensions["G"].width = 8
    ws.column_dimensions["H"].width = 16
    ws.column_dimensions["I"].width = 16
    ws.column_dimensions["J"].width = 20
    ws.column_dimensions["K"].width = 10
    start_col = 12
    for c in range(start_col, start_col + max_passports):
        col_letter = chr(ord("A") + c - 1)
        if "A" <= col_letter <= "Z":
            ws.column_dimensions[col_letter].width = 30
    row_index = 2
    tmp_files_to_cleanup: list[str] = []
    try:
        for g in guests_qs:
            # 图片作为 Excel 浮动对象插入，临时 PNG 必须保留到工作簿保存完成后。
            passports = passports_by_guest.get(g.id, [])

            created_at = getattr(g, "created_at", None)
            created_at_str = (
                created_at.strftime("%Y-%m-%d %H:%M:%S") if created_at else ""
            )

            check_in = getattr(g, "check_in_date", None)
            check_out = getattr(g, "check_out_date", None)

            ws.cell(row=row_index, column=1, value=getattr(g, "id", None))
            ws.cell(row=row_index, column=2, value=getattr(g, "name", ""))
            ws.cell(row=row_index, column=3, value=getattr(g, "job", ""))
            ws.cell(row=row_index, column=4, value=getattr(g, "phone", ""))
            ws.cell(row=row_index, column=5, value=str(getattr(g, "house", "")))
            ws.cell(row=row_index, column=6, value=getattr(g, "password", ""))
            ws.cell(row=row_index, column=7, value=getattr(g, "occupancy", None))

            # 日期统一写成字符串，避免时区或 Excel 日期格式导致展示偏差。
            ws.cell(
                row=row_index,
                column=8,
                value=check_in.strftime("%Y-%m-%d") if check_in else "",
            )

            # 退房日期（安全写入）
            ws.cell(
                row=row_index,
                column=9,
                value=check_out.strftime("%Y-%m-%d") if check_out else "",
            )

            # 创建时间
            ws.cell(row=row_index, column=10, value=created_at_str)

            # 护照数量
            ws.cell(row=row_index, column=11, value=len(passports))

            ws.row_dimensions[row_index].height = 90
            for idx, p in enumerate(passports):
                abs_path = _resolve_passport_abs_path(getattr(p, "file_path", ""))
                if not abs_path:
                    continue
                try:
                    png_path = _make_thumb_png(abs_path)
                    tmp_files_to_cleanup.append(png_path)
                    img = XLImage(png_path)
                    col = start_col + idx
                    img.anchor = ws.cell(row=row_index, column=col).coordinate
                    ws.add_image(img)
                except Exception:
                    continue
            row_index += 1
        bio = BytesIO()
        wb.save(bio)
        bio.seek(0)
        resp = HttpResponse(
            bio.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = f'attachment; filename="{filename}"'
        return resp
    finally:
        # 响应生成后清理临时图片，避免每次导出在服务器留下垃圾文件。
        for f in tmp_files_to_cleanup:
            try:
                os.remove(f)
            except Exception:
                pass
