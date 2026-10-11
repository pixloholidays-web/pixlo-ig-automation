"""Foreground cutouts, so Reel text can sit *behind* the subject (the "title behind the mountain" look).

Two ways to find the foreground, both on CPU:
- sky: the depth map already knows what is far away, so everything that isn't sky is foreground. This is
  the reliable case for landscapes (ridgelines, temples and trees against the sky).
- subject: U2-Net (the model behind the open-source `rembg` tool) finds the salient object when there is no sky.
The rough mask is then snapped to real edges with OpenCV's GrabCut.
"""
import cv2
import numpy as np
from PIL import Image

U2NET_URL = "https://github.com/danielgatis/rembg/releases/download/v0.0.0/u2netp.onnx"
SKY_DEPTH = 0.05  # raw depth below this (0 = farthest) counts as sky

_session = None


def _u2net_session():
    global _session
    if _session is None:
        import onnxruntime as ort
        import requests

        from .config import ROOT
        path = ROOT / "media" / "cache" / "models" / "u2netp.onnx"
        if not path.exists():
            resp = requests.get(U2NET_URL, timeout=120)
            resp.raise_for_status()
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(resp.content)
        _session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    return _session


def saliency(img: Image.Image) -> np.ndarray:
    """Subject probability in [0, 1], same size as img."""
    x = np.asarray(img.resize((320, 320), Image.BILINEAR), np.float32) / 255
    x = (x - np.array([0.485, 0.456, 0.406], np.float32)) / np.array([0.229, 0.224, 0.225], np.float32)
    s = _u2net_session()
    out = s.run(None, {s.get_inputs()[0].name: x.transpose(2, 0, 1)[None]})[0][0, 0]
    out = (out - out.min()) / (out.max() - out.min() + 1e-6)
    return cv2.resize(out, img.size, interpolation=cv2.INTER_LINEAR)


def _refine(img: Image.Image, rough: np.ndarray) -> np.ndarray:
    """Snap a rough 0/1 mask to the photo's real edges with GrabCut. Returns a soft mask in [0, 1]."""
    w, h = img.size
    scale = 480 / max(w, h)
    sw, sh = max(2, round(w * scale)), max(2, round(h * scale))
    small = cv2.cvtColor(np.asarray(img.resize((sw, sh), Image.BILINEAR)), cv2.COLOR_RGB2BGR)
    m = cv2.resize(rough.astype(np.uint8), (sw, sh), interpolation=cv2.INTER_NEAREST)
    kernel = np.ones((9, 9), np.uint8)
    gc = np.where(m > 0, cv2.GC_PR_FGD, cv2.GC_PR_BGD).astype(np.uint8)
    gc[cv2.erode(m, kernel) > 0] = cv2.GC_FGD          # well inside the foreground: certain
    gc[cv2.dilate(m, kernel) == 0] = cv2.GC_BGD        # well outside: certain
    if (gc == cv2.GC_FGD).sum() < 50 or (gc == cv2.GC_BGD).sum() < 50:
        return cv2.GaussianBlur(rough.astype(np.float32), (0, 0), 2)
    try:
        cv2.grabCut(small, gc, None, np.zeros((1, 65)), np.zeros((1, 65)), 2, cv2.GC_INIT_WITH_MASK)
    except cv2.error:
        return cv2.GaussianBlur(rough.astype(np.float32), (0, 0), 2)
    fine = np.isin(gc, (cv2.GC_FGD, cv2.GC_PR_FGD)).astype(np.float32)
    fine = cv2.resize(fine, (w, h), interpolation=cv2.INTER_LINEAR)
    return cv2.GaussianBlur(fine, (0, 0), max(1.0, w / 900))  # soft 1-2 px edge, no jaggies


def foreground(img: Image.Image, raw_depth: np.ndarray) -> tuple[np.ndarray, str] | None:
    """(soft foreground mask, "sky" or "subject"), or None if the photo has no clear foreground."""
    sky = raw_depth < SKY_DEPTH
    # Sky must be a real band at the top of the photo, not a few far pixels
    top = sky[: sky.shape[0] // 2]
    if 0.10 < sky.mean() < 0.75 and top.mean() > 0.25:
        # ...and it must look like sky (bright or blue), not a dark far cliff or forest
        r, g, b = (float(c) for c in np.asarray(img, np.float32)[sky].mean(axis=0))
        if (r + g + b) / 3 > 105 or b > r + 12:
            return _refine(img, ~sky), "sky"
    try:
        sal = saliency(img) > 0.5
    except Exception:  # model download failed: just skip the effect
        return None
    if 0.08 < sal.mean() < 0.55:
        return _refine(img, sal), "subject"
    return None
