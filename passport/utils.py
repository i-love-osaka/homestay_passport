import os
import re
from functools import lru_cache
from pathlib import Path

# OCR 工具链分为三层：读取与缩放图片、提取护照版式特征、综合评分并返回可序列化结果。
# 这些函数尽量只返回识别事实，不直接修改数据库；业务状态由 views.py 负责保存。
import cv2
import numpy as np

try:
    import pytesseract
except Exception:
    pytesseract = None


tesseract_last_error = ""
tesseract_cmd = (os.environ.get("TESSERACT_CMD") or "").strip()
tessdata_prefix = (os.environ.get("TESSDATA_PREFIX") or "").strip()

if pytesseract is not None and tesseract_cmd:
    pytesseract.pytesseract.tesseract_cmd = tesseract_cmd


cv2.setUseOptimized(True)
try:
    cv2.setNumThreads(0)
except Exception:
    pass


ALLOWED_IMAGE_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "bmp",
    "tiff",
    "gif",
    "heic",
    "heif",
}

PASSPORT_KEYWORDS = (
    "PASSPORT",
    "PASSEPORT",
    "PASAPORTE",
    "REISEPASS",
    "PASSPORT NO",
    "PASSPORT NUMBER",
)

MRZ_VALID_CHARS = set("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<")

MY_NUMBER_CARD_MARK_PATH = (
    Path(__file__).resolve().parent.parent
    / "static"
    / "img"
    / "passport"
    / "card_mark.png"
)


@lru_cache(maxsize=1)
def _tesseract_ready():
    # 在真正 OCR 前检查引擎和 eng 语言包，尽早返回明确原因，避免上传流程出现难懂的底层异常。
    # OCR 初始化结果缓存一次，避免每张上传图片都重复探测可执行文件和语言包。
    global tesseract_last_error

    if pytesseract is None:
        tesseract_last_error = "pytesseract_import_failed"
        return False, tesseract_last_error

    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception as exc:
        tesseract_last_error = str(exc) or exc.__class__.__name__
        return False, tesseract_last_error

    try:
        config = f'--tessdata-dir "{tessdata_prefix}"' if tessdata_prefix else ""
        languages = {
            str(item).lower() for item in pytesseract.get_languages(config=config)
        }
    except Exception as exc:
        tesseract_last_error = str(exc) or exc.__class__.__name__
        return False, tesseract_last_error

    if "eng" not in languages:
        tesseract_last_error = f"missing_eng_traineddata:{','.join(sorted(languages))}"
        return False, tesseract_last_error

    tesseract_last_error = ""
    return True, version


def allowed_file(filename, allowed_exts=None):
    # 这里只做扩展名白名单校验，实际内容仍由 OpenCV/Pillow 在后续读取时确认。
    if allowed_exts is None:
        allowed_exts = ALLOWED_IMAGE_EXTENSIONS
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_exts


@lru_cache(maxsize=1)
def _my_number_card_mark():
    # 只加载一次已批准的 My Number Card 右上角标志模板。
    image = cv2.imread(str(MY_NUMBER_CARD_MARK_PATH), cv2.IMREAD_GRAYSCALE)
    if image is None or image.size == 0:
        return None
    return image


def _prepare_mark_image(image):
    # 模板和待检测图片必须经过相同预处理，才能让灰度匹配结果具有可比性。
    gray = image if len(image.shape) == 2 else cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    return cv2.GaussianBlur(gray, (3, 3), 0)


def _my_number_card_template_score(image, template):
    # 按多个缩放比例寻找标志，并返回最高的归一化相似度。
    gray = _prepare_mark_image(image)
    template_gray = _prepare_mark_image(template)
    height, width = gray.shape[:2]
    template_height, template_width = template_gray.shape[:2]

    best_score = 0.0
    best_bbox = None

    # 标志在原图中大小不固定，因此按相对宽度尝试多个模板尺寸，而不是依赖固定像素。
    for fraction in (0.07, 0.09, 0.12, 0.16, 0.20, 0.26, 0.33, 0.42):
        target_width = max(32, int(width * fraction))
        if target_width >= width or target_width >= height:
            continue

        target_height = max(32, int(template_height * target_width / template_width))
        resized = cv2.resize(
            template_gray,
            (target_width, target_height),
            interpolation=cv2.INTER_AREA
            if target_width < template_width
            else cv2.INTER_CUBIC,
        )
        if resized.shape[0] >= height or resized.shape[1] >= width:
            continue

        # matchTemplate 返回每个位置的相似度矩阵；这里只保留最高分及其左上角坐标。
        result = cv2.matchTemplate(gray, resized, cv2.TM_CCOEFF_NORMED)
        _, score, _, location = cv2.minMaxLoc(result)
        if score > best_score:
            x, y = location
            best_score = float(score)
            best_bbox = [x, y, x + target_width, y + target_height]

    return best_score, best_bbox


def _my_number_card_feature_score(image, template):
    # 使用 ORB 特征补充模板匹配，使识别能容忍缩放和旋转。
    orb = cv2.ORB_create(nfeatures=1200)
    template_gray = _prepare_mark_image(template)
    template_keypoints, template_descriptors = orb.detectAndCompute(template_gray, None)
    if template_descriptors is None or len(template_keypoints) < 6:
        return 0.0

    # ORB 特征作为模板匹配的补充，用于吸收缩放、旋转和轻微光照变化。
    best_good = 0
    for rotation in (0, 1, 2, 3):
        # 模板本身不旋转，但输入图像尝试四个方向，以提高手机旋转拍摄时的容错率。
        rotated = _rotate(image, rotation)
        gray = _prepare_mark_image(rotated)
        keypoints, descriptors = orb.detectAndCompute(gray, None)
        if descriptors is None or len(keypoints) < 6:
            continue

        matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
        try:
            pairs = matcher.knnMatch(template_descriptors, descriptors, k=2)
        except cv2.error:
            continue

        # Lowe ratio test 只保留“最相似匹配明显优于第二相似匹配”的特征点。
        good = [
            first
            for first, second in pairs
            if second is not None and first.distance < 0.76 * second.distance
        ]
        best_good = max(best_good, len(good))

    return min(best_good / 14.0, 1.0)


def is_valid_my_number_card_image(filepath, debug=False):
    # My Number Card 采用独立识别流程，只验证卡片标志，不与护照 MRZ 评分混用。
    # 这里只识别卡片版式标志，不读取个人编号，也不能据此证明证件真伪。
    image = _read_image(filepath)
    if image is None:
        result = {"accepted": False, "reason": "cannot_read_image"}
        return False, result

    height, width = image.shape[:2]
    if min(height, width) < 180:
        result = {
            "accepted": False,
            "reason": "image_too_small",
            "mark_score": 0.0,
            "feature_score": 0.0,
        }
        return False, result

    template = _my_number_card_mark()
    if template is None:
        result = {
            "accepted": False,
            "reason": "my_number_card_template_unavailable",
            "mark_score": 0.0,
            "feature_score": 0.0,
        }
        return False, result

    image = _resize_max_side(image, 1800)
    mark_score, bbox = _my_number_card_template_score(image, template)
    feature_score = _my_number_card_feature_score(image, template)

    # 模板匹配是主要信号；ORB 只作为容忍缩放、旋转、透视和光照变化的补充信号。
    accepted = (
        mark_score >= 0.62
        or (mark_score >= 0.52 and feature_score >= 0.35)
        or feature_score >= 0.72
    )
    result = {
        "accepted": bool(accepted),
        "reason": "my_number_card_mark_detected"
        if accepted
        else "my_number_card_mark_not_detected",
        "mark_score": round(mark_score, 4),
        "feature_score": round(feature_score, 4),
        "matched_bbox": bbox or [],
        "template": str(MY_NUMBER_CARD_MARK_PATH),
    }
    return bool(accepted), _to_builtin(result)


def _read_image(path):
    # np.fromfile 兼容包含非 ASCII 字符的路径，随后交给 OpenCV 解码图片内容。
    try:
        data = np.fromfile(path, dtype=np.uint8)
    except Exception:
        return None
    if data.size == 0:
        return None
    return cv2.imdecode(data, cv2.IMREAD_COLOR)


def _resize_max_side(image, max_side=1400):
    # 限制最长边可以控制 OCR/OpenCV 的耗时；图片较小时直接复用原数组避免重复缩放。
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image

    scale = float(max_side) / float(longest)
    size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA)


def _rotate(image, rotation_index):
    # 用 0、1、2、3 表示四种方向，调用方无需重复编写 OpenCV 旋转分支。
    if rotation_index == 0:
        return image
    if rotation_index == 1:
        return cv2.rotate(image, cv2.ROTATE_90_CLOCKWISE)
    if rotation_index == 2:
        return cv2.rotate(image, cv2.ROTATE_180)
    return cv2.rotate(image, cv2.ROTATE_90_COUNTERCLOCKWISE)


def _to_builtin(value):
    # OpenCV/NumPy 类型不能直接安全序列化到 JSON；递归转换为 Python 内置类型。
    if isinstance(value, dict):
        return {str(key): _to_builtin(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_builtin(item) for item in value]
    if isinstance(value, np.ndarray):
        return [_to_builtin(item) for item in value.tolist()]
    if isinstance(value, np.generic):
        return value.item()
    return value


@lru_cache(maxsize=1)
def _face_cascade():
    # Haar 分类器文件加载成本较高，缓存后每张图片只需重复执行检测。
    return cv2.CascadeClassifier(
        cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
    )


def _detect_face_score(image):
    # 人脸位置和大小只作为“像护照信息页”的弱信号，不单独决定是否通过。
    gray = cv2.cvtColor(_resize_max_side(image, 900), cv2.COLOR_BGR2GRAY)
    height, width = gray.shape[:2]
    faces = _face_cascade().detectMultiScale(
        gray,
        scaleFactor=1.08,
        minNeighbors=5,
        minSize=(max(40, width // 14), max(40, height // 14)),
    )

    best_score = 0.0
    best_face = None
    image_area = float(height * width)

    for x, y, w, h in faces:
        # 护照头像通常位于页面中上部且大小适中，因此把面积和中心位置组合成弱评分。
        area_ratio = (w * h) / image_area
        center_x = (x + w / 2.0) / width
        center_y = (y + h / 2.0) / height

        size_score = 1.0 - min(abs(area_ratio - 0.12) / 0.12, 1.0)
        x_score = 1.0 - min(abs(center_x - 0.5) / 0.35, 1.0)
        y_score = 1.0 - min(abs(center_y - 0.38) / 0.28, 1.0)
        score = 0.45 * size_score + 0.25 * x_score + 0.30 * y_score

        if score > best_score:
            best_score = score
            best_face = (x, y, w, h)

    return best_score, best_face


def _locate_mrz_band(gray):
    # 只在图片下半部寻找宽而扁的文字带，以降低页面其他文字造成的误检。
    height, width = gray.shape[:2]
    start_y = int(height * 0.52)
    region = gray[start_y:, :]
    if region.size == 0:
        return 0.0, None

    region = cv2.GaussianBlur(region, (3, 3), 0)
    rect_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (25, 7))
    sq_kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (21, 21))

    # Blackhat 突出深色文字，Sobel 强调横向边缘，再通过形态学闭运算连接同一行字符。
    blackhat = cv2.morphologyEx(region, cv2.MORPH_BLACKHAT, rect_kernel)
    grad_x = cv2.Sobel(blackhat, cv2.CV_32F, 1, 0, ksize=-1)
    grad_x = np.absolute(grad_x)

    max_value = float(grad_x.max()) if grad_x.size else 0.0
    if max_value <= 0:
        return 0.0, None

    grad_x = np.uint8(255 * (grad_x / max_value))
    grad_x = cv2.morphologyEx(grad_x, cv2.MORPH_CLOSE, rect_kernel)
    _, thresh = cv2.threshold(grad_x, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    thresh = cv2.morphologyEx(thresh, cv2.MORPH_CLOSE, sq_kernel)
    thresh = cv2.erode(thresh, None, iterations=1)
    thresh = cv2.dilate(thresh, None, iterations=2)

    contours, _ = cv2.findContours(thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best = 0.0
    best_bbox = None
    region_height = float(region.shape[0])
    region_width = float(region.shape[1])

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        width_ratio = w / region_width
        height_ratio = h / region_height
        aspect_ratio = w / float(max(h, 1))
        bottom_ratio = (y + h) / region_height

        # MRZ 是横向长条；先过滤明显不符合形状的轮廓，再对剩余候选评分。
        if width_ratio < 0.45 or height_ratio < 0.10 or aspect_ratio < 4.5:
            continue

        score = (
            min(width_ratio / 0.82, 1.0) * 0.45
            + min(aspect_ratio / 12.0, 1.0) * 0.25
            + min(bottom_ratio / 0.92, 1.0) * 0.30
        )
        if score > best:
            best = score
            pad_x = max(8, int(w * 0.02))
            pad_y = max(6, int(h * 0.20))
            x1 = max(0, x - pad_x)
            y1 = max(0, start_y + y - pad_y)
            x2 = min(width, x + w + pad_x)
            y2 = min(height, start_y + y + h + pad_y)
            best_bbox = (x1, y1, x2, y2)

    return best, best_bbox


def _ocr(image, *, whitelist=None, psm=6, timeout=1.8):
    # 每次 OCR 都设置超时；识别服务异常时返回空文本，由上层评分逻辑统一处理。
    global tesseract_last_error

    ready, detail = _tesseract_ready()
    if not ready:
        tesseract_last_error = detail
        return ""

    config = f"--oem 1 --psm {psm}"
    if whitelist:
        config += f" -c tessedit_char_whitelist={whitelist}"

    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

    try:
        return (
            pytesseract.image_to_string(
                rgb,
                lang="eng",
                config=config,
                timeout=timeout,
            )
            or ""
        )
    except Exception as exc:
        tesseract_last_error = str(exc) or exc.__class__.__name__
        return ""


def _prepare_keyword_roi(image):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _prepare_mrz_roi(image):
    # MRZ 字符细且规则，放大后自适应二值化可增强字符与背景的对比度。
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    gray = cv2.equalizeHist(gray)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    gray = cv2.resize(
        gray,
        None,
        fx=1.8,
        fy=1.8,
        interpolation=cv2.INTER_CUBIC,
    )
    thresh = cv2.adaptiveThreshold(
        gray,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        31,
        9,
    )
    return cv2.cvtColor(thresh, cv2.COLOR_GRAY2BGR)


def _best_keyword_hit(image):
    # 同时识别预处理图和原图，任一结果包含护照关键词即可命中。
    prepared = _prepare_keyword_roi(image)
    texts = [
        _ocr(prepared, psm=6, timeout=1.2),
        _ocr(image, psm=6, timeout=1.2),
    ]
    hit = any(_contains_passport_keyword(text) for text in texts)
    return hit, max(texts, key=len, default="")


def _best_mrz_result(image):
    # 先尝试常规版式，再用稀疏文本模式兜底，选取得分最高的 MRZ 候选。
    variants = [image, _prepare_mrz_roi(image)]
    best_score = 0.0
    best_lines = []
    best_text = ""

    for variant in variants:
        text = _ocr(
            variant,
            whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
            psm=6,
            timeout=1.3,
        )
        score, lines = _score_mrz_text(text)
        if score > best_score:
            best_score = score
            best_lines = lines
            best_text = text

    if best_score < 0.58:
        fallback_text = _ocr(
            variants[-1],
            whitelist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789<",
            psm=11,
            timeout=1.5,
        )
        fallback_score, fallback_lines = _score_mrz_text(fallback_text)
        if fallback_score > best_score:
            best_score = fallback_score
            best_lines = fallback_lines
            best_text = fallback_text

    return best_score, best_lines, best_text


def _extract_region(image, bbox):
    if not bbox:
        return image
    x1, y1, x2, y2 = bbox
    if x2 <= x1 or y2 <= y1:
        return image
    return image[y1:y2, x1:x2]


def _clean_mrz_lines(text):
    # OCR 常带空格、标点或换行噪声；这里只保留 MRZ 允许的字符和足够长的候选行。
    cleaned = []
    for line in (text or "").upper().splitlines():
        normalized = re.sub(r"[^A-Z0-9<]", "", line)
        if len(normalized) >= 24 and all(ch in MRZ_VALID_CHARS for ch in normalized):
            cleaned.append(normalized)
    return cleaned


def _score_mrz_text(text):
    # 将 OCR 文本按护照 MRZ 的长度、尖括号、字母数字结构进行启发式评分。
    lines = _clean_mrz_lines(text)
    if len(lines) < 2:
        return 0.0, []

    unique_lines = []
    for line in sorted(lines, key=lambda item: (-len(item), -item.count("<"))):
        if line not in unique_lines:
            unique_lines.append(line)
    lines = unique_lines[:6]

    best_score = 0.0
    best_pair = []

    for first_index, first in enumerate(lines):
        for second_index, second in enumerate(lines):
            if first_index == second_index:
                continue

            first_len_score = 1.0 - min(abs(len(first) - 44) / 18.0, 1.0)
            second_len_score = 1.0 - min(abs(len(second) - 44) / 18.0, 1.0)
            chevron_score = min((first.count("<") + second.count("<")) / 16.0, 1.0)
            passport_prefix = 1.0 if first.startswith("P<") else 0.0
            alpha_prefix = 1.0 if re.match(r"^[A-Z<]{5,}", first) else 0.0
            digit_score = min(sum(ch.isdigit() for ch in second) / 12.0, 1.0)
            second_prefix = 1.0 if re.match(r"^[A-Z0-9<]{8,}", second) else 0.0
            country_code_score = (
                1.0 if re.search(r"[A-Z<][A-Z<][A-Z<]", second[:6]) else 0.0
            )
            passport_number_score = (
                1.0 if re.match(r"^[A-Z0-9<]{8,12}", second) else 0.0
            )

            # 每项分数都归一化到 0~1，再按经验权重合成总分；长度和字符结构权重最高。
            score = (
                0.22 * first_len_score
                + 0.22 * second_len_score
                + 0.20 * chevron_score
                + 0.18 * passport_prefix
                + 0.06 * alpha_prefix
                + 0.06 * digit_score
                + 0.04 * second_prefix
                + 0.01 * country_code_score
                + 0.01 * passport_number_score
            )

            if passport_prefix and len(first) >= 30 and len(second) >= 30:
                score += 0.10
            if passport_prefix and digit_score >= 0.5:
                score += 0.08

            if score > best_score:
                best_score = score
                best_pair = [first, second]

    return best_score, best_pair


def _contains_passport_keyword(text):
    # 关键词是辅助证据，不单独决定通过；OCR 识别到的标点和大小写会先被规范化。
    normalized = re.sub(r"[^A-Z ]", " ", (text or "").upper())
    normalized = re.sub(r"\s+", " ", normalized).strip()

    if not normalized:
        return False

    if any(keyword in normalized for keyword in PASSPORT_KEYWORDS):
        return True

    return "PASSPORT" in normalized


def _analyze_orientation(image):
    # 对单一方向汇总人脸、MRZ 带、MRZ 文本和标题关键词四类信号。
    scaled = _resize_max_side(image, 1400)
    gray = cv2.cvtColor(scaled, cv2.COLOR_BGR2GRAY)

    face_score, face_box = _detect_face_score(scaled)
    mrz_band_score, mrz_bbox = _locate_mrz_band(gray)

    if face_score < 0.18 and mrz_band_score < 0.28:
        # 两个初筛信号都很弱时不继续 OCR，可节省时间并降低随机文字误判概率。
        return {
            "face_score": face_score,
            "mrz_band_score": mrz_band_score,
            "mrz_text_score": 0.0,
            "keyword_hit": False,
            "mrz_lines": [],
            "mrz_bbox": mrz_bbox,
        }

    height, width = scaled.shape[:2]
    top_end = max(int(height * 0.48), 1)
    bottom_start = min(int(height * 0.72), height - 1)

    top_region = scaled[:top_end, :]
    fallback_bottom_region = scaled[bottom_start:, :]
    mrz_region = _extract_region(scaled, mrz_bbox)
    bottom_region = mrz_region if mrz_bbox is not None else fallback_bottom_region

    keyword_hit, keyword_text = (False, "")
    if mrz_band_score < 0.45 or face_score > 0.12:
        keyword_hit, keyword_text = _best_keyword_hit(top_region)
    mrz_text_score, mrz_lines, mrz_text = _best_mrz_result(bottom_region)
    # 优先识别检测出的 MRZ 区域；结果不足时逐步扩大到下半部和整张图片作为兜底。

    if mrz_text_score < 0.52 and mrz_bbox is not None:
        fallback_score, fallback_lines, fallback_text = _best_mrz_result(
            fallback_bottom_region
        )
        if fallback_score > mrz_text_score:
            mrz_text_score = fallback_score
            mrz_lines = fallback_lines
            mrz_text = fallback_text

    if mrz_text_score < 0.42:
        full_score, full_lines, full_text = _best_mrz_result(scaled)
        if full_score > mrz_text_score:
            mrz_text_score = full_score
            mrz_lines = full_lines
            mrz_text = full_text

    return {
        "face_score": face_score,
        "mrz_band_score": mrz_band_score,
        "mrz_text_score": mrz_text_score,
        "keyword_hit": keyword_hit,
        "mrz_lines": mrz_lines,
        "face_box": face_box,
        "mrz_bbox": mrz_bbox,
        "keyword_text": keyword_text,
        "mrz_text": mrz_text,
    }


def is_valid_passport_image(filepath, require_face=False, debug=False):
    # 护照校验同时观察关键词、MRZ 区域和可选的人脸特征，最终返回是否通过及各项评分明细。
    # 图片可能来自手机横拍、竖拍或旋转后的文件，因此按候选方向逐一分析。
    # 先用较便宜的方向顺序尝试，所有方向都不满足阈值时才返回最高分诊断信息。
    image = _read_image(filepath)
    if image is None:
        result = {"reason": "cannot_read_image"}
        return (False, result) if debug else (False, result)

    ready, detail = _tesseract_ready()
    if not ready:
        result = {
            "reason": "ocr_engine_unavailable",
            "ocr_detail": detail,
        }
        return (False, _to_builtin(result)) if debug else (False, _to_builtin(result))

    height, width = image.shape[:2]
    if min(height, width) < 320:
        result = {"reason": "image_too_small"}
        return (False, result) if debug else (False, result)

    best = {
        "accepted": False,
        "rotation": 0,
        "face_score": 0.0,
        "mrz_band_score": 0.0,
        "mrz_text_score": 0.0,
        "keyword_hit": False,
        "mrz_lines": [],
        "final_score": 0.0,
    }

    rotations = (0, 2) if height >= width else (1, 3, 0, 2)

    for rotation in rotations:
        analysis = _analyze_orientation(_rotate(image, rotation))
        # final_score 用于保留最佳诊断；真正的通过条件仍由下方多组规则共同决定。
        final_score = (
            0.18 * analysis["face_score"]
            + 0.32 * analysis["mrz_band_score"]
            + 0.40 * analysis["mrz_text_score"]
            + 0.10 * (1.0 if analysis["keyword_hit"] else 0.0)
        )

        if final_score > best["final_score"]:
            best.update(analysis)
            best["rotation"] = rotation
            best["final_score"] = final_score

        # 这里不是简单判断 final_score，而是准备多组“强 MRZ”“关键词+版式”等可解释规则。
        face_ready = analysis["face_score"] >= 0.20 or not require_face
        strong_passport = (
            analysis["mrz_text_score"] >= 0.58 and analysis["mrz_band_score"] >= 0.18
        )
        balanced_passport = (
            analysis["mrz_text_score"] >= 0.40
            and analysis["mrz_band_score"] >= 0.16
            and analysis["keyword_hit"]
        )
        mrz_first_passport = analysis["mrz_text_score"] >= 0.46 and (
            analysis["mrz_band_score"] >= 0.12 or len(analysis["mrz_lines"]) >= 2
        )
        text_heavy_passport = (
            analysis["mrz_text_score"] >= 0.62 and analysis["keyword_hit"]
        )
        keyword_face_passport = (
            analysis["keyword_hit"]
            and analysis["mrz_band_score"] >= 0.20
            and face_ready
        )

        if (
            strong_passport
            or balanced_passport
            or mrz_first_passport
            or text_heavy_passport
            or keyword_face_passport
        ):
            # 任一规则成立即接受当前方向；返回 best 同时保留各项分数供调试和错误提示使用。
            best["accepted"] = True
            return True, _to_builtin(best)

    if height >= width:
        for rotation in (1, 3):
            analysis = _analyze_orientation(_rotate(image, rotation))
            final_score = (
                0.18 * analysis["face_score"]
                + 0.32 * analysis["mrz_band_score"]
                + 0.40 * analysis["mrz_text_score"]
                + 0.10 * (1.0 if analysis["keyword_hit"] else 0.0)
            )

            if final_score > best["final_score"]:
                best.update(analysis)
                best["rotation"] = rotation
                best["final_score"] = final_score

            face_ready = analysis["face_score"] >= 0.20 or not require_face
            strong_passport = (
                analysis["mrz_text_score"] >= 0.58
                and analysis["mrz_band_score"] >= 0.18
            )
            balanced_passport = (
                analysis["mrz_text_score"] >= 0.40
                and analysis["mrz_band_score"] >= 0.16
                and analysis["keyword_hit"]
            )
            mrz_first_passport = analysis["mrz_text_score"] >= 0.46 and (
                analysis["mrz_band_score"] >= 0.12 or len(analysis["mrz_lines"]) >= 2
            )
            text_heavy_passport = (
                analysis["mrz_text_score"] >= 0.62 and analysis["keyword_hit"]
            )
            keyword_face_passport = (
                analysis["keyword_hit"]
                and analysis["mrz_band_score"] >= 0.20
                and face_ready
            )

            if (
                strong_passport
                or balanced_passport
                or mrz_first_passport
                or text_heavy_passport
                or keyword_face_passport
            ):
                best["accepted"] = True
                return True, _to_builtin(best)

    best["reason"] = "passport_signals_insufficient"
    best = _to_builtin(best)
    return (False, best) if debug else (False, best)
