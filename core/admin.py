import os
from collections import defaultdict
from datetime import datetime

from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.actions import delete_selected as django_delete_selected
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth.admin import GroupAdmin, UserAdmin
from django.contrib.auth.models import Group, User
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from core.exports.excel_export import export_guests_with_passports_xlsx
from core.exports.zip_export import export_selected_guests_passports_zip
from core.services.ttlock_service import remote_unlock, send_temp_password
from passport.models import Guest, GuestPassport, House, HouseLock, Lock


# =========================================================
# 房源 Inline：锁
# =========================================================
class HouseLockInline(admin.StackedInline):
    model = HouseLock
    extra = 0
    fields = ("lock",)
    verbose_name = "绑定的锁"
    verbose_name_plural = "绑定的锁"


# =========================================================
# 房源过滤器
# =========================================================
class HouseActiveFilter(admin.SimpleListFilter):
    title = "房源状态"
    parameter_name = "house_active"

    def lookups(self, request, model_admin):
        return (
            ("1", "仅上线房源"),
            ("0", "仅下线房源"),
        )

    def queryset(self, request, queryset):
        if self.value() == "1":
            return queryset.filter(house__is_active=True)
        if self.value() == "0":
            return queryset.filter(house__is_active=False)
        return queryset


# =========================================================
# 门锁（Lock）
# =========================================================
@admin.register(Lock)
class LockAdmin(admin.ModelAdmin):
    list_display = (
        "id_display",
        "code",
        "account",
        "remark",
        "unlock_button",
        "blank_col",
    )
    search_fields = ("code", "account", "remark")
    ordering = ("-id",)
    list_per_page = 20
    actions = (
        "send_temp_pwd_action",
        "delete_selected",
    )
    change_list_template = "admin/dialog_send_password.html"

    @admin.display(description="ID")
    def id_display(self, obj):
        return obj.id

    @admin.display(description="远程开锁")
    def unlock_button(self, obj):
        if not obj.code:
            return "—"

        url = reverse(
            "admin:core_lock_unlock",
            args=[obj.pk],
        )

        return format_html(
            '<a class="button" href="{}">开锁</a>',
            url,
        )

    @admin.display(description="")
    def blank_col(self, obj):
        return ""

    @admin.action(description="发放临时密码")
    def send_temp_pwd_action(self, request, queryset):
        if not queryset.exists():
            messages.warning(request, "请先选择门锁。")
            return
        messages.info(request, "请在弹窗中输入密码。")

    @admin.action(description="删除选中门锁")
    def delete_selected(self, request, queryset):
        return django_delete_selected(self, request, queryset)

    def get_urls(self):
        return [
            path(
                "unlock/<int:lock_id>/",
                self.admin_site.admin_view(self.unlock_view),
                name="core_lock_unlock",
            ),
            path(
                "send-temp-password/",
                self.admin_site.admin_view(self.send_temp_password_view),
                name="core_lock_send_temp_password",
            ),
        ] + super().get_urls()

    def unlock_view(self, request, lock_id):
        # 自定义后台路由只负责取本地门锁、调用服务层并反馈消息，具体 API 细节留在 service。
        lock = get_object_or_404(Lock, pk=lock_id)

        result = remote_unlock(
            lock.code,
            lock.account,
        )

        if result.get("success"):
            messages.success(request, "远程开锁成功。")
        else:
            messages.error(
                request,
                result.get("message") or "开锁失败。",
            )

        return redirect(request.META.get("HTTP_REFERER", ".."))

    def send_temp_password_view(self, request):
        # 弹窗以逗号分隔锁 ID；所有选中锁共用密码和日期，但逐锁返回执行结果。
        if request.method != "POST":
            return JsonResponse(
                {"ok": False},
                status=405,
            )

        ids = request.POST.get("ids", "")
        password = request.POST.get("password", "")
        start_date = (request.POST.get("start_date", "") or "").strip()
        end_date = (request.POST.get("end_date", "") or "").strip()

        if not start_date or not end_date:
            return JsonResponse(
                {"ok": False, "error": "请选择开始日期和结束日期。"},
                status=400,
            )

        try:
            start_day = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_day = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return JsonResponse(
                {"ok": False, "error": "日期格式错误，请使用系统日期控件重新选择。"},
                status=400,
            )

        if end_day < start_day:
            return JsonResponse(
                {"ok": False, "error": "结束日期不能早于开始日期。"},
                status=400,
            )

        results = []
        ok_all = True

        for lock in Lock.objects.filter(id__in=[int(x) for x in ids.split(",") if x]):
            # 单锁失败不会阻断其他锁，ok_all 最终反映是否全部成功。
            resp = send_temp_password(
                lock.code,
                password,
                start_date,
                end_date,
                lock.account,
            )

            ok = bool(resp.get("success"))

            ok_all &= ok

            results.append(
                {
                    "id": lock.id,
                    "code": lock.code,
                    "remark": lock.remark,
                    "success": ok,
                    "message": resp.get("message"),
                }
            )

        return JsonResponse({"ok": ok_all, "results": results})


# =========================================================
# 房源（House）
# =========================================================
@admin.register(House)
class HouseAdmin(admin.ModelAdmin):
    list_display = (
        "id_display",
        "code",
        "name",
        "password",
        "city",
        "max_guests",
        "active_flag",
        "blank_col",
    )
    fields = ("code", "name", "password", "city", "max_guests", "is_active")
    search_fields = ("code", "name", "city")
    ordering = ("-id",)
    list_per_page = 20
    inlines = (HouseLockInline,)

    @admin.display(description="ID")
    def id_display(self, obj):
        return obj.id

    @admin.display(description="状态")
    def active_flag(self, obj):
        return "✅" if obj.is_active else "❌"

    @admin.display(description="")
    def blank_col(self, obj):
        return ""


# =========================================================
# 房源-锁
# =========================================================
@admin.register(HouseLock)
class HouseLockAdmin(admin.ModelAdmin):
    list_display = ("id_display", "house", "lock", "blank_col")
    ordering = ("house__code", "lock__code")

    @admin.display(description="ID")
    def id_display(self, obj):
        return obj.id

    @admin.display(description="")
    def blank_col(self, obj):
        return ""


# =========================================================
# 客人 / 订单
# =========================================================
@admin.register(Guest)
class GuestAdmin(admin.ModelAdmin):
    list_display = (
        "id_display",
        "house",
        "phone",
        "password",
        "occupancy",
        "check_in_date",
        "check_out_date",
        "created_at",
        "detail_button",
        "blank_col",
    )
    list_filter = ("house", HouseActiveFilter, "created_at")
    search_fields = ("name", "phone")
    list_display_links = None
    ordering = ("-id",)
    list_per_page = 20
    actions = (
        "export_selected_guests_excel",
        "export_selected_guests_passports_zip_action",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def blank_col(self, obj):
        return ""

    @admin.display(description="ID")
    def id_display(self, obj):
        return obj.id

    @admin.display(description="详情")
    def detail_button(self, obj):
        url = reverse(
            "admin:core_guest_order_detail",
            args=[obj.pk],
        )

        return format_html(
            '<a class="button" href="{}">查看</a>',
            url,
        )

    def get_urls(self):
        urls = super().get_urls()

        custom_urls = [
            path(
                "<int:guest_id>/detail/",
                self.admin_site.admin_view(self.order_detail_view),
                name="core_guest_order_detail",
            ),
        ]

        return custom_urls + urls

    def order_detail_view(self, request, guest_id):
        # 详情页按 ID 查询订单，并按创建顺序展示其全部证件识别记录。
        guest = get_object_or_404(
            Guest,
            pk=guest_id,
        )

        passports = GuestPassport.objects.filter(guest=guest).order_by("id")

        context = {
            **self.admin_site.each_context(request),
            "guest": guest,
            "passports": passports,
            "title": "订单详情",
        }

        return render(
            request,
            "admin/guest_order_detail.html",
            context,
        )

    @admin.action(description="导出客人 Excel")
    def export_selected_guests_excel(self, request, queryset):
        # 先按 guest_id 建索引，再交给导出层生成工作簿，避免导出层访问数据库关系。
        passports_qs = GuestPassport.objects.filter(guest__in=queryset).order_by(
            "guest_id", "id"
        )

        passports_by_guest = {}

        for p in passports_qs:
            passports_by_guest.setdefault(
                p.guest_id,
                [],
            ).append(p)

        return export_guests_with_passports_xlsx(
            queryset,
            passports_by_guest,
        )

    @admin.action(description="下载护照 Zip")
    def export_selected_guests_passports_zip_action(self, request, queryset):
        # Zip 导出使用原图；缩略图只服务于后台和 Excel 展示。
        passports_by_guest = defaultdict(list)

        for p in GuestPassport.objects.filter(guest__in=queryset).order_by(
            "guest_id", "id"
        ):
            passports_by_guest[p.guest_id].append(p)

        return export_selected_guests_passports_zip(
            guests_qs=queryset,
            passports_by_guest=passports_by_guest,
            filename="passports.zip",
        )

    def _delete_passport_files(self, guest):
        # Django 删除 ImageField 记录不会可靠地删除自定义媒体文件，因此显式清理原图。
        base_root = getattr(settings, "MEDIA_ROOT", None)

        if not base_root:
            return

        for passport in guest.passports.all():
            if not passport.file_path:
                continue

            rel_path = passport.file_path.name

            if not rel_path:
                continue

            orig_path = os.path.join(
                base_root,
                rel_path,
            )

            try:
                if os.path.isfile(orig_path):
                    os.remove(orig_path)
            except Exception:
                pass

    def delete_model(self, request, obj):
        self._delete_passport_files(obj)

        super().delete_model(
            request,
            obj,
        )

    def delete_queryset(self, request, queryset):
        # 批量删除前逐个清理文件，再让 Django 删除订单及级联的证件记录。
        for guest in queryset:
            self._delete_passport_files(guest)

        super().delete_queryset(
            request,
            queryset,
        )


# =========================================================
# 管理员日志
# =========================================================
@admin.register(LogEntry)
class AdminLogAdmin(admin.ModelAdmin):
    list_display = (
        "action_time",
        "user",
        "content_type",
        "object_link",
        "action_flag_display",
        "short_message",
        "blank_col",
    )
    list_filter = ("action_flag", "user", "content_type", "action_time")
    search_fields = ("object_repr", "change_message")
    ordering = ("-action_time",)
    list_per_page = 20
    actions = ("clear_admin_logs",)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="")
    def blank_col(self, obj):
        return ""

    @admin.display(description="对象")
    def object_link(self, obj):
        if obj.action_flag == DELETION:
            return obj.object_repr

        try:
            url = reverse(
                f"admin:{obj.content_type.app_label}_{obj.content_type.model}_change",
                args=[obj.object_id],
            )

            return format_html(
                '<a href="{}">{}</a>',
                url,
                obj.object_repr,
            )

        except NoReverseMatch:
            return obj.object_repr

    @admin.display(description="操作")
    def action_flag_display(self, obj):
        return {
            ADDITION: "新增",
            CHANGE: "修改",
            DELETION: "删除",
        }.get(
            obj.action_flag,
            obj.action_flag,
        )

    @admin.display(description="说明")
    def short_message(self, obj):
        msg = obj.get_change_message() or ""

        return msg if len(msg) <= 60 else msg[:57] + "..."

    @admin.action(description="清空日志")
    def clear_admin_logs(self, request, queryset):
        if not queryset.exists():
            queryset = self.get_queryset(request)

        count = queryset.count()

        queryset.delete()

        self.message_user(
            request,
            f"已清空 {count} 条操作日志。",
            level=messages.SUCCESS,
        )


# =========================================================
# 管理员，为了末尾加空列
# =========================================================
admin.site.unregister(User)


@admin.register(User)
class UserAdmin(UserAdmin):
    list_display = UserAdmin.list_display + ("blank_col",)

    @admin.display(description="")
    def blank_col(self, obj):
        return ""


# =========================================================
# 管理员分组，为了末尾加空列
# =========================================================
admin.site.unregister(Group)


@admin.register(Group)
class GroupAdmin(GroupAdmin):
    list_display = GroupAdmin.list_display + ("blank_col",)

    @admin.display(description="")
    def blank_col(self, obj):
        return ""
