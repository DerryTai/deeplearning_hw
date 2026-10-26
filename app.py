"""Gradio chat interface for the 50嵐 Kaohsiung Tuku customer-service agent.

Run: python app.py
"""

import json
import os
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from agent_core import StoreTools, build_database, run_agent


ROOT = Path(__file__).resolve().parent
GREETING = "您好！我是 50嵐高雄土庫店的線上客服。可以詢問營業時間、飲料價格或訂單金額；我會用店家資料查詢後回答。"
EMPTY_TRACE = "### 工具軌跡\n\n尚未呼叫工具。"


def start_chat():
    return [{"role": "assistant", "content": GREETING}]


def format_trace(trace):
    if not trace:
        return "### 工具軌跡\n\n本輪未呼叫工具。"
    lines = ["### 工具軌跡"]
    for entry in trace:
        arguments = json.dumps(entry["arguments"], ensure_ascii=False)
        result = json.dumps(entry["result"], ensure_ascii=False)
        lines.extend([
            f"**第 {entry['step']} 步：{entry['tool']}**",
            f"參數：`{arguments}`",
            f"結果：`{result}`",
        ])
    return "\n\n".join(lines)


def add_user_message(text, history):
    text = (text or "").strip()
    return "", (history or start_chat()) + ([{"role": "user", "content": text}] if text else [])


def answer_question(history, tools, client, model):
    if not history or history[-1]["role"] != "user":
        return history, EMPTY_TRACE
    try:
        answer, trace = run_agent(history, client, model, tools)
        return history + [{"role": "assistant", "content": answer}], format_trace(trace)
    except Exception as error:
        return history + [{"role": "assistant", "content": "目前無法連線至客服模型，請稍後再試。"}], f"### 工具軌跡\n\n系統錯誤：`{type(error).__name__}`"


def create_app():
    load_dotenv(ROOT / ".env")
    connection = build_database(ROOT / "shop_app.db", ROOT / "my_shop_info.csv", ROOT / "my_menu.csv")
    tools = StoreTools(connection)
    key = os.getenv("API_KEY")
    endpoint = os.getenv("LLM_API_URL", "https://www.iai.nkust.edu.tw/aihub/v1/")
    model = os.getenv("MODEL_NAME", "Furen-large")
    client = OpenAI(api_key=key or "missing-key", base_url=endpoint, timeout=60.0)

    with gr.Blocks(title="50嵐高雄土庫店線上客服") as demo:
        gr.Markdown("# 50嵐高雄土庫店線上客服\n從資料庫查詢店家資訊與飲料，訂單金額由程式計算。右側顯示每次使用的工具。")
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(value=start_chat(), height=470, label="客服對話")
                with gr.Row():
                    user_box = gr.Textbox(placeholder="請輸入飲料、門市或價格問題", show_label=False, scale=7)
                    send = gr.Button("送出", variant="primary")
                    clear = gr.Button("清除")
                gr.Markdown("### 範例問題")
                gr.Examples(
                    examples=[
                        "你們幾點營業？",
                        "珍珠奶茶多少錢？",
                        "一杯珍珠奶茶和一杯茉莉綠茶總共多少錢？",
                        "我是合作學校學生，會出示學生證；一杯珍珠奶茶和一杯茉莉綠茶折扣後多少錢？",
                        "請問有外送嗎？",
                        "我要找店員。",
                    ],
                    inputs=user_box,
                )
            with gr.Column(scale=2):
                trace_panel = gr.Markdown(EMPTY_TRACE)

        for trigger in (send.click, user_box.submit):
            trigger(add_user_message, [user_box, chat], [user_box, chat]).then(
                lambda history: answer_question(history, tools, client, model),
                inputs=chat, outputs=[chat, trace_panel],
            )
        clear.click(lambda: (start_chat(), "", EMPTY_TRACE), outputs=[chat, user_box, trace_panel])
    return demo


if __name__ == "__main__":
    create_app().launch()
