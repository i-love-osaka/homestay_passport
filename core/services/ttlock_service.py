import hashlib
import threading
import time
from datetime import datetime, timedelta

import requests
from django.conf import settings

# 后台服务和管理命令共用 settings 中按账号区分的 TTLock 配置表。
TTLOCK_ACCOUNTS = getattr(settings, "TTLOCK_ACCOUNTS", None)

if not TTLOCK_ACCOUNTS:
    raise RuntimeError("未配置 TTLOCK_ACCOUNTS")


# =========================================================
# Token 缓存（每账号一份，线程安全）
# =========================================================
class TokenCache:
    # 单账号 token 缓存

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._access_token = None
        self._expires_at = 0

    def get_token(self):
        # 提前 60 秒刷新 Token，避免请求执行期间恰好跨过有效期。
        with self._lock:
            now = time.time()
            if self._access_token and now < self._expires_at - 60:
                return self._access_token
            return self._fetch_token()

    def _fetch_token(self):
        # 账号配置集中校验后再请求 OAuth，失败直接抛出明确的配置或接口异常。
        url = f"{self.config['BASE_URL']}/oauth2/token"

        password_md5 = hashlib.md5(self.config["PASSWORD"].encode("utf-8")).hexdigest()

        payload = {
            "client_id": self.config["CLIENT_ID"],
            "client_secret": self.config["CLIENT_SECRET"],
            "username": self.config["USERNAME"],
            "password": password_md5,
            "grant_type": "password",
        }

        resp = requests.post(url, data=payload, timeout=10)
        resp.raise_for_status()

        js = resp.json()
        access_token = js.get("access_token")
        expires_in = js.get("expires_in", 7200)

        if not access_token:
            raise RuntimeError(f"access_token 获取失败：{js}")

        self._access_token = access_token
        self._expires_at = time.time() + int(expires_in)
        return access_token


TOKEN_CACHES = {
    # 这里只创建缓存对象，不会立即访问网络；Token 在第一次业务调用时按需获取。
    key: TokenCache(config)
    for key, config in TTLOCK_ACCOUNTS.items()
}


# =========================================================
# 工具函数
# =========================================================
def get_access_token(account_key):
    # 账号 key 来自门锁配置；每个账号必须使用自己的缓存 Token。
    cache = TOKEN_CACHES.get(account_key)
    if not cache:
        raise RuntimeError(f"未找到 TTLock 账号配置：{account_key}")
    return cache.get_token()


def to_ms(ts):
    # Python 的 time.time() 返回秒，而 TTLock 的 date 参数要求毫秒，所以统一乘以 1000。
    return int(ts * 1000)


def _post_json(url, payload, timeout=30):
    # 统一把网络异常和非 JSON 响应转换为结构化结果，调用方无需重复 try/except。
    try:
        resp = requests.post(url, data=payload, timeout=timeout)
        status = resp.status_code
        try:
            js = resp.json()
        except Exception:
            js = None
        return status, js, None
    except Exception as e:
        return 0, None, str(e)


def _get_json(url, payload, timeout=30):
    # 查询接口使用 GET，参数放在 URL 查询字符串中；返回格式与 POST 辅助函数保持一致。
    try:
        resp = requests.get(url, params=payload, timeout=timeout)
        status = resp.status_code
        try:
            js = resp.json()
        except Exception:
            js = None
        return status, js, None
    except Exception as e:
        return 0, None, str(e)


def _success_from_response(js):
    # 不同 TTLock 接口有 success 或 errcode 两种成功表示，统一在这里兼容。
    if not isinstance(js, dict):
        return False
    if isinstance(js.get("success"), bool):
        return js.get("success") is True
    errcode = js.get("errcode")
    if errcode is None:
        return False
    try:
        return int(errcode) == 0
    except (TypeError, ValueError):
        return False


# =========================================================
# 远程开锁（支持多账号）
# =========================================================
def remote_unlock(lock_id, account_key="zhenghe"):
    # 后台远程开锁只返回标准化结果，不把 TTLock 原始响应直接交给页面层判断。
    if not lock_id:
        return {
            "success": False,
            "message": "lock_id 为空，无法执行远程开锁",
            "lock_id": None,
            "raw": None,
        }

    config = TTLOCK_ACCOUNTS.get(account_key)
    if not config:
        return {
            "success": False,
            "message": f"TTLock 账号不存在：{account_key}",
            "lock_id": lock_id,
            "raw": None,
        }

    lock_id = str(lock_id).strip()
    url = f"{config['BASE_URL']}/v3/lock/unlock"

    payload = {
        "clientId": config["CLIENT_ID"],
        "accessToken": get_access_token(account_key),
        "lockId": lock_id,
        "date": to_ms(time.time()),
    }

    status, js, err = _post_json(url, payload, timeout=30)
    if err:
        return {
            "success": False,
            "message": f"远程开锁请求异常：{err}",
            "lock_id": lock_id,
            "raw": None,
        }

    success = _success_from_response(js)
    message = js.get("errmsg") or js.get("message") if isinstance(js, dict) else ""
    if not message:
        message = "开锁成功" if success else f"开锁失败（HTTP {status}）"

    return {"success": success, "message": message, "lock_id": lock_id, "raw": js}


# =========================================================
# 键盘密码列表 / 删除（支持多账号）
# =========================================================
def list_keyboard_passwords(lock_id, account_key="zhenghe", page_no=1, page_size=100):
    # 管理命令依赖 items 和 raw：前者处理密码，后者判断是否还有下一页。
    if not lock_id:
        return {"success": False, "message": "lock_id 为空", "items": [], "raw": None}

    config = TTLOCK_ACCOUNTS.get(account_key)
    if not config:
        return {
            "success": False,
            "message": f"TTLock 账号不存在：{account_key}",
            "items": [],
            "raw": None,
        }

    lock_id = str(lock_id).strip()
    url = f"{config['BASE_URL']}/v3/lock/listKeyboardPwd"
    payload = {
        "clientId": config["CLIENT_ID"],
        "accessToken": get_access_token(account_key),
        "lockId": lock_id,
        "pageNo": int(page_no),
        "pageSize": int(page_size),
        "date": to_ms(time.time()),
    }

    status, js, err = _get_json(url, payload, timeout=30)
    if err:
        return {
            "success": False,
            "message": f"密码列表请求异常：{err}",
            "items": [],
            "raw": None,
        }

    success = _success_from_response(js) or (
        isinstance(js, dict)
        and any(
            # 列表接口有时没有 success 字段，但返回列表本身仍表示查询成功。
            isinstance(js.get(key), list)
            for key in ("list", "keyboardPwdList", "keyboardPwds")
        )
    )
    if not success:
        message = js.get("errmsg") or js.get("message") if isinstance(js, dict) else ""
        return {
            "success": False,
            "message": message or f"获取密码列表失败（HTTP {status}）",
            "items": [],
            "raw": js,
        }

    items = []
    if isinstance(js, dict):
        for key in ("list", "keyboardPwdList", "keyboardPwds"):
            value = js.get(key)
            if isinstance(value, list):
                items = value
                break

    return {"success": True, "message": "获取成功", "items": items, "raw": js}


def delete_keyboard_password(
    lock_id, keyboard_pwd_id, account_key="zhenghe", delete_type=2
):
    # 删除必须同时提供门锁和 TTLock 密码 ID，delete_type 原样映射为 API 参数。
    if not lock_id:
        return {"success": False, "message": "lock_id 为空", "raw": None}
    if not keyboard_pwd_id:
        return {"success": False, "message": "keyboard_pwd_id 为空", "raw": None}

    config = TTLOCK_ACCOUNTS.get(account_key)
    if not config:
        return {
            "success": False,
            "message": f"TTLock 账号不存在：{account_key}",
            "raw": None,
        }

    lock_id = str(lock_id).strip()
    url = f"{config['BASE_URL']}/v3/keyboardPwd/delete"
    payload = {
        "clientId": config["CLIENT_ID"],
        "accessToken": get_access_token(account_key),
        "lockId": lock_id,
        "keyboardPwdId": keyboard_pwd_id,
        "deleteType": int(delete_type),
        "date": to_ms(time.time()),
    }

    status, js, err = _post_json(url, payload, timeout=30)
    if err:
        return {"success": False, "message": f"删除密码请求异常：{err}", "raw": None}

    success = _success_from_response(js)
    message = js.get("errmsg") or js.get("message") if isinstance(js, dict) else ""
    if not message:
        message = "删除成功" if success else f"删除失败（HTTP {status}）"

    return {"success": success, "message": message, "raw": js}


# =========================================================
# 下发限时键盘密码（支持多账号）
# =========================================================
def send_temp_password(
    lock_id, password, start_date=None, end_date=None, account_key="zhenghe"
):
    # 后台以日期为输入，转换成当天起止时间；当天开始时间至少延后 5 分钟。
    if not lock_id:
        return {
            "success": False,
            "message": "lock_id 为空",
            "password": None,
            "raw": None,
        }

    config = TTLOCK_ACCOUNTS.get(account_key)
    if not config:
        return {
            "success": False,
            "message": f"TTLock 账号不存在：{account_key}",
            "password": None,
            "raw": None,
        }

    lock_id = str(lock_id).strip()
    password = str(password or "").strip()
    if not password:
        return {
            "success": False,
            "message": "password 为空",
            "password": None,
            "raw": None,
        }

    try:
        start_day = datetime.strptime(str(start_date or "").strip(), "%Y-%m-%d")
    except ValueError:
        return {
            "success": False,
            "message": "开始日期格式错误，应为 YYYY-MM-DD",
            "password": None,
            "raw": None,
        }

    try:
        end_day = datetime.strptime(str(end_date or "").strip(), "%Y-%m-%d")
    except ValueError:
        return {
            "success": False,
            "message": "结束日期格式错误，应为 YYYY-MM-DD",
            "password": None,
            "raw": None,
        }

    if end_day < start_day:
        return {
            "success": False,
            "message": "结束日期不能早于开始日期",
            "password": None,
            "raw": None,
        }

    now = datetime.now()
    # 后台只输入日期：开始日从 00:00 起，结束日到 23:59:59；当天开始则至少延后 5 分钟。
    min_start = now + timedelta(minutes=5)
    selected_start = start_day.replace(hour=0, minute=0, second=0, microsecond=0)
    selected_end = end_day.replace(hour=23, minute=59, second=59, microsecond=0)

    start_dt = selected_start
    if start_day.date() == now.date() and start_dt < min_start:
        start_dt = min_start.replace(second=0, microsecond=0)

    if selected_end <= start_dt:
        return {
            "success": False,
            "message": "结束日期必须晚于开始日期时间",
            "password": None,
            "raw": None,
        }

    start_ms = to_ms(start_dt.timestamp())
    end_ms = to_ms(selected_end.timestamp())

    url = f"{config['BASE_URL']}/v3/keyboardPwd/add"
    payload = {
        "clientId": config["CLIENT_ID"],
        "accessToken": get_access_token(account_key),
        "lockId": lock_id,
        "keyboardPwd": password,
        "keyboardPwdName": "临时",
        "keyboardPwdType": 3,
        "addType": 2,
        "startDate": start_ms,
        "endDate": end_ms,
        "date": start_ms,
    }

    status, js, err = _post_json(url, payload, timeout=30)
    if err:
        return {
            "success": False,
            "message": f"接口调用异常：{err}",
            "password": None,
            "raw": None,
        }

    # 部分接口即使 errcode 不明显，也会返回有效的 keyboardPwdId，因此兼容该响应形态。
    success = _success_from_response(js)

    keyboard_pwd_id = js.get("keyboardPwdId") if isinstance(js, dict) else None
    if not success and keyboard_pwd_id is not None:
        try:
            success = int(keyboard_pwd_id) > 0
        except (TypeError, ValueError):
            success = False

    message = js.get("errmsg") or js.get("message") if isinstance(js, dict) else ""
    if not message:
        message = "下发成功" if success else f"下发失败（HTTP {status}）"

    return {
        "success": success,
        "message": message,
        "password": password if success else None,
        "raw": js,
    }
