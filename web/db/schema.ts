import { index, integer, sqliteTable, text, uniqueIndex } from "drizzle-orm/sqlite-core";

export const projects = sqliteTable("projects", {
  id: text("id").primaryKey(),
  ownerId: text("owner_id").notNull(),
  title: text("title").notNull(),
  canvasJson: text("canvas_json").notNull(),
  sourceFormat: text("source_format").notNull().default("creative-engine-web"),
  version: integer("version").notNull().default(1),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  index("idx_projects_owner_updated").on(table.ownerId, table.updatedAt),
]);

export const projectMembers = sqliteTable("project_members", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  userId: text("user_id").notNull(),
  email: text("email").notNull(),
  role: text("role").notNull().default("viewer"),
  createdAt: integer("created_at").notNull(),
}, (table) => [
  uniqueIndex("idx_project_members_project_user").on(table.projectId, table.userId),
  index("idx_project_members_user").on(table.userId),
]);

export const assets = sqliteTable("assets", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  ownerId: text("owner_id").notNull(),
  nodeId: text("node_id"),
  name: text("name").notNull(),
  kind: text("kind").notNull(),
  objectKey: text("object_key").notNull(),
  contentType: text("content_type").notNull(),
  size: integer("size").notNull(),
  status: text("status").notNull().default("ready"),
  metadataJson: text("metadata_json").notNull().default("{}"),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  index("idx_assets_project_created").on(table.projectId, table.createdAt),
  index("idx_assets_node").on(table.nodeId),
]);

export const tasks = sqliteTable("tasks", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  nodeId: text("node_id").notNull(),
  ownerId: text("owner_id").notNull(),
  kind: text("kind").notNull(),
  status: text("status").notNull().default("queued"),
  progress: integer("progress").notNull().default(0),
  provider: text("provider"),
  model: text("model"),
  inputJson: text("input_json").notNull().default("{}"),
  outputJson: text("output_json"),
  error: text("error"),
  createdAt: integer("created_at").notNull(),
  updatedAt: integer("updated_at").notNull(),
}, (table) => [
  index("idx_tasks_project_updated").on(table.projectId, table.updatedAt),
  index("idx_tasks_status_updated").on(table.status, table.updatedAt),
]);

export const projectRevisions = sqliteTable("project_revisions", {
  id: text("id").primaryKey(),
  projectId: text("project_id").notNull(),
  actorId: text("actor_id").notNull(),
  version: integer("version").notNull(),
  canvasJson: text("canvas_json").notNull(),
  createdAt: integer("created_at").notNull(),
}, (table) => [
  uniqueIndex("idx_project_revisions_project_version").on(table.projectId, table.version),
]);
