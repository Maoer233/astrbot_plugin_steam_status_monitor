import io

import numpy as np
import httpx
from PIL import Image as PILImage

from ...shared.network import httpx_client_kwargs


async def fetch_image(url, proxy=None):
    """异步下载远端图片为 bytes（httpx），供裁剪前获取图片使用。

    使用异步客户端，避免在异步命令处理路径中做同步网络 I/O 阻塞事件循环。
    """
    async with httpx.AsyncClient(timeout=15, **httpx_client_kwargs(proxy)) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def crop_image_auto(img_path_or_bytes, bg_color=(20, 26, 33), threshold=25):
    """自动裁剪图片内容区域，去除边缘与背景相近的空白。

    入参支持 PIL.Image / 本地路径 / bytes。若需处理远端图片，请先用
    ``fetch_image``（httpx 异步）下载为 bytes 再传入，避免同步网络 I/O。
    """
    if isinstance(img_path_or_bytes, PILImage.Image):
        img = img_path_or_bytes.convert("RGB")
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
