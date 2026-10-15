import { applyD1Migrations, env } from "cloudflare:test";
import { beforeEach } from "vitest";

beforeEach(async () => {
  // Fresh tables for every test.
  for (const t of ["jobs", "device_codes", "sessions", "installs", "waitlist", "users", "d1_migrations"]) {
    await env.DB.prepare(`DROP TABLE IF EXISTS ${t}`).run();
  }
  await applyD1Migrations(env.DB, env.TEST_MIGRATIONS);
});
