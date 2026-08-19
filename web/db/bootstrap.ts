import { getRawDb } from ".";

let ready: Promise<void> | null = null;

const statements = [
  "CREATE TABLE IF NOT EXISTS projects (id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, title TEXT NOT NULL, canvas_json TEXT NOT NULL, source_format TEXT NOT NULL DEFAULT 'creative-engine-web', version INTEGER NOT NULL DEFAULT 1, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_projects_owner_updated ON projects(owner_id, updated_at)",
  "CREATE TABLE IF NOT EXISTS project_members (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, user_id TEXT NOT NULL, email TEXT NOT NULL, role TEXT NOT NULL DEFAULT 'viewer', created_at INTEGER NOT NULL)",
  "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_members_project_user ON project_members(project_id, user_id)",
  "CREATE INDEX IF NOT EXISTS idx_project_members_user ON project_members(user_id)",
  "CREATE TABLE IF NOT EXISTS assets (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, owner_id TEXT NOT NULL, node_id TEXT, name TEXT NOT NULL, kind TEXT NOT NULL, object_key TEXT NOT NULL, content_type TEXT NOT NULL, size INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'ready', metadata_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_assets_project_created ON assets(project_id, created_at)",
  "CREATE INDEX IF NOT EXISTS idx_assets_node ON assets(node_id)",
  "CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, node_id TEXT NOT NULL, owner_id TEXT NOT NULL, kind TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0, provider TEXT, model TEXT, input_json TEXT NOT NULL DEFAULT '{}', output_json TEXT, error TEXT, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL)",
  "CREATE INDEX IF NOT EXISTS idx_tasks_project_updated ON tasks(project_id, updated_at)",
  "CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at)",
  "CREATE TABLE IF NOT EXISTS project_revisions (id TEXT PRIMARY KEY, project_id TEXT NOT NULL, actor_id TEXT NOT NULL, version INTEGER NOT NULL, canvas_json TEXT NOT NULL, created_at INTEGER NOT NULL)",
  "CREATE UNIQUE INDEX IF NOT EXISTS idx_project_revisions_project_version ON project_revisions(project_id, version)",
];

export function ensureSchema() {
  if (!ready) {
    ready = (async () => {
      const db = getRawDb();
      await db.batch(statements.map((statement) => db.prepare(statement)));
      await db.prepare("PRAGMA optimize").run();
    })();
  }
  return ready;
}
