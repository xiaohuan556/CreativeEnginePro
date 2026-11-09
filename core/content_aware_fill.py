"""Fill selections by extending nearby image texture with overlapping patches."""

import cv2
import numpy as np


def _best_donor(rgb, integral, template, context, left, top, radius):
    """Find a fully valid donor, matching known colors and favoring short moves."""
    height, width = rgb.shape[:2]
    h, w = context.shape
    sx, sy = max(0, left - radius), max(0, top - radius)
    ex, ey = min(width, left + w + radius), min(height, top + h + radius)
    source = rgb[sy:ey, sx:ex]
    area = integral[sy:ey + 1, sx:ex + 1]
    bad = area[h:, w:] - area[:-h, w:] - area[h:, :-w] + area[:-h, :-w]
    if not (bad == 0).any() or not context.any():
        return None, np.inf

    scores = cv2.matchTemplate(source, template, cv2.TM_SQDIFF, mask=context)
    scores = np.maximum(scores, 0) / (3 * np.square(context, dtype=np.float64).sum())
    # A patch may match its border yet hide an unrelated bright or dark object
    # in the hole. Penalize these donors when the surrounding tone is uniform.
    template_gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    low, high = np.percentile(template_gray[context > 0], (5, 95))
    hole = context == 0
    if high - low < 55 and hole.any():
        tolerance = max(18, (high - low) / 2)
        source_gray = cv2.cvtColor(source, cv2.COLOR_RGB2GRAY)
        outliers = ((source_gray < low - tolerance) | (source_gray > high + tolerance))
        contamination = cv2.matchTemplate(outliers.astype(np.float32), hole.astype(np.float32),
                                          cv2.TM_CCORR)
        scores += np.maximum(contamination, 0) * (10000 / int(hole.sum()))
    dy, dx = np.ogrid[sy - top:ey - h + 1 - top, sx - left:ex - w + 1 - left]
    # A distant patch must match better than a patch near the spot being repaired.
    scores += (dx * dx + dy * dy) * (144.0 / radius**2)
    scores[bad != 0] = np.inf
    row, col = np.unravel_index(np.argmin(scores), scores.shape)
    return source[row:row + h, col:col + w], scores[row, col]


def _extend_texture(rgb, result, usable, integral, selection, bounds):
    """Propagate from the surrounding pixels, repairing a few pixels at a time."""
    x, y, w, h = bounds
    height, width = usable.shape
    patch = min(11, height, width)
    half = patch // 2
    left, top = max(0, x - 11), max(0, y - 11)
    right, bottom = min(width, x + w + 11), min(height, y + h + 11)
    unknown = selection.copy()
    original = usable[top:bottom, left:right]
    known = original.astype(np.float32)
    work = result[top:bottom, left:right]
    gray = cv2.cvtColor(work, cv2.COLOR_RGB2GRAY)
    dx = cv2.Sobel(gray, cv2.CV_32F, 1, 0)
    dy = cv2.Sobel(gray, cv2.CV_32F, 0, 1)
    clean = cv2.erode(original.astype(np.uint8), np.ones((3, 3), np.uint8))
    dx *= clean
    dy *= clean
    dx = cv2.blur(dx, (5, 5))
    dy = cv2.blur(dy, (5, 5))
    kernel = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))

    # The iteration cap bounds work for very large selections.
    for _ in range(min(700, int(unknown.sum()))):
        frontier = unknown & cv2.dilate((known > 0).astype(np.uint8), kernel).astype(bool)
        if not frontier.any():
            break
        hole = unknown.astype(np.float32)
        nx = cv2.Sobel(hole, cv2.CV_32F, 1, 0)
        ny = cv2.Sobel(hole, cv2.CV_32F, 0, 1)
        # Extend strong edges into the hole before filling flatter regions.
        edge = np.abs(dx * ny - dy * nx) / np.maximum(1, np.hypot(nx, ny))
        priority = cv2.blur(known, (patch, patch)) * (1 + np.minimum(edge / 24, 8))
        priority[~frontier] = -1
        _, _, _, (cx, cy) = cv2.minMaxLoc(priority)
        px = max(0, min(cx - half, work.shape[1] - patch))
        py = max(0, min(cy - half, work.shape[0] - patch))
        region = np.s_[py:py + patch, px:px + patch]
        target = unknown[region]
        context = known[region]
        donor, _ = _best_donor(rgb, integral, work[region], context,
                                left + px, top + py, 64)
        if donor is None:
            donor, _ = _best_donor(rgb, integral, work[region], context,
                                    left + px, top + py, 128)
        if donor is None:
            break
        work[region][target] = donor[target]
        context[target] = 0.65
        target[:] = False
    return unknown


def fill_from_background(rgb, mask, *, valid=None):
    """Return RGB with only masked pixels changed; valid marks usable donor pixels."""
    rgb = np.ascontiguousarray(rgb)
    mask = np.asarray(mask, dtype=bool)
    result = rgb.copy()
    if not mask.any():
        return result

    usable = ~mask if valid is None else (~mask & valid)
    integral = cv2.integral((~usable).astype(np.uint8))
    _, labels, stats, _ = cv2.connectedComponentsWithStats(mask.astype(np.uint8), connectivity=8)
    height, width = mask.shape
    fallback = np.zeros_like(mask)
    for label, (x, y, w, h, _) in enumerate(stats[1:], 1):
        border = max(3, min(12, min(w, h) // 4))
        left, top = max(0, x - border), max(0, y - border)
        right, bottom = min(width, x + w + border), min(height, y + h + border)
        region = np.s_[top:bottom, left:right]
        selected = labels[region] == label
        # Preserve an entire repeating pattern when its nearby border matches.
        donor, error = None, np.inf
        if max(w, h) <= 256:
            donor, error = _best_donor(rgb, integral, rgb[region],
                                       usable[region].astype(np.uint8), left, top, 96)
        if error <= 36:
            result[region][selected] = donor[selected]
            continue

        region = np.s_[max(0, y - 11):min(height, y + h + 11),
                       max(0, x - 11):min(width, x + w + 11)]
        remaining = _extend_texture(rgb, result, usable, integral, labels[region] == label,
                                    (x, y, w, h))
        fallback[region] |= remaining

    if fallback.any():
        smooth = cv2.inpaint(result, fallback.astype(np.uint8), 3, cv2.INPAINT_TELEA)
        result[fallback] = smooth[fallback]
    return result

