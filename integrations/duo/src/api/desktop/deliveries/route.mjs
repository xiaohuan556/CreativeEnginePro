import {auditInsert, ensureAuditSchema} from '../../../lib/audit.mjs';
import {authorizeState, blankState} from '../../../lib/policy.mjs';
import {bindings, body, Failure, handled, json, member, mutation} from '../../../lib/server.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const EXTENSIONS = {
  'image/jpeg': 'jpg', 'image/png': 'png', 'image/webp': 'webp', 'image/gif': 'gif',
  'video/mp4': 'mp4', 'video/webm': 'webm',
};
const MAX_FILES = 200;
const dateFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
});

function canDeliver(user, request) {
  return !request.example && (user.role === 'admin' ||
    (user.role === 'designer' && request.designer === user.name));
}

function workspace(db) {
  const row = db.prepare("SELECT version,payload FROM shared_state WHERE id='workspace'").first();
  return {version: row?.version, data: row ? JSON.parse(row.payload) : blankState()};
}

function ensureDeliverySchema(db) {
  db.prepare(`CREATE TABLE IF NOT EXISTS desktop_deliveries (
    actor_id TEXT NOT NULL, batch_id TEXT NOT NULL, request_id INTEGER NOT NULL,
    upload_ids TEXT NOT NULL, receipt TEXT NOT NULL, PRIMARY KEY(actor_id,batch_id)
  )`).run();
  db.prepare('CREATE INDEX IF NOT EXISTS desktop_deliveries_request ON desktop_deliveries(request_id)').run();
}

function materialPrefix(request, catalog) {
  const {products = [], optimizers = {}, designers = {}, channels = {}} = catalog || {};
  const platform = {'安卓': 'AND', '苹果': 'IOS', AND: 'AND', IOS: 'IOS', BOTH: 'BOTH'}[request.platform];
  const item = products.find(product => product.id === request.product);
  const dual = platform === 'BOTH' && ['AND', 'IOS'].every(value =>
    products.some(product => product.name === item?.name && product.platform === value));
  if (!item || !platform || (item.platform && item.platform !== 'BOTH' && item.platform !== platform && !dual)) {
    throw new Failure(400, '产品与平台不匹配，请在 Duo 检查需求');
  }
  const name = (item.shortName || item.name.replace(/[-_](?:ios|and)$/i, ''))
    .trim().replace(/[\\/:*?"<>|\s\x00-\x1f]+/g, '_');
  const codes = [channels[request.channel], optimizers[request.requester], designers[request.designer]];
  if (!name || codes.some(code => typeof code !== 'string' || !/^[\w-]+$/.test(code))) {
    throw new Failure(400, '人员或渠道代号不完整，请在 Duo 检查需求');
  }
  return [name, platform === 'BOTH' ? 'AND' : platform, ...codes].join('_');
}

function alreadyDelivered() {
  return json({code: 'already_delivered', error: '这条需求已交付，请选择另一条需求。'}, 409);
}

export const GET = req => handled(async () => {
  const user = await member();
  const {DB: db} = bindings();
  ensureDeliverySchema(db);
  const {data} = workspace(db);
  const delivered = new Set(db.prepare('SELECT DISTINCT request_id FROM desktop_deliveries').all().results
    .map(row => row.request_id));
  const isDelivered = request => request.files.length || delivered.has(request.id);
  const visible = data.requests.filter(request => canDeliver(user, request));
  const id = new URL(req.url).searchParams.get('requestId');
  if (id !== null) {
    const target = visible.find(request => String(request.id) === id);
    if (!target) throw new Failure(404, '未找到可交付的已发布需求');
    if (isDelivered(target)) return alreadyDelivered();
  }
  const requests = visible.filter(request => !isDelivered(request) && (id === null || String(request.id) === id)).map(request => ({
    id: request.id, description: request.description, channel: request.channel,
    product: data.catalog?.products?.find(product => product.id === request.product)?.name || request.product,
    designer: request.designer, fileCount: request.files.length,
  })).sort((a, b) => b.id - a.id);
  return json({version: 1, singleDelivery: true, user: {id: user.id, name: user.name, role: user.role}, requests});
});

export const POST = req => handled(async () => {
  mutation(req);
  const user = await member();
  let input;
  try {
    input = JSON.parse(new TextDecoder().decode(await body(req, 24_000)));
  } catch (error) {
    if (error instanceof Failure) throw error;
    throw new Failure(400, '提交格式无效');
  }
  const {batchId, requestId, uploadIds} = input || {};
  if (typeof batchId !== 'string' || !UUID.test(batchId) || !Number.isSafeInteger(requestId) || requestId < 1 ||
      !Array.isArray(uploadIds) || uploadIds.length < 1 || uploadIds.length > MAX_FILES ||
      uploadIds.some(id => typeof id !== 'string' || !UUID.test(id)) || new Set(uploadIds).size !== uploadIds.length) {
    throw new Failure(400, '请选择有效的需求及 1–200 个成品');
  }
  if (!['admin', 'designer'].includes(user.role)) throw new Failure(403, '当前账号没有成品提交权限');

  const {DB: db} = bindings();
  await ensureAuditSchema();
  ensureDeliverySchema(db);
  const ids = JSON.stringify(uploadIds);
  for (let attempt = 0; attempt < 5; attempt++) {
    const saved = db.prepare('SELECT * FROM desktop_deliveries WHERE actor_id=? AND batch_id=?')
      .bind(user.id, batchId).first();
    if (saved) {
      if (saved.request_id !== requestId || saved.upload_ids !== ids) {
        throw new Failure(409, '这批素材已经用于另一份提交');
      }
      return json(JSON.parse(saved.receipt));
    }

    const {version, data} = workspace(db);
    const request = data.requests.find(item => item.id === requestId);
    if (!request) throw new Failure(404, '需求不存在或已删除');
    if (!canDeliver(user, request)) throw new Failure(403, '只能提交分配给自己的需求');
    if (request.files.length || db.prepare('SELECT 1 FROM desktop_deliveries WHERE request_id=?').bind(requestId).first()) {
      return alreadyDelivered();
    }
    const prefix = materialPrefix(request, data.catalog);
    const date = dateFormat.format(new Date());
    const existing = data.requests.flatMap(item => item.files);
    const used = new Set([...JSON.stringify(data).matchAll(/\/api\/files\/([a-f0-9-]{36})/g)].map(match => match[1]));
    if (uploadIds.some(id => used.has(id))) throw new Failure(409, '所选文件已登记到工作台');
    const start = existing.reduce((max, file) =>
      file.uploadedAt === date && Number.isSafeInteger(file.sequence) ? Math.max(max, file.sequence) : max, 0);
    const files = uploadIds.map((id, index) => {
      const upload = db.prepare('SELECT owner,mime,size,name FROM uploads WHERE id=?').bind(id).first();
      const extension = EXTENSIONS[upload?.mime];
      if (!upload || upload.owner !== user.id) throw new Failure(403, '附件不存在或不属于当前账号');
      if (!extension) throw new Failure(400, 'Duo 只接受支持的图片或视频');
      const sequence = start + index + 1;
      const ext = extension === 'jpg' && /\.jpeg$/i.test(upload.name) ? 'jpeg' : extension;
      const name = `${prefix}_${date.replaceAll('-', '')}_${String(sequence).padStart(3, '0')}.${ext}`;
      return {name, originalName: upload.name, url: `/api/files/${id}`, type: upload.mime,
        size: upload.size, uploadedAt: date, sequence};
    });
    const next = {...data, requests: data.requests.map(item => item === request
      ? {...item, files} : item)};
    try {
      authorizeState(data, next, user);
    } catch (error) {
      throw new Failure(403, error.message);
    }
    const receipt = {requestId, batchId, files};
    const payload = JSON.stringify(next);
    if (new TextEncoder().encode(payload).length > 2_000_000) {
      throw new Failure(413, '工作台数据已达容量限制，请联系 Duo 管理员');
    }
    const [changed] = db.batch([
      db.prepare("UPDATE shared_state SET payload=?,version=version+1,updated_by=?,updated_at=? WHERE id='workspace' AND version=?")
        .bind(payload, user.id, new Date().toISOString(), version),
      db.prepare('INSERT INTO desktop_deliveries (actor_id,batch_id,request_id,upload_ids,receipt) SELECT ?,?,?,?,? WHERE changes()=1')
        .bind(user.id, batchId, requestId, ids, JSON.stringify(receipt)),
      auditInsert(user, [{action: '修改', module: '需求', target: String(requestId),
        summary: `桌面交付：REQ-${requestId} · ${files.length} 个成品`, after: receipt}], true),
    ]);
    if (changed.meta.changes === 1) return json(receipt, 201);
  }
  throw new Failure(409, 'Duo 正在被其他人更新，请重试这批成品');
});
