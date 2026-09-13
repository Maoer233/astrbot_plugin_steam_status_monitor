import io

import numpy as np
import requests
from PIL import Image as PILImage

from ...shared.network import requests_verify


def crop_image_auto(img_path_or_bytes, bg_color=(20, 26, 33), threshold=25):
    """自动裁剪图片内容区域，去除边缘与背景相近的空白。"""
    if isinstance(img_path_or_bytes, PILImage.Image):
        img = img_path_or_bytes.convert("RGB")
    elif isinstance(img_path_or_bytes, str) and (
        img_path_or_bytes.startswith("http://") or img_path_or_bytes.startswith("https://")
    ):
        resp = requests.get(img_path_or_bytes, timeout=15, verify=requests_verify())
        resp.raise_for_status()
        img = PILImage.open(io.BytesIO(resp.content)).convert("RGB")
    elif isinstance(img_path_or_bytes, bytes):
        img = PILImage.open(io.BytesIO(img_path_or_bytes)).convert("RGB")
    else:
        img = PILImage.open(img_path_or_bytes).convert("RGB")
    arr = np.array(img)
    h, w, _ = arr.shape
    corners = [arr[0, 0], arr[0, -1], arr[-1, 0], arr[-1, -1]]
    avg_bg = np.mean(corners, axis=0)
    diff = np.abs(arr - avg_bg).sum(axis=2)
    mask = diff > threshold
    coords = np.argwhere(mask)
    if coords.size == 0:
        return img
    y0, x0 = coords.min(axis=0)
    y1, x1 = coords.max(axis=0) + 1
    y0 = max(y0, 0)
    x0 = max(x0, 0)
    y1 = min(y1, arr.shape[0])
    x1 = min(x1, arr.shape[1])
    return img.crop((x0, y0, x1, y1))
