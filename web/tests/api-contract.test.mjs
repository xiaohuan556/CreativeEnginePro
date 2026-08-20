import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (path) => readFile(new URL(path, import.meta.url), "utf8");

test("every registered node has an explicit execution contract", async () => {
  const registry = await read("../lib/node-registry.ts");
  const keys = [...registry.matchAll(/spec\(\{ key: "([^"]+)"/g)].map((match) => match[1]);
  const matrix = registry.slice(registry.indexOf("export const NODE_EXECUTION_CONTRACTS"), registry.indexOf("export function inferNodeSpec"));
  assert.equal(keys.length, 23);
  for (const key of keys) assert.match(matrix, new RegExp(`\\n  ${key}: \\{`), `missing execution contract for ${key}`);
});

test("every canvas task kind is implemented by the server runtime", async () => {
  const [canvas, compiler, worker, validation, sync] = await Promise.all([
    read("../app/studio/StudioCanvas.tsx"),
    read("../../server/creative_server/request_compiler.py"),
    read("../../server/creative_server/worker.py"),
    read("../../server/creative_server/task_validation.py"),
    read("../../server/creative_server/canvas_sync.py"),
  ]);
  const operations = [
    "chat", "text_to_image", "image_edit", "text_to_video", "image_to_video",
    "continue_video", "extract_video_frames", "text_to_speech", "video_breakdown",
  ];
  for (const operation of operations) {
    assert.match(canvas, new RegExp(`["]${operation}["]`), `canvas does not submit ${operation}`);
    assert.ok(
      compiler.includes(operation) || worker.includes(operation) || validation.includes(operation),
      `server does not implement ${operation}`,
    );
  }
  for (const mediaKind of ["image", "video", "audio"]) assert.match(sync, new RegExp(mediaKind));
  assert.match(sync, /server_task_id/);
  assert.doesNotMatch(canvas, /model:\s*String\(payload\.voice\s*\|\|\s*""\)/, "voice id must not be submitted as model id");
});

test("all canvas control-plane calls have matching server routes", async () => {
  const [canvas, main] = await Promise.all([
    read("../app/studio/StudioCanvas.tsx"),
    read("../../server/creative_server/main.py"),
  ]);
  const routeContracts = [
    "/api/providers", "/api/projects", "/api/assets", "/save-to-library",
    "/api/production-runs", "/api/production-runs/quote", "/command",
    "/api/workflow-runs", "/api/workflow-runs/quote", "/api/workflow-templates",
    "/api/tasks", "/api/tasks/quote",
  ];
  for (const route of routeContracts) {
    assert.match(canvas, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `canvas missing ${route}`);
    assert.match(main, new RegExp(route.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")), `server missing ${route}`);
  }
  assert.match(canvas, /action === "暂停" \? "pause" : action === "继续" \? "resume" : action === "取消" \? "cancel" : "retry"/);
  for (const operation of ["cancel", "pause", "resume", "retry"]) assert.match(main, new RegExp(`/api/tasks/\\{task_id\\}/${operation}`));
});

test("node actions require an explicit execute click and system nodes do not pretend to generate", async () => {
  const canvas = await read("../app/studio/StudioCanvas.tsx");
  assert.match(canvas, /DIRECT_NODE_ACTIONS/);
  assert.match(canvas, /onChange=\{\(event\) => updatePayload\("editor_action", event\.target\.value\)\}/);
  assert.doesNotMatch(canvas, /onChange=\{\(event\) => void handleNodeAction\(event\.target\.value\)\}/);
  assert.match(canvas, /hasNodeCommand && <div className="editor-command-bar">/);
  assert.match(canvas, /showAssetInput &&/);
  assert.match(canvas, /provider_name: "", model: "", skill_source/);
});
