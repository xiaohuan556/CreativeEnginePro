import fs from 'node:fs/promises';
import path from 'node:path';
import {createInterface} from 'node:readline/promises';
import {fileURLToPath} from 'node:url';
import {randomUUID} from 'node:crypto';

const bundle = path.dirname(fileURLToPath(import.meta.url));
const deployedDuo = String.raw`D:\需求对接\Duo-Windows-部署交付-2026-10-27(1)\Duo`;
const routes = ['deliveries', 'requests'].map(name => path.join('api', 'desktop', name, 'route.mjs'));

async function exists(file) {
  try { await fs.access(file); return true; }
  catch (error) { if (error.code === 'ENOENT') return false; throw error; }
}

async function chooseRoot() {
  if (process.argv[2]) return path.resolve(process.argv[2]);
  if (await exists(path.join(deployedDuo, 'server.mjs'))) return deployedDuo;
  if (await exists(path.join(process.cwd(), 'server.mjs'))) return process.cwd();
  const prompt = createInterface({input: process.stdin, output: process.stdout});
  try {
    const entered = (await prompt.question('请输入正在运行的 Duo 安装目录：')).trim().replace(/^"|"$/g, '');
    if (!entered) throw Error('未填写 Duo 安装目录');
    return path.resolve(entered);
  } finally { prompt.close(); }
}

async function install() {
  const root = await fs.realpath(await chooseRoot());
  const server = await fs.readFile(path.join(root, 'server.mjs'), 'utf8');
  const config = JSON.parse(await fs.readFile(path.join(root, 'config.json'), 'utf8'));
  if (!server.includes('dist/api') || !server.includes('route.mjs') || !config.publicUrl) {
    throw Error('此目录不是兼容的 Duo 部署目录');
  }
  for (const name of ['server.mjs', 'audit.mjs', 'policy.mjs']) {
    if (!await exists(path.join(root, 'dist', 'lib', name))) throw Error(`缺少 Duo 运行模块 ${name}`);
  }
  for (const route of routes) await fs.access(path.join(bundle, 'src', route));
  const targets = routes.flatMap(route => ['src', 'dist'].map(dir => ({
    relative: path.join(dir, route), source: path.join(bundle, 'src', route),
  })));
  const backup = path.join(root, 'backups', `desktop-delivery-${new Date().toISOString().replace(/[:.]/g, '-')}`);
  const installed = [];
  try {
    for (const target of targets) {
      const destination = path.join(root, target.relative);
      const temporary = destination + '.' + randomUUID() + '.tmp';
      const old = await exists(destination);
      if (old) {
        await fs.mkdir(path.dirname(path.join(backup, target.relative)), {recursive: true});
        await fs.copyFile(destination, path.join(backup, target.relative));
      }
      await fs.mkdir(path.dirname(destination), {recursive: true});
      try {
        await fs.copyFile(target.source, temporary);
        await fs.rename(temporary, destination);
      } finally { await fs.rm(temporary, {force: true}); }
      installed.push({destination, relative: target.relative, old});
    }
  } catch (error) {
    for (const item of installed.reverse()) {
      if (item.old) await fs.copyFile(path.join(backup, item.relative), item.destination);
      else await fs.rm(item.destination, {force: true});
    }
    throw error;
  }
  console.log(`已安装桌面交付及发布需求接口：${root}`);
  if (installed.some(item => item.old)) console.log(`旧版接口备份：${backup}`);
  console.log('请按服务器当前的启动方式重启 Duo，使接口生效。');
  console.log(`验证地址：${new URL('/api/desktop/deliveries', config.publicUrl).href}（未登录应返回 401）`);
}

install().catch(error => { console.error('Duo 更新失败：' + error.message); process.exitCode = 1; });
