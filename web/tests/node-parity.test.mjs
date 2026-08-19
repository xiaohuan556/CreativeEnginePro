import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const registryPath = new URL("../lib/node-registry.ts", import.meta.url);
const desktopPath = new URL("../../ai/ui/production_canvas.py", import.meta.url);

test("web registry covers every desktop canvas node family and primary workflow", async () => {
  const [registry, desktop] = await Promise.all([readFile(registryPath, "utf8"), readFile(desktopPath, "utf8")]);
  for (const desktopType of ["director", "scene", "character", "element", "shot", "asset_view", "asset_take", "shot_take", "generation_task", "text_node", "storyboard_node", "workflow_group", "image_node", "video_node", "video_analysis_node", "audio_node", "skill_node"]) {
    assert.match(desktop, new RegExp(`\\"${desktopType}\\"\\s*:`));
    assert.match(registry, new RegExp(`desktopType:\\s*\\"${desktopType}\\"`), `missing ${desktopType}`);
  }
  for (const label of ["AI 故事板", "剧本工作台", "信息流口播文案", "多图生成图片", "多图导演视频", "AI 自动拉片", "基于尾帧续拍"]) {
    assert.match(registry, new RegExp(label));
  }
});
