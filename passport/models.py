import os
import uuid
from datetime import date

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.deconstruct import deconstructible


# =========================================================
# 通用：GUID 文件命名
# =========================================================
@deconstructible
class UploadWithGuid:
    def __init__(self, prefix: str):
        self.prefix = prefix

    def __call__(self, instance, filename):
        # 不使用用户上传的原文件名，避免重名覆盖和特殊字符引起的路径问题。
        # 保留扩展名是为了让 Django、图片处理库和下载端继续识别文件格式。
        ext = os.path.splitext(filename)[1].lower()
        return f"{self.prefix}/{uuid.uuid4().hex}{ext}"


def upload_with_guid(prefix: str):
    return UploadWithGuid(prefix)


# =========================================================
# 智能锁
# =========================================================
class Lock(models.Model):
    code = models.CharField(
        "门锁编号",
        max_length=10,
        unique=True,
    )
    account = models.CharField(
        "通通账号",
        max_length=100,
        blank=True,
    )
    remark = models.CharField(
        "备注",
        max_length=100,
        blank=True,
        default="",
    )

    class Meta:
        db_table = "locks"
        verbose_name = "门锁"
        verbose_name_plural = "门锁管理"

    def __str__(self):
        return f"{self.remark}({self.code})".strip()


# =========================================================
# 房源
# =========================================================
class House(models.Model):
    code = models.CharField(
        "编码",
        max_length=10,
        unique=True,
        help_text="房源编码",
    )
    name = models.CharField(
        "名称",
        max_length=20,
        help_text="房源名称",
    )
    password = models.CharField(
        "密码",
        max_length=20,
        default="0",
        help_text="入户密码；为 0 表示需要动态下发",
    )
    city = models.CharField(
        "城市",
        max_length=20,
        default="",
        help_text="城市",
    )
    cover = models.CharField(
        "封面",
        max_length=200,
        blank=True,
        null=True,
        help_text="封面图片 URL",
    )
    price = models.IntegerField(
        "定价",
        default=16000,
        help_text="每晚价格",
    )
    rooms = models.IntegerField(
        "卧室数量",
        default=2,
    )
    living = models.IntegerField(
        "客厅数量",
        default=1,
    )
    baths = models.IntegerField(
        "厕所数量",
        default=1,
    )
    beds = models.IntegerField(
        "床位",
        default=2,
    )
    kitchen = models.BooleanField(
        "有无厨房",
        default=True,
    )
    tub = models.BooleanField(
        "有无浴缸",
        default=True,
    )
    max_guests = models.IntegerField(
        "容纳人数",
        default=4,
    )
    cleaning_fee = models.IntegerField(
        "清洁费",
        default=5000,
    )
    remark = models.CharField(
        "备注",
        max_length=200,
        default="",
    )
    amenities = models.CharField(
        "配置",
        max_length=200,
        default="",
    )
    is_active = models.BooleanField(
        "是否可用",
        default=True,
        db_index=True,
    )

    class Meta:
        db_table = "houses"
        verbose_name = "民宿"
        verbose_name_plural = "民宿管理"

    def __str__(self):
        return f"{self.name}({self.code})".strip()


# =========================================================
# 房源 - 智能锁 关联
# =========================================================
class HouseLock(models.Model):
    # 一个房源可以绑定多把锁；唯一约束防止同一把锁被重复绑定到同一房源。
    house = models.ForeignKey(
        House,
        on_delete=models.CASCADE,
        db_column="house_id",
        related_name="house_locks",
        verbose_name="民宿",
    )
    lock = models.ForeignKey(
        Lock,
        on_delete=models.CASCADE,
        db_column="lock_id",
        related_name="lock_houses",
        verbose_name="门锁",
    )

    class Meta:
        db_table = "house_locks"
        unique_together = ("house", "lock")
        verbose_name = "房锁绑定"
        verbose_name_plural = "房锁绑定"

    def __str__(self):
        return f"{self.house} - {self.lock}"


# =========================================================
# 客人 / 订单
# =========================================================
class Guest(models.Model):
    # Guest 同时保存入住资料和门锁下发结果，便于后台追踪订单与远程调用状态。
    order_no = models.CharField(
        "订单编号",
        max_length=32,
        unique=True,
        null=True,
        blank=True,
    )
    house = models.ForeignKey(
        House,
        on_delete=models.CASCADE,
        db_column="house_id",
        verbose_name="民宿",
    )
    name = models.CharField(
        "客人姓名",
        max_length=80,
    )
    job = models.CharField(
        "从事职业",
        max_length=50,
    )
    phone = models.CharField(
        "电话号码",
        max_length=40,
    )
    password = models.CharField(
        "入户密码",
        max_length=10,
    )
    password_source = models.CharField(
        "密码来源",
        max_length=20,
        default="house",
    )
    occupancy = models.IntegerField(
        "入住人数",
        default=0,
    )
    check_in_date = models.DateField(
        "入住日期",
        default=date.today,
    )
    check_out_date = models.DateField(
        "退房日期",
        default=date.today,
    )
    source = models.CharField(
        "订单来源",
        max_length=20,
        default="web",
    )
    total_amount = models.IntegerField(
        "订单总金额（日元）",
        default=0,
    )
    paid_at = models.DateTimeField(
        "支付完成时间",
        null=True,
        blank=True,
    )
    status = models.CharField(
        "订单状态",
        max_length=20,
        default="pending",
        db_index=True,
    )
    created_at = models.DateTimeField(
        "创建时间",
        default=timezone.now,
    )
    ttlock_message = models.CharField(
        "TTLock 消息",
        max_length=255,
        default="",
    )
    ttlock_payload = models.JSONField(
        "TTLock 返回",
        default=dict,
    )
    ttlock_status = models.CharField(
        "TTLock 状态",
        max_length=20,
        default="not_required",
    )

    class Meta:
        db_table = "guests"
        verbose_name = "客人"
        verbose_name_plural = "客人列表"

    def __str__(self):
        return self.order_no or f"Guest #{self.id}"


# =========================================================
# 客人护照
# =========================================================
class GuestPassport(models.Model):
    # 每张上传图片单独保存识别结果，允许一个订单包含多位客人的证件照片。
    guest = models.ForeignKey(
        Guest,
        on_delete=models.CASCADE,
        related_name="passports",
        verbose_name="客人",
    )
    file_path = models.ImageField(
        "护照照片",
        upload_to=upload_with_guid("passports"),
    )
    document_type = models.CharField(
        "证件类型",
        max_length=30,
        default="passport",
    )
    image_index = models.IntegerField(
        "图片序号",
        default=0,
    )
    mrz_raw = models.TextField(
        "MRZ 原文",
        default="",
    )
    original_name = models.CharField(
        "原始姓名",
        max_length=160,
        default="",
    )
    recognition_payload = models.JSONField(
        "识别结果",
        default=dict,
    )
    recognition_status = models.CharField(
        "识别状态",
        max_length=30,
        default="pending",
    )

    class Meta:
        db_table = "guest_passports"
        verbose_name = "护照"
        verbose_name_plural = "护照"

    def __str__(self):
        return f"{self.guest.name} 的护照 {self.id}"

    # =================================================
    # 后台展示用：缩略图 URL
    # =================================================
    def thumb_url(self):
        # 约定：缩略图文件名 = thumb_原文件名
        # 缩略图由上传流程生成，后台列表只拼接 URL，不在请求时重新处理图片。
        if not self.file_path:
            return ""

        name = self.file_path.name
        dir_name, filename = os.path.split(name)
        thumb_name = f"{dir_name}/thumb_{filename}"
        return f"{settings.MEDIA_URL}{thumb_name}"
