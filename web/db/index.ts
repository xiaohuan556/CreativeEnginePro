import { drizzle } from "drizzle-orm/d1";
import * as schema from "./schema";

async function runtimeEnv() {
  const { env } = await import("cloudflare:workers");
  return env;
}

export async function getDb() {
  const env = await runtimeEnv();
  if (!env.DB) {
    throw new Error(
      "Cloudflare D1 binding `DB` is unavailable. Set the `d1` field in .openai/hosting.json to `DB` or let your control plane inject the real binding values before using the database."
    );
  }

  return drizzle(env.DB, { schema });
}

export async function getRawDb() {
  const env = await runtimeEnv();
  if (!env.DB) throw new Error("Cloudflare D1 binding `DB` is unavailable.");
  return env.DB;
}
