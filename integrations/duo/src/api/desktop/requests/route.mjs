import {auditInsert, ensureAuditSchema, stateEvents} from '../../../lib/audit.mjs';
import {authorizeState, blankState} from '../../../lib/policy.mjs';
import {bindings, body, Failure, handled, json, member, mutation} from '../../../lib/server.mjs';

const UUID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/;
const MEDIA = new Set(['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'video/mp4', 'video/webm']);
const dateFormat = new Intl.DateTimeFormat('sv-SE', {
  timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
});

function workspace(db) {
  return db.prepare("SELECT version,payload FROM shared_state WHERE id='workspace'").first();
}

function formOptions(db, data, user) {
  const catalog = data.catalog || {};
  const enabled = table => Object.keys(table || {}).filter(name => !(catalog.disabledPeople || []).includes(name));
  let requesters = enabled(catalog.optimizers);
  let designers = enabled(catalog.designers);
  if (user.role === 'designer') {
    const active = db.prepare("SELECT name FROM members WHERE active=1 AND role='optimizer'").all().results;
    requesters = requesters.filter(name => active.some(row => row.name === name));
    designers = designers.filter(name => name === user.name);
  } else if (user.role === 'optimizer') {
    requesters = requesters.filter(name => name === user.name);
  } else if (user.role !== 'admin') {
    throw new Failure(403, '当前账号没有发布需求的权限');
  }
  const products = new Map();
  for (const row of catalog.products || []) {
    if (row.disabled || row.archived) continue;
    const variants = products.get(row.name) || [];
    variants.push(row);
    products.set(row.name, variants);
  }
  return {
    products: [...products].map(([name, rows]) => {
      const shared = rows.find(row => !row.platform || row.platform === 'BOTH');
      const platforms = {};
      for (const key of ['AND', 'IOS']) {
        const row = rows.find(item => item.platform === key) || shared;
        if (row) platforms[key] = row.id;
      }
      if (platforms.AND && platforms.IOS) platforms.BOTH = shared?.id || rows[0].id;
      return {name, platforms};
    }).filter(product => Object.keys(product.platforms).length),
    channels: Object.keys(catalog.channels || {}), requesters, designers,
    priorities: ['普通', '紧急'],
    tags: (catalog.tags || []).filter(tag => !tag.disabled).map(({id, name}) => ({id, name})),
    today: dateFormat.format(new Date()),
  };
}

export const GET = () => handled(async () => {
  const user = await member();
  const {DB: db} = bindings();
  const row = workspace(db);
  return json(formOptions(db, row ? JSON.parse(row.payload) : blankState(), user));
});

export const POST = req => handled(async () => {
  mutation(req);
  const user = await member();
  let input;
  try {
    input = JSON.parse(new TextDecoder().decode(await body(req, 32_000)));
  } catch (error) {
    if (error instanceof Failure) throw error;
    throw new Failure(400, '发布格式无效');
  }
  const {publicationId, product, platform, channel, priority, requester, designer, scheduleEnd, tag, uploadIds} = input || {};
  const description = typeof input?.description === 'string' ? input.description.trim() : '';
  if (typeof publicationId !== 'string' || !UUID.test(publicationId) || !description || description.length > 2000 ||
      /[<>]/.test(description) || ![product, platform, channel, priority, requester, designer, scheduleEnd, tag]
        .every(value => typeof value === 'string') || !Array.isArray(uploadIds) ||
      uploadIds.some(id => typeof id !== 'string' || !UUID.test(id)) || new Set(uploadIds).size !== uploadIds.length) {
    throw new Failure(400, '请填写有效的需求内容和参考附件');
  }
  const fingerprint = JSON.stringify([description, product, platform, channel, priority,
    requester, designer, scheduleEnd, tag, uploadIds]);
  const {DB: db} = bindings();
  await ensureAuditSchema();
  db.prepare(`CREATE TABLE IF NOT EXISTS desktop_publications (
    actor_id TEXT NOT NULL, publication_id TEXT NOT NULL, input TEXT NOT NULL,
    request_id INTEGER NOT NULL, PRIMARY KEY(actor_id,publication_id)
  )`).run();
  for (let attempt = 0; attempt < 5; attempt++) {
    const saved = db.prepare('SELECT input,request_id FROM desktop_publications WHERE actor_id=? AND publication_id=?')
      .bind(user.id, publicationId).first();
    if (saved) {
      if (saved.input !== fingerprint) throw new Failure(409, '该发布已处理，请刷新需求列表');
      return json({requestId: saved.request_id});
    }

    const row = workspace(db);
    const data = row ? JSON.parse(row.payload) : blankState();
    const options = formOptions(db, data, user);
    if (!options.requesters.includes(requester) || !options.designers.includes(designer)) {
      throw new Failure(403, '请选择当前账号有权限使用的提需人和设计师');
    }
    const productId = options.products.find(item => item.name === product)?.platforms[platform];
    if (!['AND', 'IOS', 'BOTH'].includes(platform) || !productId || !options.channels.includes(channel) ||
        !options.priorities.includes(priority) || (tag && !options.tags.some(item => item.id === tag))) {
      throw new Failure(400, '产品、平台、渠道、优先级或标签已失效，请重新选择');
    }
    const end = new Date(scheduleEnd + 'T00:00:00Z');
    if (!/^\d{4}-\d{2}-\d{2}$/.test(scheduleEnd) || Number.isNaN(+end) ||
        end.toISOString().slice(0, 10) !== scheduleEnd || scheduleEnd < options.today) {
      throw new Failure(400, '请选择有效的完成时间，不能早于今天');
    }
    const attachments = uploadIds.map(id => {
      const file = db.prepare('SELECT owner,mime,size,name FROM uploads WHERE id=?').bind(id).first();
      if (!file || file.owner !== user.id) throw new Failure(403, '参考附件不存在或不属于当前账号');
      if (!MEDIA.has(file.mime)) throw new Failure(400, '参考附件只接受支持的图片或视频');
      return {name: file.name, type: file.mime, size: file.size, url: `/api/files/${id}`};
    });
    const now = new Date();
    let id = now.getTime();
    const ids = new Set(data.requests.map(request => String(request.id)));
    while (ids.has(String(id))) id++;
    const request = {id, description, product: productId, platform, channel, priority, requester, designer, tag,
      scheduleStart: options.today, scheduleEnd, ownerId: user.id, attachments, date: options.today, files: []};
    const next = {...data, requests: [request, ...data.requests]};
    try {
      authorizeState(data, next, user);
    } catch (error) {
      throw new Failure(403, error.message);
    }
    const payload = JSON.stringify(next);
    if (new TextEncoder().encode(payload).length > 2_000_000) {
      throw new Failure(413, '工作台数据已达容量限制，请联系平台管理员');
    }
    const update = row
      ? db.prepare("UPDATE shared_state SET payload=?,version=version+1,updated_by=?,updated_at=? WHERE id='workspace' AND version=?")
        .bind(payload, user.id, now.toISOString(), row.version)
      : db.prepare("INSERT OR IGNORE INTO shared_state (id,version,payload,updated_by,updated_at) VALUES ('workspace',1,?,?,?)")
        .bind(payload, user.id, now.toISOString());
    const [changed] = db.batch([
      update,
      db.prepare('INSERT INTO desktop_publications (actor_id,publication_id,input,request_id) SELECT ?,?,?,? WHERE changes()=1')
        .bind(user.id, publicationId, fingerprint, id),
      auditInsert(user, stateEvents(data, next), true),
    ]);
    if (changed.meta.changes === 1) return json({requestId: id}, 201);
  }
  throw new Failure(409, '工作台正在被其他人更新，请稍后重试');
});
