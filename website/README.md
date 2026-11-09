# 小欢pro 产品官网

双击 `index.html` 可以直接用浏览器打开。也可以在项目根目录运行以下命令，然后访问 `http://127.0.0.1:4173/`。

```powershell
node website/site-tools.mjs
```

- `index.html` 是官网首页。
- `product.html` 介绍剪辑、图片和 AI 制片画布，包含网页交互示意。
- `download.html` 是下载中心、使用指南和常见问题。

## 接入 Windows 客户端

在 `release.js` 中填写 `downloadUrl`，使用公开可访问的 HTTPS 下载地址，或把 `.exe` 放入 `website/downloads/` 并填写对应的相对地址。可同时填写 `version`、`size` 和 `sha256` 校验值。免安装 EXE 设为 `standalone: true`，安装包设为 `standalone: false`。下载地址留空时，下载中心会显示「正式安装包待发布」。

当前接入的是 `1.0.0-demo-20261109-144515`，源文件为 `build/demo-pro-20261109-144515/小欢专业版pro.exe`。它需要内网登录，有效期至北京时间 2026 年 12 月 1 日结束。大型 EXE 留在本机下载目录，Git 不跟踪该目录。若从新检出重建本站，需要恢复该文件或将下载地址改为可访问的正式发布地址。

正式版客户端的认证服务器由桌面端打包时指定。网站域名可以单独配置。

## 生成部署目录

```powershell
node website/site-tools.mjs build
```

将生成的 `website/dist/` 目录部署到静态网站托管服务，再绑定域名。

## 视觉素材

参考素材和 AI 成图由内置图像生成工具制作，并压缩为 WebP 随网站交付。

- [Frame Study](assets/frame-study.webp) · 新版首页主视觉，以折叠的金属片和铜橙反光表现时间线。
- [Violet Form](assets/violet-form.webp) · AI 画布参考图。
- [After Dark 原图](assets/after-dark.webp) 和 [图片工作台导出](assets/after-dark-graded.webp) · 成品由项目的图片工作台渲染器实际合成，应用照片显影、渐变映射、高光柔光和文字图层。
- [The Violet Gate](assets/violet-gate.webp) · AI 制片画布中以 Violet Form 为参考生成的峡谷场景。
- [《夜驰》封面](assets/night-shift.webp) 和 [10 秒剪辑样片](assets/night-shift.mp4) · 来自用户提供的 Seedance 成片，封面从视频取帧，保留视频和原声。

### 生成提示词

**Frame Study**

> Create a single ultra-wide cinematic 3D brand image for a high-end creative studio software website. Landscape composition as wide as possible. On the RIGHT HALF ONLY, an extraordinary sculptural cascade of broad, folded polished graphite-metal film ribbons fans diagonally from lower left to upper right, like frames of time opening in a camera shutter. Each blade has a satin graphite face, precise machined edges, and vivid incandescent amber-orange light reflecting along its inner folds. A few large sweeping folds with strong graphic silhouette, no circular torus, no obvious X, no stars, no sparks, no busy particle field. Background is a seamless nearly-black neutral blue-grey studio void and must be particularly dark and empty throughout the LEFT 48 percent, reserved for legible white HTML text. Refined photoreal CGI, art-directed, substantial tactile materials, gentle warm bloom and atmospheric depth, no letters, no logo, no UI, no border, no watermark. Make the form large, dramatic, and clearly visible on the right with room around it.

**Violet Form**

> Create a premium cinematic 3D illustration for CreativeEnginePro product website, a single landscape 3:2 image. Purely artwork, no text, no logo, no border, no interface, no collage. A massive twisted torus sculpture composed of intertwined shimmering violet and silver glass ribbons, complex optical facets and smooth rounded folds, centrally framed and filling 72% of the image, dramatic vivid lavender and indigo light with a slight warm tangerine glint on one edge, on a dark inky purple studio cyclorama. Slight fog, polished reflective black floor, fine cinematic grain, tactile refracted gradients and elegant art-direction like high-end experimental motion graphics still for a contemporary design studio. Keep the whole sculptural ring in frame and let background fade almost black at all edges. Powerful, spare, and visually striking.

**After Dark**

> Landscape 3:2 premium CGI product campaign key visual for a creative studio website. A small unbranded cylindrical perfume bottle in deep blue-grey translucent glass, on an angular sculptural piece of pale frosted rock, a few reflective liquid droplets suspended nearby. Understated cinematic light, icy cyan from the left and soft violet rim from right, nearly black gradient backdrop, dramatic close-up with bottle centered and entirely in view. Photorealistic refractive glass, crisp editorial advertising art direction, restrained palette. No text, no label, no watermark, no letters, no interface.

**The Violet Gate**

> Create a landscape 3:2 cinematic science-fiction still for a creative software website, no type and no logo. Use the attached image of a purple glass twisted torus sculpture as the actual visual reference: preserve the distinctive iridescent coiled torus form, purple glass and faint warm glints. Transform the small studio object into a colossal levitating portal in a monumental black basalt canyon. Violet light spills through from an amber dusk horizon. A single tiny expedition figure in foreground stands on dark mirror-like wet stone, giving a sense of scale. Volumetric atmosphere, high-end photoreal VFX, wide cinematic framing with silhouette clearly legible even when thumbnailed, bright enough to retain rock texture. Dark violet, slate blue, amber as limited accent. No text, no interface, no watermarks.
