from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from core.services.ttlock_service import (
    delete_keyboard_password,
    list_keyboard_passwords,
)
from passport.models import Lock

# 定时任务入口：读取本地门锁及其账号，分页获取远端密码，只删除超过宽限期的过期记录。
# 通过 --dry-run 可以完整预览筛选结果而不调用删除接口。
DEFAULT_ACCOUNT_KEY = "zhenghe"


def _account_key(lock):
    # 本地门锁可以指定自己的 TTLock 账号；空值沿用项目默认账号。
    return (lock.account or DEFAULT_ACCOUNT_KEY).strip() or DEFAULT_ACCOUNT_KEY


def _to_int(value):
    # 第三方接口可能返回字符串或数字；转换失败时返回 None，交给调用方安全跳过。
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _looks_like_more_pages(raw, page_no, page_size, item_count):
    # 优先使用 API 返回的总页数/总数；旧响应没有分页元数据时按满页推断。
    if not isinstance(raw, dict):
        return item_count >= page_size

    for key in ("pages", "totalPage", "totalPages"):
        pages = _to_int(raw.get(key))
        if pages is not None:
            return page_no < pages

    total = _to_int(raw.get("total"))
    if total is not None:
        return page_no * page_size < total

    return item_count >= page_size


def _keyboard_pwd_id(item):
    # 不同 TTLock API 版本的字段大小写可能不同，按兼容顺序取第一个有效 ID。
    for key in ("keyboardPwdId", "keyboardPwdID", "id"):
        value = item.get(key)
        if value:
            return value
    return None


def _end_ms(item):
    value = _to_int(item.get("endDate"))
    if value is None:
        return None

    # TTLock 通常返回毫秒；兼容秒级时间戳，避免单位不同导致误删或漏删。
    if value < 10_000_000_000:
        value *= 1000
    return value


class Command(BaseCommand):
    help = "清除通通锁中已经过期的键盘密码，可用于宝塔定时任务。"

    def add_arguments(self, parser):
        # argparse 负责把命令行文本转换为 options，handle() 只处理解析后的参数。
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="只显示将要删除的密码，不真正调用删除接口。",
        )
        parser.add_argument(
            "--lock-id",
            dest="lock_ids",
            action="append",
            help="只处理指定的本地门锁 ID，可重复传入。",
        )
        parser.add_argument(
            "--account",
            help="强制使用指定 TTLock 账号配置；默认使用每把锁的通通账号字段，空值使用 zhenghe。",
        )
        parser.add_argument(
            "--page-size",
            type=int,
            default=100,
            help="每次从 TTLock 拉取的密码数量，默认 100。",
        )
        parser.add_argument(
            "--grace-minutes",
            type=int,
            default=1440,
            help="宽限分钟数；默认 1440，表示只删除已过期超过 1 天的密码。",
        )
        parser.add_argument(
            "--delete-type",
            type=int,
            default=2,
            help="TTLock 删除方式，默认 2。",
        )

    def handle(self, *args, **options):
        # 所有统计变量都在本次命令内累计，最后统一输出，便于定时任务日志审计。
        # 删除截止时间 = 当前时间 - 宽限期，给刚过期的密码留出人工处理窗口。
        page_size = min(max(1, options["page_size"]), 100)
        dry_run = options["dry_run"]
        cutoff = timezone.now() - timedelta(minutes=max(0, options["grace_minutes"]))
        cutoff_ms = int(cutoff.timestamp() * 1000)

        locks = Lock.objects.all().order_by("id")
        if options["lock_ids"]:
            locks = locks.filter(id__in=options["lock_ids"])

        total_locks = 0
        scanned = 0
        expired = 0
        deleted = 0
        failed = 0
        skipped = 0

        for lock in locks:
            # 每把锁独立分页扫描并记录失败，不因单锁网络异常中断整个批处理。
            total_locks += 1
            account_key = (options.get("account") or _account_key(lock)).strip()
            page_no = 1
            expired_items = []

            self.stdout.write(
                f"处理门锁 id={lock.id} code={lock.code} account={account_key}"
            )

            while True:
                result = list_keyboard_passwords(
                    lock.code,
                    account_key=account_key,
                    page_no=page_no,
                    page_size=page_size,
                )
                if not result["success"]:
                    failed += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"  获取密码列表失败 page={page_no}: {result['message']}"
                        )
                    )
                    break

                items = result["items"]
                scanned += len(items)
                for item in items:
                    # 先收集过期项，完成分页读取后再删除，避免边遍历边修改分页结果。
                    end_ms = _end_ms(item)
                    keyboard_pwd_id = _keyboard_pwd_id(item)
                    keyboard_pwd = (
                        item.get("keyboardPwd") or item.get("keyboardPwdName") or ""
                    )

                    if end_ms is None or end_ms > cutoff_ms:
                        # 缺少结束时间时不删除，避免把无法判断的记录误判为过期。
                        skipped += 1
                        continue

                    expired += 1
                    expired_items.append((keyboard_pwd_id, keyboard_pwd, item))

                if not _looks_like_more_pages(
                    result["raw"], page_no, page_size, len(items)
                ):
                    break
                page_no += 1

            for keyboard_pwd_id, keyboard_pwd, item in expired_items:
                if not keyboard_pwd_id:
                    failed += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"  过期密码缺少 keyboardPwdId，无法删除：{item}"
                        )
                    )
                    continue

                if dry_run:
                    # dry-run 保持完整筛选和统计流程，但绝不调用删除接口。
                    self.stdout.write(
                        f"  dry-run 将删除 password={keyboard_pwd} keyboardPwdId={keyboard_pwd_id}"
                    )
                    continue

                delete_result = delete_keyboard_password(
                    lock.code,
                    keyboard_pwd_id,
                    account_key=account_key,
                    delete_type=options["delete_type"],
                )
                if delete_result["success"]:
                    deleted += 1
                    self.stdout.write(
                        self.style.SUCCESS(
                            f"  已删除 password={keyboard_pwd} keyboardPwdId={keyboard_pwd_id}"
                        )
                    )
                else:
                    failed += 1
                    self.stderr.write(
                        self.style.ERROR(
                            f"  删除失败 keyboardPwdId={keyboard_pwd_id}: {delete_result['message']}"
                        )
                    )

        self.stdout.write(
            self.style.SUCCESS(
                "完成："
                f"门锁 {total_locks} 把，扫描密码 {scanned} 个，"
                f"过期 {expired} 个，删除 {deleted} 个，"
                f"跳过 {skipped} 个，失败 {failed} 个。"
            )
        )
