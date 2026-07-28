# 故意留空：覆盖 _pyinstaller_hooks_contrib 自带的 std hook-onnxruntime.py。
# 标准 hook 会在 PyInstaller「分析阶段」导入 onnxruntime 的原生 .pyd，而该 .pyd 在
# 分析子进程（已加载 cv2/PyQt6 等大量原生库）里会触发 access violation，连带拖垮打包。
# onnxruntime 改为在 spec 里以 datas 整体拷贝进 bundle；运行时由
# hooks/onnxruntime_runtime.py 设置原生 DLL 的搜索路径，无需分析期导入。
datas = []
binaries = []
hiddenimports = []
