import assert from "node:assert/strict";
import { access, readFile } from "node:fs/promises";
import test from "node:test";

const registryPath = new URL("../lib/node-registry.ts", import.meta.url);
const desktopPath = new URL("../../ai/ui/production_canvas.py", import.meta.url);
const canvasPath = new URL("../app/studio/StudioCanvas.tsx", import.meta.url);
const pulseEdgePath = new URL("../app/studio/PulseEdge.tsx", import.meta.url);

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
  assert.match(canvas, /function chineseGenerationError/);
  assert.match(canvas, /图片请求未通过安全审核/);
  assert.match(canvas, /moderation_blocked/);
  assert.match(canvas, /NodeEditorPortal/);
  assert.match(canvas, /node-editor-slot-/);
  assert.match(canvas, /node-inline-editor/);
  assert.doesNotMatch(canvas, /<aside className="inspector">/);
  assert.match(canvas, /selectionOnDrag=\{canWrite\}/);
  assert.match(canvas, /SelectionMode\.Partial/);
  assert.match(canvas, /deleteSelectedNodes/);
  assert.match(canvas, /onPaneClick=\{\(\) => \{ setSelectedId\(""\)/);
  assert.match(canvas, /deleteKeyCode=\{canWrite \? "Delete" : null\}/);
  assert.doesNotMatch(canvas, /deleteKeyCode=.*Backspace/);
  assert.match(canvas, /AI 无限画布/);
  assert.match(canvas, /className="editor-section compact-corner"/);
  assert.match(canvas, /hasGenerationParameters && <details/);
  assert.match(canvas, /hasScriptResult && <details/);
  assert.match(canvas, /className="asset-chip compact-corner"/);
  assert.match(canvas, /className="compact-action-selector"/);
  assert.doesNotMatch(canvas, /<div className="node-actions">/);
  assert.match(canvas, /selectedNode\.data\.specKey !== "storyboard"/);
  assert.doesNotMatch(canvas, /制片流程控制/);
  assert.doesNotMatch(canvas, /aria-label="重做阶段"/);
  assert.match(canvas, /className="redo-dialog"/);
  assert.match(canvas, /确认重做并继续/);
  assert.match(canvas, /className="node-media-frame nodrag nowheel"/);
  assert.match(canvas, /\?download=true/);
  assert.match(canvas, /下载素材/);
  assert.match(canvas, /edgesVisible \? edges : \[\]/);
  assert.match(canvas, /mapVisible && <MiniMap/);
  assert.match(canvas, /setMapVisible/);
  assert.match(canvas, /按时间安排每张图/);
  assert.match(canvas, /这段画面怎么动/);
  assert.match(canvas, /镜头怎么拍/);
  assert.match(canvas, /onNodeContextMenu/);
  assert.match(canvas, /className="canvas-context-menu"/);
  assert.match(canvas, /position="bottom-left" nodeColor/);
  assert.match(canvas, /importStartY/);
  assert.match(canvas, /className="multi-image-compact"/);
  assert.match(canvas, /className="quick-settings-menu"/);
  assert.match(canvas, /screenToFlowPosition/);
  assert.match(canvas, /handleFileDrop/);
  assert.match(canvas, /importMedia\(files, position\)/);
  assert.match(canvas, /图片、视频和音频会自动生成对应节点/);
  assert.match(canvas, /imported_by_drop/);
  for (const beginnerLabel of ["一句话完成短片", "文字生成图片", "图片生成图片", "一句话生成视频", "一张图生成视频", "首尾帧生成视频", "文字生成语音", "上传视频自动拉片"]) {
    assert.match(canvas, new RegExp(beginnerLabel), `missing beginner entry ${beginnerLabel}`);
  }
  assert.match(canvas, /beginnerSteps/);
  assert.match(canvas, /data: \{ relation \}/);
  assert.match(canvas, /first_last_frame_video/);
  const pulseEdge = await readFile(pulseEdgePath, "utf8");
  assert.match(pulseEdge, /getBezierPath/);
  assert.doesNotMatch(pulseEdge, /getSmoothStepPath/);
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
