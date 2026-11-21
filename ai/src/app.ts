import "reflect-metadata";
import {
  Body,
  CanActivate,
  Controller,
  ExecutionContext,
  Get,
  Inject,
  Injectable,
  Module,
  Post,
  Query,
  UseGuards,
  type DynamicModule,
} from "@nestjs/common";
import { timingSafeEqual } from "node:crypto";
import { AiService, aiError } from "./ai.service.js";
import { CONFIG, LEDGER, LLM } from "./tokens.js";
import type { Config } from "./config.js";
import type { Ledger } from "./ledger.js";
import type { Llm } from "./llm.js";

/** Only the Fasla app in the same task may call this service. */
@Injectable()
class InternalKeyGuard implements CanActivate {
  constructor(@Inject(CONFIG) private cfg: Config) {}
  canActivate(ctx: ExecutionContext): boolean {
    const got = String(ctx.switchToHttp().getRequest().headers["x-internal-key"] ?? "");
    const want = this.cfg.internalKey;
    if (!want) return true; // local development only; production always sets a key
    const a = Buffer.from(got);
    const b = Buffer.from(want);
    if (a.length !== b.length || !timingSafeEqual(a, b)) throw aiError(401, "unauthorized", "Not allowed.");
    return true;
  }
}

type MapBody = { userId: string; headers: string[]; original?: string[]; sample?: string[][]; fields: string[] };
type StatusBody = { userId: string; values: string[] };
type ExplainBody = { userId: string; planKey: string; context: unknown };
type AskBody = ExplainBody & { question: string; history?: { role: string; text: string }[] };

@Controller()
class HealthController {
  @Get("healthz")
  health() {
    return { ok: true };
  }
}

@Controller("v1")
@UseGuards(InternalKeyGuard)
class AiController {
  constructor(@Inject(AiService) private ai: AiService) {}

  @Get("usage")
  usage(@Query("userId") userId: string) {
    if (!userId) throw aiError(400, "bad_request", "Missing user.");
    return this.ai.usage(userId);
  }

  @Post("map-columns")
  mapColumns(@Body() b: MapBody) {
    return this.ai.mapColumns(b.userId, b.headers, b.original ?? [], b.sample ?? [], b.fields);
  }

  @Post("map-statuses")
  mapStatuses(@Body() b: StatusBody) {
    return this.ai.mapStatuses(b.userId, b.values);
  }

  @Post("explain")
  explain(@Body() b: ExplainBody) {
    if (!b.planKey) throw aiError(400, "bad_request", "Missing plan.");
    return this.ai.explain(b.userId, String(b.planKey).slice(0, 200), b.context);
  }

  @Post("ask")
  ask(@Body() b: AskBody) {
    if (!b.planKey) throw aiError(400, "bad_request", "Missing plan.");
    return this.ai.ask(b.userId, String(b.planKey).slice(0, 200), b.context, b.question, b.history ?? []);
  }
}

@Module({})
export class AppModule {
  static create(cfg: Config, ledger: Ledger, llm: Llm): DynamicModule {
    return {
      module: AppModule,
      controllers: [HealthController, AiController],
      providers: [
        { provide: CONFIG, useValue: cfg },
        { provide: LEDGER, useValue: ledger },
        { provide: LLM, useValue: llm },
        AiService,
        InternalKeyGuard,
      ],
    };
  }
}
