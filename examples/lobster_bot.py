#!/usr/bin/env python3
"""
Nexus — 官方龙虾 Bot (Lobster Bot)
====================================
接单方旗舰 Worker：使用 OpenRouter 调用 LLM 做高质量 JSON 提取。

支持模型（通过 OpenRouter 聚合）：
  - google/gemini-2.0-flash-001     ← 默认，快速经济
  - google/gemini-2.5-pro-preview-03-25
  - deepseek/deepseek-chat           ← 中文场景推荐
  - openai/gpt-4o-mini
  - anthropic/claude-3-5-sonnet

中转说明：
  - 本地开发（Mac）: OPENROUTER_BASE_URL=https://openrouter.ai/api/v1
  - 线上生产服务器:  OPENROUTER_BASE_URL=http://154.201.90.199:4000/api/v1

用法：
    # 方式一：直接设置环境变量
    export OPENROUTER_API_KEY=sk-or-v1-...
    export NEXUS_API_KEY=你的接单方key
    python examples/lobster_bot.py

    # 方式二：加载 .env 文件
    python examples/lobster_bot.py --env-file .env.lobster

    # 方式三：使用生产中转
    OPENROUTER_BASE_URL=http://154.201.90.199:4000/api/v1 \\
    OPENROUTER_API_KEY=sk-or-v1-... \\
    NEXUS_API_KEY=xxx \\
    python examples/lobster_bot.py
"""

import json
import logging
import os
import sys
import time
import argparse

# ── 依赖检查 ──────────────────────────────────────────────────
try:
    import httpx
except ImportError:
    print("ERROR: pip install httpx")
    sys.exit(1)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("lobster")


# ── 配置（优先读环境变量，其次 .env 文件）────────────────────────

def load_env_file(path: str):
    """简单解析 .env 文件，跳过注释和空行。"""
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" in line:
                k, _, v = line.partition("=")
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                os.environ.setdefault(k, v)  # 不覆盖已有的环境变量


def get_config():
    return {
        "nexus_base_url": os.getenv("NEXUS_BASE_URL", "http://localhost:8000"),
        "nexus_api_key": os.getenv("NEXUS_API_KEY", ""),
        "openrouter_api_key": os.getenv("OPENROUTER_API_KEY", ""),
        # 本地直连 or 中转（154.201.90.199:4000）
        "openrouter_base_url": os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        "model": os.getenv("OPENROUTER_MODEL", "google/gemini-2.0-flash-001"),
        "bid_multiplier": float(os.getenv("BID_MULTIPLIER", "1.3")),
        "poll_interval": float(os.getenv("POLL_INTERVAL", "1.0")),
    }


# ── Token 成本估算（credits）────────────────────────────────────
# OpenRouter 各模型价格（每1M tokens，美元）
MODEL_PRICING = {
    "google/gemini-2.0-flash-001":             (0.10, 0.40),
    "google/gemini-2.5-pro-preview-03-25":     (1.25, 10.00),
    "deepseek/deepseek-chat":                  (0.14, 0.28),
    "openai/gpt-4o-mini":                      (0.15, 0.60),
    "openai/gpt-4o":                           (2.50, 10.00),
    "anthropic/claude-3-5-sonnet":             (3.00, 15.00),
    "anthropic/claude-3-7-sonnet-20250219":    (3.00, 15.00),
    "moonshot/moonshot-v1-8k":                 (1.00, 3.00),
}
CHARS_PER_TOKEN = 4
CREDITS_PER_DOLLAR = 100  # 1 credit = $0.01


def estimate_cost_credits(input_text: str, schema: dict, model: str) -> int:
    """估算一次 LLM 调用的成本（credits）。"""
    input_price, output_price = MODEL_PRICING.get(model, (1.0, 3.0))

    prompt_overhead_chars = 800  # 系统提示 + 模板
    input_chars = len(input_text) + len(json.dumps(schema)) + prompt_overhead_chars
    input_tokens = input_chars / CHARS_PER_TOKEN
    output_tokens = max(80, len(schema.get("properties", {})) * 25)

    cost_usd = (
        input_tokens / 1_000_000 * input_price
        + output_tokens / 1_000_000 * output_price
    )
    return max(1, int(cost_usd * CREDITS_PER_DOLLAR + 0.999))


# ── LLM 提取核心 ───────────────────────────────────────────────

def extract_with_llm(input_text: str, schema: dict, cfg: dict) -> dict:
    """
    调用 OpenRouter 做 JSON 提取。
    完全兼容 OpenAI chat completions 格式。
    """
    system_prompt = (
        "You are a precise JSON extraction assistant. "
        "Your task: extract structured data from the given text strictly following the provided JSON Schema. "
        "Rules:\n"
        "1. Return ONLY valid JSON — no markdown fences, no explanation, no extra keys.\n"
        "2. If a field cannot be found in the text, omit it (do not guess).\n"
        "3. Match the exact types specified in the schema (string/integer/number/array).\n"
        "4. For dates, normalize to ISO 8601 format (YYYY-MM-DD) when possible.\n"
        "5. For numbers, strip currency symbols and units."
    )

    user_prompt = (
        f"Text to extract from:\n---\n{input_text}\n---\n\n"
        f"JSON Schema:\n{json.dumps(schema, indent=2, ensure_ascii=False)}\n\n"
        "Extract and return valid JSON:"
    )

    payload = {
        "model": cfg["model"],
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.0,
        "max_tokens": 1024,
        "response_format": {"type": "json_object"},
    }

    resp = httpx.post(
        f"{cfg['openrouter_base_url']}/chat/completions",
        headers={
            "Authorization": f"Bearer {cfg['openrouter_api_key']}",
            "Content-Type": "application/json",
            # OpenRouter 推荐携带这两个头（便于统计，不影响功能）
            "HTTP-Referer": "https://nexus.ai",
            "X-Title": "Nexus-Lobster-Bot",
        },
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    return json.loads(content)


# ── Nexus 平台交互 ──────────────────────────────────────────────

class NexusClient:
    def __init__(self, api_key: str, base_url: str):
        self._http = httpx.Client(
            base_url=base_url,
            headers={"X-API-Key": api_key, "Content-Type": "application/json"},
            timeout=15,
        )

    def get_available_tasks(self) -> list:
        resp = self._http.get("/api/v1/tasks/available")
        resp.raise_for_status()
        return resp.json()

    def bid(self, task_id: str, credits: int) -> bool:
        resp = self._http.post(f"/api/v1/tasks/{task_id}/bid", json={"bid_credits": credits})
        if resp.status_code == 200:
            return True
        if resp.status_code == 409:
            return False  # 已出价
        logger.warning(f"Bid failed {task_id[:8]}: {resp.status_code} {resp.text[:80]}")
        return False

    def get_task(self, task_id: str) -> dict:
        resp = self._http.get(f"/api/v1/tasks/{task_id}")
        resp.raise_for_status()
        return resp.json()

    def wait_for_award(self, task_id: str, timeout: float = 10.0) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                st = self.get_task(task_id)
                if st["status"] == "AWARDED":
                    return True
                if st["status"] in ("SETTLED", "EXPIRED", "CANCELLED", "PENDING_POOL"):
                    return False
            except Exception:
                pass
            time.sleep(0.5)
        return False

    def submit(self, task_id: str, result: dict) -> dict:
        resp = self._http.post(
            f"/api/v1/tasks/{task_id}/submit",
            json={"result_data": result},
        )
        resp.raise_for_status()
        return resp.json()

    def balance(self) -> dict:
        return self._http.get("/api/v1/credits/balance").json()

    def reputation(self) -> dict:
        return self._http.get("/api/v1/account/reputation").json()

    def close(self):
        self._http.close()


# ── 主循环 ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Nexus Lobster Bot")
    parser.add_argument("--env-file", default=".env", help="指定 .env 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只轮询任务，不出价（调试用）")
    args = parser.parse_args()

    # 加载 .env（环境变量优先级更高）
    load_env_file(args.env_file)
    cfg = get_config()

    # 校验必填项
    if not cfg["nexus_api_key"]:
        print("❌ 缺少 NEXUS_API_KEY\n   export NEXUS_API_KEY=你的接单方key")
        sys.exit(1)
    if not cfg["openrouter_api_key"]:
        print("❌ 缺少 OPENROUTER_API_KEY\n   export OPENROUTER_API_KEY=sk-or-v1-...")
        sys.exit(1)

    client = NexusClient(cfg["nexus_api_key"], cfg["nexus_base_url"])

    logger.info("=" * 55)
    logger.info("  🦞 Nexus Lobster Bot 启动")
    logger.info(f"  模型:   {cfg['model']}")
    logger.info(f"  中转:   {cfg['openrouter_base_url']}")
    logger.info(f"  出价:   估算成本 × {cfg['bid_multiplier']}")
    logger.info(f"  轮询:   每 {cfg['poll_interval']}s")
    if args.dry_run:
        logger.info("  模式:   🔍 DRY-RUN（只观察，不出价）")
    logger.info("=" * 55)

    settled_count = 0
    failed_count = 0
    total_earned = 0

    try:
        while True:
            try:
                tasks = client.get_available_tasks()
            except Exception as e:
                logger.error(f"轮询失败: {e}")
                time.sleep(cfg["poll_interval"])
                continue

            for task_data in tasks:
                if task_data["task_type"] != "json_extraction":
                    logger.debug(f"跳过非 json_extraction 任务: {task_data['task_type']}")
                    continue

                task_id = task_data["id"]
                budget = task_data["max_budget_credits"]
                schema = task_data.get("validation_schema", {})
                preview = task_data.get("input_data_preview", "")

                # 估算成本 + 计算出价
                est_cost = estimate_cost_credits(preview, schema, cfg["model"])
                bid_amount = min(budget, max(1, int(est_cost * cfg["bid_multiplier"])))

                logger.info(
                    f"📋 任务 {task_id[:8]}... budget={budget}cr "
                    f"est_cost={est_cost}cr bid={bid_amount}cr"
                )

                if args.dry_run:
                    logger.info(f"   [DRY-RUN] 跳过出价")
                    continue

                # 出价
                if not client.bid(task_id, bid_amount):
                    continue

                # 等待撮合
                if not client.wait_for_award(task_id):
                    logger.info(f"   未中标: {task_id[:8]}...")
                    continue

                logger.info(f"   🏆 中标！开始用 {cfg['model']} 提取...")

                # 获取完整输入数据
                try:
                    detail = client.get_task(task_id)
                    full_input = detail.get("input_data") or preview
                    schema = detail.get("validation_schema", schema)
                except Exception:
                    full_input = preview

                # LLM 提取 + 提交（最多2次重试）
                success = False
                for attempt in range(3):
                    try:
                        result = extract_with_llm(full_input, schema, cfg)
                        logger.info(f"   提取结果: {json.dumps(result, ensure_ascii=False)}")

                        sub = client.submit(task_id, result)
                        if sub["error_code"] is None:
                            logger.info(f"   ✅ SETTLED! 获得 {bid_amount}cr")
                            settled_count += 1
                            total_earned += bid_amount
                            success = True
                            break
                        else:
                            logger.warning(
                                f"   ❌ {sub['error_code']} "
                                f"(retries_left={sub['retries_left']}, attempt={attempt+1})"
                            )
                            if sub["retries_left"] <= 0:
                                break
                            time.sleep(0.5)
                    except Exception as e:
                        logger.error(f"   处理失败 (attempt={attempt+1}): {e}")
                        break

                if not success:
                    failed_count += 1

            time.sleep(cfg["poll_interval"])

    except KeyboardInterrupt:
        logger.info("\n⛔ Bot 停止")

    # 最终统计
    logger.info("=" * 55)
    logger.info(f"  结算成功: {settled_count} 笔")
    logger.info(f"  结算失败: {failed_count} 笔")
    logger.info(f"  总收入:   {total_earned} credits")
    try:
        bal = client.balance()
        rep = client.reputation()
        logger.info(f"  余额:     {bal['credits_balance']} credits")
        logger.info(f"  信誉值:   {rep['reputation']} (共 {rep['task_count']} 笔)")
    except Exception:
        pass
    logger.info("=" * 55)
    client.close()


if __name__ == "__main__":
    main()
