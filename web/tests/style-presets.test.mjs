import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const stylePath = new URL("../lib/style-presets.ts", import.meta.url);
const canvasPath = new URL("../app/studio/StudioCanvas.tsx", import.meta.url);

test("style explorer ships a broad material and craft prompt library", async () => {
  const source = await readFile(stylePath, "utf8");
  assert.ok((source.match(/preset\(\{/g) || []).length >= 48);
  for (const label of ["吹制玻璃", "熔岩核心", "积木玩具", "手捏黏土", "针织毛线", "羊毛毡", "彩色玻璃窗", "菌丝结构", "全息镭射"]) {
    assert.match(source, new RegExp(label), `missing style ${label}`);
  }
  for (const category of ["材质幻化", "手作工艺", "玩具微缩", "绘画印刷", "自然实验", "未来视觉"]) {
    assert.match(source, new RegExp(category), `missing category ${category}`);
  }
  for (const dimension of ["medium", "material", "finish", "palette", "lighting"]) {
    assert.match(source, new RegExp(`${dimension}: \\[`), `missing exploration dimension ${dimension}`);
  }
});

test("image editing compiles hold-plus-change constraints and stable exploration recipes", async () => {
  const [source, canvas] = await Promise.all([readFile(stylePath, "utf8"), readFile(canvasPath, "utf8")]);
  assert.match(source, /以输入图片为唯一结构基准/);
  assert.match(source, /保持完全不变/);
  assert.match(source, /只改变/);
  assert.match(source, /反射、粗糙度、透明度、厚度、接缝与重力关系/);
  assert.match(canvas, /compileStylePrompt/);
  assert.match(canvas, /style_prompt/);
  assert.match(canvas, /createStyleRecipe/);
  assert.match(canvas, /风格探索器/);
  assert.match(canvas, /锁住原图/);
});
