"""Pure array operations for editable image effects."""
import numpy as np


def pixel_matrix(document_matrix):
    """Convert a document-edge transform to OpenCV's pixel-center coordinates."""
    matrix = np.asarray(document_matrix,dtype=np.float64)
    affine = matrix.shape==(2,3)
    if affine:
        matrix = np.vstack((matrix,[0,0,1]))
    to_document = np.array([[1,0,.5],[0,1,.5],[0,0,1]],np.float64)
    to_pixel = np.array([[1,0,-.5],[0,1,-.5],[0,0,1]],np.float64)
    result = to_pixel@matrix@to_document
    return (result[:2] if affine else result).astype(np.float32)


def curve_table(points):
    """Shape-preserving cubic interpolation; endpoints cover the byte range."""
    values = np.asarray(points, dtype=np.float64)
    if (values.ndim != 2 or values.shape[1] != 2 or not 2 <= len(values) <= 32
            or not np.isfinite(values).all() or (values < 0).any() or (values > 255).any()
            or values[0, 0] != 0 or values[-1, 0] != 255 or (np.diff(values[:, 0]) <= 0).any()):
        raise ValueError('曲线点必须按输入值递增，范围为 0–255，且包含两个端点')
    x, y = values.T
    widths = np.diff(x)
    secants = np.diff(y) / widths
    slopes = np.zeros(len(x))
    slopes[0], slopes[-1] = secants[0], secants[-1]
    for index in range(1, len(x) - 1):
        if secants[index - 1] * secants[index] > 0:
            a, b = 2 * widths[index] + widths[index - 1], widths[index] + 2 * widths[index - 1]
            slopes[index] = (a + b) / (a / secants[index - 1] + b / secants[index])
    samples = np.arange(256)
    interval = np.clip(np.searchsorted(x, samples, side='right') - 1, 0, len(x) - 2)
    h = widths[interval]
    t = (samples - x[interval]) / h
    result = ((2*t**3 - 3*t**2 + 1) * y[interval]
              + (t**3 - 2*t**2 + t) * h * slopes[interval]
              + (-2*t**3 + 3*t**2) * y[interval + 1]
              + (t**3 - t**2) * h * slopes[interval + 1])
    return np.clip(result, 0, 255)


def apply_curves(rgb, channels):
    identity = [[0, 0], [255, 255]]
    master = curve_table(channels.get('rgb', identity))
    result = np.empty_like(rgb, dtype=np.float32)
    for channel, name in enumerate(('red', 'green', 'blue')):
        table = curve_table(channels.get(name, identity))
        table = np.interp(table, np.arange(256), master)
        result[:, :, channel] = np.interp(rgb[:, :, channel], np.arange(256), table)
    return result


def mix_rgba(original, filtered, strength):
    """Blend effect strength in premultiplied space to keep soft edges clean."""
    original = original.astype(np.float32) / 255
    filtered = filtered.astype(np.float32) / 255
    a, b = original[:, :, 3:4], filtered[:, :, 3:4]
    alpha = a * (1 - strength) + b * strength
    color = original[:, :, :3] * a * (1 - strength) + filtered[:, :, :3] * b * strength
    out = np.concatenate((np.divide(color, alpha, out=np.zeros_like(color), where=alpha > 0), alpha), axis=2)
    return np.rint(np.clip(out, 0, 1) * 255).astype(np.uint8)


def blur_rgba(pixels, kind, params):
    import cv2
    premultiplied = pixels.astype(np.float32) / 255
    premultiplied[:, :, :3] *= premultiplied[:, :, 3:4]
    if kind == 'gaussian_blur':
        radius = max(1, int(params.get('radius', 3)))
        filtered = cv2.GaussianBlur(premultiplied, (radius * 2 + 1, radius * 2 + 1), 0)
    else:
        size = max(3, int(params.get('size', 9)))
        kernel = np.zeros((size, size), np.float32)
        kernel[size // 2, :] = 1
        rotation = cv2.getRotationMatrix2D((size / 2 - .5, size / 2 - .5), float(params.get('angle', 0)), 1)
        kernel = cv2.warpAffine(kernel, rotation, (size, size))
        kernel /= max(float(kernel.sum()), 1e-8)
        filtered = cv2.filter2D(premultiplied, -1, kernel)
    alpha = filtered[:, :, 3:4]
    filtered[:, :, :3] = np.divide(filtered[:, :, :3], alpha,
                                  out=np.zeros_like(filtered[:, :, :3]), where=alpha > 1e-8)
    return np.rint(np.clip(filtered, 0, 1) * 255).astype(np.uint8)
