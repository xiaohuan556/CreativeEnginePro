import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const registryPath = new URL("../lib/node-registry.ts", import.meta.url);
const desktopPath = new URL("../../ai/ui/production_canvas.py", import.meta.url);
const canvasPath = new URL("../app/studio/StudioCanvas.tsx", import.meta.url);

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

test("web canvas exposes explicit media roles, safe result adoption, native audio, and synced writes", async () => {
  const canvas = await readFile(canvasPath, "utf8");
  for (const contract of ["first_frame", "last_frame", "script_candidate", "采用AI候选稿", "generate_audio", "audio_prompt", "compileShotPrompt", "saveCurrentProjectNow", "lastSyncedProjectRef", "node-media", "模型版本 / 端点 ID", "planning_model", "video_model", "/api/production-runs/quote", "production_completed_stage", "quoteData.quote.tasks"]) {
    assert.match(canvas, new RegExp(contract), `missing canvas contract ${contract}`);
  }
  assert.match(canvas, /selectedAction === "图生视频" \? "image_to_video" : "text_to_video"/);
  assert.match(canvas, /任务未提交，避免错误扣费/);
});

test("company deployment uses its own password control plane and keeps preview storage lazy", async () => {
  const [control, database, assetRoute] = await Promise.all([
    readFile(new URL("../app/studio/ControlPlane.tsx", import.meta.url), "utf8"),
    readFile(new URL("../db/index.ts", import.meta.url), "utf8"),
    readFile(new URL("../app/api/assets/route.ts", import.meta.url), "utf8"),
  ]);
  assert.match(control, /NEXT_PUBLIC_CONTROL_PLANE_URL/);
  assert.match(control, /\/api\/auth\/login/);
  assert.match(control, /name="username"/);
  assert.match(control, /name="password" type="password"/);
  assert.doesNotMatch(database, /^import \{ env \} from "cloudflare:workers"/m);
  assert.match(database, /await import\("cloudflare:workers"\)/);
  assert.match(assetRoute, /if \(!identity\) return unauthorized\(\)/);
  await assert.rejects(access(new URL("../app/chatgpt-auth.ts", import.meta.url)));
});
