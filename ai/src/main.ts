import "reflect-metadata";
import { NestFactory } from "@nestjs/core";
import type { NestExpressApplication } from "@nestjs/platform-express";
import { AppModule } from "./app.js";
import { config, type Config } from "./config.js";
import { makeLedger, type Ledger } from "./ledger.js";
import { BedrockLlm, FakeLlm, type Llm } from "./llm.js";

export async function buildApp(cfg: Config = config(), ledger?: Ledger, llm?: Llm): Promise<NestExpressApplication> {
  const app = await NestFactory.create<NestExpressApplication>(
    AppModule.create(cfg, ledger ?? makeLedger(cfg.databaseUrl), llm ?? (cfg.provider === "fake" ? new FakeLlm() : new BedrockLlm(cfg))),
    { logger: ["error", "warn"], bodyParser: false },
  );
  app.useBodyParser("json", { limit: "200kb" });
  app.enableShutdownHooks();
  return app;
}

const isMain = import.meta.url === `file://${process.argv[1]}`;
if (isMain) {
  const cfg = config();
  if (process.env.NODE_ENV === "production" && !cfg.internalKey) {
    console.error("AI_INTERNAL_KEY must be set in production");
    process.exit(1);
  }
  const app = await buildApp(cfg);
  await app.listen(cfg.port, cfg.host);
  console.log(JSON.stringify({ level: "info", msg: "fasla-ai listening", host: cfg.host, port: cfg.port, provider: cfg.provider, ledger: cfg.databaseUrl.startsWith("postgres") ? "postgres" : "memory" }));
}
