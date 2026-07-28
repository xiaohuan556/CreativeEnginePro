import sys, numpy as np, onnxruntime as ort, cv2
img, model = sys.argv[1], sys.argv[2]
bgr = cv2.imread(img)
IN = 640
rs = cv2.resize(bgr, (IN, IN))
blob = (rs.astype(np.float32) / 255.0).transpose(2, 0, 1)[None, ...].astype(np.float32)
sess = ort.InferenceSession(model, providers=["CPUExecutionProvider"])
out = sess.run(None, {"input": blob})
d = {n: v for n, v in zip([x.name for x in sess.get_outputs()], out)}


def sig(x):
    return 1 / (1 + np.exp(-x))


cx, cy = 349.5, 355.5
for stride, key in [(8, "bbox_8"), (16, "bbox_16"), (32, "bbox_32")]:
    ck = key.replace("bbox_", "cls_"); ok = key.replace("bbox_", "obj_")
    cls = sig(d[ck][0].reshape(-1)); obj = sig(d[ok][0].reshape(-1))
    bb = d[key][0]
    gw = gh = IN // stride
    cc, cr = np.meshgrid(np.arange(gw), np.arange(gh))
    cc = cc.reshape(-1); cr = cr.reshape(-1)
    x = (cc + sig(bb[:, 0])) * stride; y = (cr + sig(bb[:, 1])) * stride
    dist = np.hypot(x - cx, y - cy)
    jn = int(np.argmin(dist))
    print(f"stride {stride}: nearCenter cls={cls[jn]:.3f} obj={obj[jn]:.3f} prod={cls[jn]*obj[jn]:.3f} box640=({x[jn]:.0f},{y[jn]:.0f})")
    print(f"   GLOBAL max: cls={cls.max():.3f} obj={obj.max():.3f} prod={max(cls*obj):.3f}")
