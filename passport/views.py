import os
import random
import uuid
from datetime import date, datetime, time

from django.conf import settings
from django.http import Http404, HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext as _
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from PIL import Image

from passport.image_support import open_image, processing_path
from passport.models import Guest, GuestPassport, House, HouseLock
from passport.ttlock import send_temp_password
from passport.utils import (
    allowed_file,
    is_valid_my_number_card_image,
    is_valid_passport_image,
)

PASSPORT_DIR = os.path.join(settings.MEDIA_ROOT, "passports")
# 前台流程按“读取房源 -> 接收表单和证件 -> 识别图片 -> 下发门锁密码 -> 展示结果”顺序组织。
# 视图负责请求响应和数据库状态；图片识别、TTLock API、导出功能分别由独立模块承担。
THUMB_PREFIX = "thumb_"
LAST_ORDER_SESSION_KEY = "last_guest_order_no"


# ==================================================
# 工具函数
# ==================================================
def _get_active_house_or_404(**kwargs):
    # 前台只允许访问上线房源；把不存在和下线统一转换为 404，避免泄露状态。
    try:
        return House.objects.get(is_active=True, **kwargs)
    except House.DoesNotExist:
        raise Http404(_("Property not available"))


def _parse_int(value, default=0):
    # 把表单字符串转换为整数；用户输入为空或非法时返回默认值。
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _error(text, status=400):
    # 前台提交使用 JSON 接口，因此成功和失败都返回统一的 ok 字段。
    # status 既让浏览器知道请求失败，也方便前端在 fetch 中分开处理网络错误和业务错误。
    return JsonResponse({"ok": False, "error": text}, status=status)


def _get_request_order_id(request):
    # 订单号可能由第三方通过 POST 或 GET 传入；POST 优先，两个值都没有时交给调用者生成。
    return (request.POST.get("order_id") or request.GET.get("order_id") or "").strip()


def _passport_invalid_message(index, detail):
    # 把识别模块的机器可读原因转换为用户能采取行动的提示。
    detail = detail or {}
    reason = detail.get("reason") or ""

    if reason == "cannot_read_image":
        message = _("Image could not be read. Please upload a clearer photo.")
    elif reason == "image_too_small":
        message = _("Image resolution is too low. Please upload a clearer photo.")
    elif reason == "ocr_engine_unavailable":
        detail_text = (detail.get("ocr_detail") or "").strip()
        message = _(
            "Passport recognition service is not available on the server. Please contact support."
        )
        if detail_text:
            message = f"{message} ({detail_text})"
    else:
        mrz_text_score = float(detail.get("mrz_text_score") or 0.0)
        mrz_band_score = float(detail.get("mrz_band_score") or 0.0)
        face_score = float(detail.get("face_score") or 0.0)
        keyword_hit = bool(detail.get("keyword_hit"))

        if mrz_text_score < 0.35 and mrz_band_score < 0.35:
            message = _(
                "No clear passport information page was detected. Please upload the page with personal information."
            )
        elif mrz_text_score < 0.35:
            message = _(
                "The passport machine-readable area was not clear enough. Please keep the bottom code lines visible."
            )
        elif face_score < 0.25 and not keyword_hit:
            message = _(
                "The passport photo page was incomplete or blurry. Please keep the whole page visible."
            )
        else:
            message = _(
                "The uploaded photo does not look like a valid passport photo. Please retake it clearly."
            )

    return _("Passport photo %(index)s is invalid: %(reason)s") % {
        "index": index,
        "reason": message,
    }


def _normalize_lock_id(code):
    # 数据库中的门锁编号是字符串字段，但 TTLock 的 lockId 按数字传递；转换失败的记录跳过。
    try:
        return int(str(code).strip())
    except Exception:
        return None


def _get_house_locks(house):
    # 只返回当前房源绑定的锁；调用方随后按锁所属账号逐个请求 TTLock。
    # 通过中间表取得房源关联锁，并去重；TTLock API 需要数值型 lock_id。
    qs = (
        HouseLock.objects.filter(house_id=house.id)
        .select_related("lock")
        .values(
            "lock__code",
            "lock__account",
            "lock__remark",
        )
        .distinct()
    )

    locks = []
    for row in qs:
        # values() 返回字典而不是模型对象；这样只取 API 所需字段，减少数据库和内存开销。
        lock_id = _normalize_lock_id(row.get("lock__code"))
        if lock_id is None:
            continue

        locks.append(
            {
                "lock_id": lock_id,
                "account": row.get("lock__account") or "zhenghe",
                "remark": row.get("lock__remark") or "",
            }
        )

    return locks


def _make_thumbnail_fixed_height(input_path, output_path, height=300):
    # 缩略图统一高度，便于后台列表和 Excel 中稳定展示，不改变原始上传文件。
    # 缩略图统一固定高度、按原比例缩放，避免后台展示时改变图片比例。
    with open_image(input_path) as img:
        w, h = img.size
        scale = height / float(h)
        img.resize(
            (int(w * scale), height),
            Image.Resampling.LANCZOS,
        ).save(output_path)


def _get_session_guest(request):
    # 会话只保存最近订单号，用于用户返回结果页后继续补传证件，不承担长期订单存储。
    # 会话只保存订单号，重新查询数据库可确保展示到最新订单状态。
    # 房源下线后主动清除旧会话，防止用户继续看到不可用房源的结果。
    order_no = request.session.get(LAST_ORDER_SESSION_KEY)
    if not order_no:
        return None

    guest = Guest.objects.select_related("house").filter(order_no=order_no).first()
    if not guest or not guest.house.is_active:
        request.session.pop(LAST_ORDER_SESSION_KEY, None)
        return None

    return guest


def _render_result(request, guest, show_continue=False):
    # 结果页集中准备订单、门锁和护照识别信息，避免多个入口重复构造模板上下文。
    # 将模板需要的对象集中放进 context；模板只负责展示，不参与订单或门锁业务判断。
    return render(
        request,
        "passport/result.html",
        {
            "title": _("Check-in Information"),
            "guest": guest,
            "house": guest.house,
            "show_continue": show_continue,
            "contents": _("Welcome to Sizuya Homestay. Please remember your door code:")
            + guest.password,
        },
    )


# ==================================================
# 客人填写表单
# ==================================================
@csrf_exempt
@require_http_methods(["GET", "POST"])
def passport_form(request, house_code):
    # GET 展示上传表单；POST 处理一次完整提交，并把每张证件图片单独记录为 GuestPassport。
    # GET 展示登记页或恢复最近订单；POST 按“校验 -> 文件识别 -> 落库 -> 下发密码”执行。
    # 任一阶段失败都会删除本次已写入的文件，避免留下无法关联的媒体文件。
    if not house_code:
        return HttpResponseBadRequest(_("Missing property parameter"))

    house = _get_active_house_or_404(code=house_code.upper())
    # 房源的 max_guests 决定表单最多显示几个上传位置；实际 POST 仍会再次校验数量。
    guests_range = range(1, house.max_guests + 1)

    if request.method == "GET":
        # 同一浏览器提交成功后再次打开页面，会优先展示最近订单，防止重复登记。
        session_guest = _get_session_guest(request)
        if session_guest:
            return _render_result(request, session_guest, show_continue=True)

        return render(
            request,
            "passport/form.html",
            {
                "title": _("Guest Information"),
                "house": house,
                "guests_range": guests_range,
                "order_id": _get_request_order_id(request),
            },
        )

    # 先校验表单和日期，再处理文件与外部接口，避免无效请求产生副作用。
    name = (request.POST.get("name") or "").strip()
    phone_full = (request.POST.get("phone_full") or "").strip()
    job = (request.POST.get("job") or "").strip()
    guests_num = _parse_int(request.POST.get("guests_num"))
    check_in_raw = (request.POST.get("checkIn") or "").strip()
    check_out_raw = (request.POST.get("checkOut") or "").strip()
    order_id = _get_request_order_id(request) or uuid.uuid4().hex

    if not name:
        return _error(_("Please enter your name"))
    if not job:
        return _error(_("Please enter your occupation"))
    if not phone_full:
        return _error(_("Please enter your phone number"))
    if not (1 <= guests_num <= 10):
        return _error(_("Number of guests must be between 1 and 10"))

    try:
        # 日期字符串必须严格符合 HTML date 控件的 YYYY-MM-DD 格式，解析后的 date
        # 对象才用于比较入住和退房先后，避免直接比较字符串或混入时间部分。
        check_in_date = datetime.strptime(check_in_raw, "%Y-%m-%d").date()
        check_out_date = datetime.strptime(check_out_raw, "%Y-%m-%d").date()
    except ValueError:
        return _error(_("Invalid date format"))

    if check_out_date <= check_in_date:
        return _error(_("Check-out date must be after check-in date"))

    # 先收集并校验所有文件；这样缺图或格式错误时不会只处理部分上传内容。
    files = []
    for i in range(1, guests_num + 1):
        f = request.FILES.get(f"photo_{i}")
        if not f:
            return _error(_("Please select a photo to upload!"))
        if not allowed_file(f.name):
            return _error(_("Invalid image format"))
        files.append(f)

    os.makedirs(PASSPORT_DIR, exist_ok=True)
    # 下面三个列表分别记录原图、缩略图和待写入的数据库数据；出现异常时前两个用于回滚文件。
    saved_files = []
    saved_thumbs = []
    passport_records = []

    try:
        for index, f in enumerate(files, start=1):
            # 原图先落盘，再通过 processing_path 兼容 HEIC/HEIF 等需要转换的格式。
            ext = f.name.rsplit(".", 1)[-1].lower()
            fname = f"{uuid.uuid4().hex}.{ext}"
            abs_path = os.path.join(PASSPORT_DIR, fname)

            with open(abs_path, "wb+") as dst:
                # UploadedFile.chunks() 分段读取，避免把大图片一次性全部加载到内存。
                for chunk in f.chunks():
                    dst.write(chunk)

            thumb_name = f"{THUMB_PREFIX}{fname}"
            thumb_path = os.path.join(PASSPORT_DIR, thumb_name)
            with processing_path(abs_path) as analysis_path:
                # analysis_path 可能是 HEIC 临时 JPEG；原始上传文件仍保留原扩展名和内容。
                _make_thumbnail_fixed_height(analysis_path, thumb_path)

                passport_valid, passport_detail = is_valid_passport_image(
                    analysis_path,
                    debug=True,
                )
                my_number_card_valid, my_number_card_detail = (
                    is_valid_my_number_card_image(analysis_path, debug=True)
                )

            # 护照识别和 My Number Card 识别互相独立；任一识别通过即可占用一个上传位置。
            valid = passport_valid or my_number_card_valid
            if not valid:
                raise ValueError(_passport_invalid_message(index, passport_detail))

            detail = {
                "accepted": True,
                "document_type": ("passport" if passport_valid else "my_number_card"),
                "passport": passport_detail or {},
                "my_number_card": my_number_card_detail or {},
            }

            relative_path = os.path.join("passports", fname)
            relative_thumb = os.path.join("passports", thumb_name)

            saved_files.append(relative_path)
            saved_thumbs.append(relative_thumb)
            passport_records.append(
                {
                    "file_path": relative_path,
                    "image_index": index,
                    "mrz_raw": "\n".join(
                        # MRZ 行来自护照识别结果；My Number Card 没有 MRZ 时这里自然为空。
                        passport_detail.get("mrz_lines") or []
                    ),
                    "document_type": detail["document_type"],
                    "recognition_payload": detail or {},
                    "recognition_status": (
                        "accepted" if detail.get("accepted") else "rejected"
                    ),
                }
            )

        # 先创建订单主记录，再建立与每张证件图片对应的明细记录。
        guest = Guest.objects.create(
            name=name,
            phone=phone_full,
            job=job,
            house=house,
            order_no=order_id,
            source="第三方",
            occupancy=guests_num,
            check_in_date=check_in_date,
            check_out_date=check_out_date,
            password=house.password,
            password_source="house",
            status="paid",
            created_at=timezone.now(),
            ttlock_message="not_required",
            ttlock_payload={},
            ttlock_status="not_required",
        )

        for record in passport_records:
            # 识别结果写入 JSONField，既保留通过/失败原因，也方便后台追溯当时的评分数据。
            GuestPassport.objects.create(
                guest=guest,
                file_path=record["file_path"],
                document_type=record["document_type"],
                image_index=record["image_index"],
                mrz_raw=record["mrz_raw"],
                original_name=name,
                recognition_payload=record["recognition_payload"],
                recognition_status=record["recognition_status"],
            )

        temp_password = guest.password

        # password=0 表示房源没有固定密码，需要为关联的每把锁生成同一个临时密码。
        if str(house.password) == "0":
            locks = _get_house_locks(house)

            if not locks:
                raise ValueError(_("No lock is configured for this property"))

            # TTLock 要求开始时间不能太靠近当前时间；当天入住从当前时间计算，未来入住从零点开始。
            now = datetime.now()
            if check_in_date == date.today():
                check_in_dt = datetime.combine(check_in_date, now.time())
            else:
                check_in_dt = datetime.combine(check_in_date, time(0, 0, 0))

            check_out_dt = datetime.combine(check_out_date, time(14, 0, 0))

            if locks:
                # 同一订单使用同一个四位密码，便于客人使用；每把锁分别记录调用结果。
                pwd = str(random.randint(1000, 9999))
                ttlock_results = []
                all_success = True

                for item in locks:
                    # 每次调用独立返回结果。即使某一把锁失败，也继续尝试其他锁，最后统一报告失败明细。
                    result = send_temp_password(
                        lock_id=item["lock_id"],
                        password=pwd,
                        nickname=name,
                        checkin=check_in_dt,
                        checkout=check_out_dt,
                        account_key=item["account"],
                    )
                    ttlock_results.append(
                        {
                            "lock_id": item["lock_id"],
                            "account": item["account"],
                            "result": result,
                        }
                    )
                    if not result.get("success"):
                        all_success = False

                guest.ttlock_payload = {"results": ttlock_results}
                guest.ttlock_message = "success" if all_success else "partial_failure"
                guest.ttlock_status = "success" if all_success else "failed"

                if all_success:
                    guest.password = pwd
                    guest.password_source = "ttlock"
                    temp_password = pwd
                else:
                    # 不立即覆盖密码，先保留房源原值，并收集每把失败锁的原因供用户和后台排查。
                    failed_messages = []
                    for item in ttlock_results:
                        result = item.get("result") or {}
                        if result.get("success"):
                            continue
                        failed_messages.append(
                            f"{item['lock_id']}: {result.get('message') or 'unknown error'}"
                        )

                guest.save(
                    update_fields=[
                        "password",
                        "password_source",
                        "ttlock_message",
                        "ttlock_payload",
                        "ttlock_status",
                    ]
                )

                if not all_success:
                    raise ValueError(
                        _("Temporary password delivery failed: %(detail)s")
                        % {
                            "detail": "; ".join(failed_messages) or _("unknown error"),
                        }
                    )

        request.session[LAST_ORDER_SESSION_KEY] = guest.order_no
        # 只有订单和相关门锁操作全部成功后才写入会话，GET 页面因此不会恢复半成品订单。

        return JsonResponse(
            {
                "ok": True,
                "id": guest.id,
                "password": temp_password,
                "message": _("Submitted successfully, thank you!"),
            }
        )

    except Exception as e:
        # 数据库记录不会自动回滚已写入的文件，因此这里显式清理原图和缩略图。
        for p in saved_files + saved_thumbs:
            fp = os.path.join(settings.MEDIA_ROOT, p)
            if os.path.exists(fp):
                os.remove(fp)
        return _error(str(e))


# 结果页
def passport_result(request, id):
    # 结果页通过订单主键读取数据，主键不存在时由 get_object_or_404 返回 404。
    # 结果页仍检查房源状态，确保房源下线后不能通过旧 ID 继续访问入住信息。
    guest = get_object_or_404(Guest, id=id)

    if not guest.house.is_active:
        raise Http404(_("Property not available"))

    return _render_result(
        request,
        guest,
        show_continue=request.session.get(LAST_ORDER_SESSION_KEY) == guest.order_no,
    )


@require_http_methods(["POST"])
def passport_continue_upload(request):
    # 继续上传只依赖会话中的最近订单号，成功找到订单后复用统一结果页渲染逻辑。
    # 用户选择重新登记时清除旧订单会话，避免 GET 再次被恢复到上一个结果页。
    request.session.pop(LAST_ORDER_SESSION_KEY, None)
    house_code = (request.POST.get("house_code") or "").strip()
    if not house_code:
        return HttpResponseBadRequest(_("Missing property parameter"))

    return redirect("passport_form", house_code=house_code)
