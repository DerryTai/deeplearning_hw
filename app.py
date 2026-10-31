"""Gradio chat interface for the 50嵐 Kaohsiung Tuku customer-service agent.

Run: python app.py
"""

import json
import argparse
import os
from pathlib import Path

import gradio as gr
from dotenv import load_dotenv
from openai import OpenAI

from agent_core import GREETING, StoreTools, build_database, run_agent
from rag_core import BrandRAG


ROOT = Path(__file__).resolve().parent
EMPTY_TRACE = "### 工具軌跡\n\n尚未呼叫工具。"


def start_chat():
    return [{"role": "assistant", "content": GREETING}]


def format_trace(trace):
    if not trace:
        return "### 工具軌跡\n\n本輪未呼叫工具。"
    lines = ["### 工具軌跡"]
    for entry in trace:
        arguments = json.dumps(entry["arguments"], ensure_ascii=False)
        summary = {key: value for key, value in entry['result'].items() if key != 'chunks'}
        result = json.dumps(summary, ensure_ascii=False)
        lines.extend([
            f"**第 {entry['step']} 步：{entry['tool']}**",
            f"參數：`{arguments}`",
            f"結果：`{result}`",
        ])
        for chunk in entry['result'].get('chunks', []):
            lines.append(f"**{chunk['title']}** · 相似度 {chunk['score']:.3f} · {chunk['source']}\n\n{chunk['text']}")
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


def replay_evidence(case):
    """Show a recorded real model conversation for report capture; never synthesize replies."""
    records = json.loads((ROOT / 'evidence' / 'midterm_results.json').read_text(encoding='utf-8'))['agent']
    record = next(row for row in records if row['name'] == case)
    history = start_chat() + [{'role': 'user', 'content': record['question']}, {'role': 'assistant', 'content': record['answer']}]
    return history, format_trace(record['trace'])


def create_app(replay=False):
    load_dotenv(ROOT / ".env")
    connection = build_database(ROOT / "shop_app.db", ROOT / "my_shop_info.csv", ROOT / "my_menu.csv")
    key = os.getenv("API_KEY")
    endpoint = os.getenv("LLM_API_URL", "https://www.iai.nkust.edu.tw/aihub/v1/")
    model = os.getenv("MODEL_NAME", "Furen-large")
    client = OpenAI(api_key=key or "missing-key", base_url=endpoint, timeout=60.0, max_retries=1)
    embed_client = OpenAI(api_key=os.getenv('EMBEDDING_API_KEY') or key or 'missing-key',
                          base_url=os.getenv('EMBEDDING_API_URL') or endpoint, timeout=60.0, max_retries=1)
    rag = BrandRAG(ROOT / '關於50嵐.txt', embed_client, os.getenv('EMBEDDING_MODEL_NAME') or 'Embedding',
                   cache_path=ROOT / '.cache' / 'rag-index.json')
    tools = StoreTools(connection, rag=rag)

    with gr.Blocks(title="50嵐高雄土庫店線上客服") as demo:
        gr.Markdown("# 50嵐客服 Agent + RAG\n課堂模擬專案｜價格與金額查資料庫，品牌與活動資訊檢索文件。右側顯示工具、來源片段與相似度。")
        if replay:
            gr.Markdown('報告取證模式：重播已保存的 NKUST 真實測試對話。')
        with gr.Row():
            with gr.Column(scale=3):
                chat = gr.Chatbot(value=start_chat(), height=580, label="客服對話")
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
                        "2026冬季買奶茶送禮有什麼條件？外送也可以嗎？",
                        "燕麥奶什麼時候開賣？可以替換哪些飲品？",
                        "50嵐從哪裡開始？高雄區客服怎麼聯絡？",
                        "買奶茶送禮活動外送能參加嗎？另外珍珠奶茶一杯多少錢？",
                        "請問店長的名字叫什麼？",
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
        if replay:
            def load_replay(request: gr.Request):
                return replay_evidence(request.query_params.get('case', 'database'))
            demo.load(load_replay, outputs=[chat, trace_panel])
    return demo


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--replay-evidence', action='store_true')
    parser.add_argument('--port', type=int, default=7860)
    args = parser.parse_args()
    create_app(replay=args.replay_evidence).launch(server_name='127.0.0.1', server_port=args.port)
