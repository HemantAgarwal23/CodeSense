import type { Request, Response } from "express";
import { Router } from "express";

import { analyzeCode } from "./unified_llm_service";

const router = Router();
const lastCall = new Map<string, number>();

function canCall(ip: string): boolean {
  const now = Date.now();
  const prev = lastCall.get(ip) || 0;
  if (now - prev < 1000) return false; // 1 req/sec
  lastCall.set(ip, now);
  return true;
}

router.post("/review", async (req: Request, res: Response) => {
  const ip = String(req.ip || req.headers["x-forwarded-for"] || "unknown");
  if (!canCall(ip)) {
    res.status(429).json({
      score: 0,
      quality: "Basic",
      summary: "Too many requests. Please slow down.",
      bugs: [],
      runtime_risks: [],
      code_issues: [],
      suggestions: [],
      fixes: [],
    });
    return;
  }

  const code = String(req.body?.code ?? "");
  if (!code || code.length > 20000) {
    return res.status(400).json({ error: "Invalid input" });
  }
  const result = await analyzeCode(code);
  res.json(result);
});

export default router;
