"""Run and record the assignment's live tool-choice evaluation.

Usage: python tool_choice_test.py --delay 10
The API key is read from .env and is never written to the output.
"""

import argparse
import json
import os
from pathlib import Path
import time

from dotenv import load_dotenv
from openai import OpenAI

from agent_core import StoreTools, build_database, run_agent


ROOT = Path(__file__).resolve().parent
CASES = [
    ("你們幾點營業？", {"get_store_info"}),
    ("高雄土庫店地址在哪裡？", {"get_store_info"}),
    ("可以用信用卡付款嗎？", {"get_store_info"}),
    ("合作學校學生有優惠嗎？", {"get_store_info"}),
    ("珍珠奶茶多少錢？", {"find_drink"}),
    ("茉莉綠茶有供應嗎？", {"find_drink"}),
    ("一杯珍珠奶茶和一杯茉莉綠茶總共多少錢？", {"calculate_drink_order"}),
    ("我是合作學校學生，會出示學生證；一杯珍珠奶茶和一杯茉莉綠茶折扣後多少錢？", {"calculate_drink_order"}),
    ("請問店長叫什麼名字？", {"transfer_to_human"}),
    ("高雄土庫店可以外送到我家嗎？", {"transfer_to_human"}),
    ("我要向店員反映飲料品質問題。", {"transfer_to_human"}),
    ("可以幫我寫數學作業嗎？", set()),
]


def grade_tool_choice(used, expected):
    """All required tools must appear; an unrelated question should use none."""
    return set(expected).issubset(used) if expected else not used


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--delay", type=float, default=10, help="Seconds between questions for API rate limits")
    parser.add_argument("--output", default="evidence/tool_choice_results.json")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    api_key = os.getenv("API_KEY")
    if not api_key:
        raise SystemExit("Set API_KEY in .env before running the live test.")
    client = OpenAI(api_key=api_key, base_url=os.getenv("LLM_API_URL", "https://www.iai.nkust.edu.tw/aihub/v1/"), timeout=60.0)
    model = os.getenv("MODEL_NAME", "Furen-large")
    connection = build_database(ROOT / "shop_app.db", ROOT / "my_shop_info.csv", ROOT / "my_menu.csv")
    tools = StoreTools(connection)
    results = []
    try:
        for number, (question, expected) in enumerate(CASES, 1):
            try:
                answer, trace = run_agent([{"role": "user", "content": question}], client, model, tools)
                used = [entry["tool"] for entry in trace]
                passed = grade_tool_choice(used, expected)
                result = {"question": question, "expected": sorted(expected), "used": used,
                          "answer": answer, "trace": trace, "passed": passed}
            except Exception as error:
                result = {"question": question, "expected": sorted(expected), "used": [],
                          "answer": "", "trace": [], "passed": False,
                          "error": f"{type(error).__name__}: {error}"}
            results.append(result)
            print(f"{'OK' if result['passed'] else 'MISS'} {number:02d}/{len(CASES)} expected={result['expected']} used={result['used']} question={question}", flush=True)
            if "error" in result:
                print(f"  error={result['error']}", flush=True)
            if number < len(CASES):
                time.sleep(args.delay)
    finally:
        connection.close()
        target = ROOT / args.output
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"model": model, "correct": sum(r["passed"] for r in results),
                                      "total": len(results), "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"tool choice = {sum(r['passed'] for r in results)}/{len(results)}", flush=True)


if __name__ == "__main__":
    main()
