import hashlib
import threading
import time
from datetime import datetime

import requests
from django.conf import settings

# 本模块是旧调用入口；统一负责获取配置、缓存令牌，并把限时密码请求转发给 TTLock。
# 数据库写入和订单状态更新留在 views.py，避免第三方 API 模块与业务模型相互耦合。
# 加载通通配置
TTLOCK_ACCOUNTS = getattr(settings, "TTLOCK_ACCOUNTS", None)

if not TTLOCK_ACCOUNTS:
    raise RuntimeError("未配置 TTLOCK_ACCOUNTS")


# =========================================================
# Token 缓存（每个账号一份，线程安全）
# =========================================================
class TokenCache:
    # 单账号 token 缓存：同一个账号复用令牌，不同账号彼此隔离。

    def __init__(self, config):
        self.config = config
        self._lock = threading.Lock()
        self._access_token = None
        self._expires_at = 0

    def get_token(self):
        # 提前 60 秒视为过期，给网络请求留出余量，避免刚拿到 Token 就失效。
        with self._lock:
            now = time.time()
            if self._access_token and now < self._expires_at - 60:
                return self._access_token
            return self._fetch_token()

    def _fetch_token(self):
        # TTLock OAuth 接口要求密码先做 MD5，Token 有效期由服务端响应决定。
        if not all(
            [
                self.config.get("CLIENT_ID"),
                self.config.get("CLIENT_SECRET"),
                self.config.get("USERNAME"),
                self.config.get("PASSWORD"),
                self.config.get("BASE_URL"),
            ]
        ):
            raise RuntimeError("TTLock 账号配置不完整")

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
        js = resp.json()

        access_token = js.get("access_token")
        expires_in = js.get("expires_in", 7200)

        if not access_token:
            raise RuntimeError("获取 access_token 失败")

        self._access_token = access_token
        self._expires_at = time.time() + int(expires_in)
        # 保存绝对过期时刻，下一次调用只需将当前时间与它比较。
        return access_token


# =========================================================
# 为每个账号初始化一个 TokenCache
# =========================================================
TOKEN_CACHES = {key: TokenCache(config) for key, config in TTLOCK_ACCOUNTS.items()}


# =========================================================
# 工具函数
# =========================================================
def get_access_token(account_key):
    # 房源关联锁决定账号；配置不存在时不继续调用 API，避免发错账号。
    cache = TOKEN_CACHES.get(account_key)
    if not cache:
        raise RuntimeError(f"未找到 TTLock 账号配置：{account_key}")
    return cache.get_token()


def to_ms(ts):
    # TTLock 时间参数使用 Unix 时间戳毫秒；兼容 datetime 和数字时间戳调用者。
    if isinstance(ts, datetime):
        return int(ts.timestamp() * 1000)

    if isinstance(ts, (float, int)):
        return int(ts * 1000)

    # 未识别的时间类型返回 None，由远端接口或调用方决定如何处理。


# =========================================================
# 下发限时键盘密码（支持多账号）
# =========================================================
def send_temp_password(
    lock_id,
    password,
    nickname,
    checkin,
    checkout,
    account_key="zhenghe",
):
    # 用户登记流程传入完整日期时间，成功后返回密码供 Guest 保存并展示给客人。
    # 这个函数只负责调用 TTLock，不负责修改 Guest；数据库状态由 views.py 统一维护。
    if not lock_id:
        return {"success": False, "message": "lock_id 为空"}

    config = TTLOCK_ACCOUNTS.get(account_key)
    if not config:
        return {"success": False, "message": f"TTLock 账号不存在：{account_key}"}

    lock_id = str(lock_id)

    payload = {
        "clientId": config["CLIENT_ID"],
        "lockId": lock_id,
        "keyboardPwd": password,
        "keyboardPwdName": nickname,
        "keyboardPwdType": 3,
        "addType": 2,
        "startDate": to_ms(checkin),
        "endDate": to_ms(checkout),
        "date": to_ms(time.time()),
    }

    def _call():
        # 每次重试都重新读取缓存 Token，便于外层清除失效 Token 后获取新值。
        payload["accessToken"] = get_access_token(account_key)
        resp = requests.post(
            f"{config['BASE_URL']}/v3/keyboardPwd/add",
            data=payload,
            timeout=30,
        )
        return resp.json()

    try:
        js = _call()

        # 10003/10004 表示 Token 相关错误；清缓存后只重试一次，避免重复下发。
        if js.get("errcode") in (10003, 10004):
            TOKEN_CACHES[account_key]._access_token = None
            js = _call()
    except Exception as e:  # noqa: BLE001
        return {
            "success": False,
            "message": f"接口调用异常：{e}",
            "password": None,
            "raw": None,
        }

    # TTLock 通常用 errcode=0 表示成功；某些响应只有有效 keyboardPwdId，因此两种形式都检查。
    errcode = js.get("errcode")
    errmsg = js.get("errmsg") or js.get("message")
    keyboard_pwd_id = js.get("keyboardPwdId")

    success = errcode == 0
    if not success and keyboard_pwd_id is not None:
        try:
            success = int(keyboard_pwd_id) > 0
        except (TypeError, ValueError):
            success = False

    if not errmsg:
        errmsg = "下发成功" if success else "下发失败"

    return {
        "success": success,
        "message": errmsg,
        "password": password if success else None,
        "raw": js,
    }
