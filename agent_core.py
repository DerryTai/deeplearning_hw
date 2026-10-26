"""Database-backed tools and a bounded customer-service agent for 50嵐土庫店."""

import csv
from decimal import Decimal, ROUND_HALF_UP
import json
from pathlib import Path
import re
import sqlite3
from threading import RLock


def build_database(db_path, info_csv, menu_csv):
    """Rebuild both SQLite tables from the submitted CSV files at startup."""
    connection = sqlite3.connect(str(db_path), check_same_thread=False)
    connection.row_factory = sqlite3.Row
    with connection:
        connection.execute("DROP TABLE IF EXISTS shop_info")
        connection.execute("DROP TABLE IF EXISTS drinks")
        connection.execute("CREATE TABLE shop_info (topic TEXT PRIMARY KEY, content TEXT NOT NULL)")
        connection.execute(
            "CREATE TABLE drinks (item_id INTEGER PRIMARY KEY, category TEXT NOT NULL, "
            "name TEXT NOT NULL UNIQUE, price INTEGER NOT NULL CHECK(price >= 0), "
            "available INTEGER NOT NULL CHECK(available IN (0, 1)))"
        )
        with Path(info_csv).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        connection.executemany(
            "INSERT INTO shop_info (topic, content) VALUES (?, ?)",
            [(row["topic"], row["content"]) for row in rows],
        )
        with Path(menu_csv).open(encoding="utf-8-sig", newline="") as stream:
            rows = list(csv.DictReader(stream))
        connection.executemany(
            "INSERT INTO drinks (item_id, category, name, price, available) VALUES (?, ?, ?, ?, ?)",
            [
                (int(row["item_id"]), row["category"], row["name"], int(row["price"]), int(row["available"]))
                for row in rows
            ],
        )
    return connection


class StoreTools:
    def __init__(self, connection):
        self.connection = connection
        self.lock = RLock()

    def get_store_info(self, topic):
        if not isinstance(topic, str) or not topic.strip():
            return {"found": False, "reason": "invalid_topic"}
        if topic.strip().lower() == "delivery":
            return {"found": False, "reason": "unconfirmed", "topic": "delivery"}
        with self.lock:
            row = self.connection.execute(
                "SELECT topic, content FROM shop_info WHERE lower(topic) = lower(?)", (topic.strip(),)
            ).fetchone()
        if row is None:
            return {"found": False, "reason": "not_in_data", "topic": topic}
        return {"found": True, "topic": row["topic"], "content": row["content"]}

    def find_drink(self, name):
        if not isinstance(name, str) or not name.strip():
            return {"found": False, "reason": "invalid_name"}
        needle = name.strip()
        with self.lock:
            rows = self.connection.execute(
                "SELECT item_id, category, name, price, available FROM drinks ORDER BY item_id"
            ).fetchall()
        exact = [row for row in rows if row["name"] == needle]
        matches = exact or [row for row in rows if needle in row["name"]]
        if not matches:
            return {"found": False, "reason": "not_in_data", "name": needle}
        return {
            "found": True,
            "ambiguous": len(matches) > 1,
            "items": [
                {"item_id": row["item_id"], "category": row["category"], "name": row["name"],
                 "price": row["price"], "available": bool(row["available"])}
                for row in matches
            ],
        }

    def calculate_drink_order(self, items, student_discount=False):
        if not isinstance(items, list) or not items:
            return {"ok": False, "reason": "invalid_items"}
        if type(student_discount) is not bool:
            return {"ok": False, "reason": "invalid_discount_flag"}
        lines = []
        subtotal = 0
        for entry in items:
            if not isinstance(entry, dict):
                return {"ok": False, "reason": "invalid_item"}
            name, quantity = entry.get("name"), entry.get("quantity")
            if type(quantity) is not int or quantity <= 0:
                return {"ok": False, "reason": "invalid_quantity", "name": name}
            lookup = self.find_drink(name)
            if not lookup["found"]:
                return {"ok": False, "reason": "drink_not_found", "name": name}
            if lookup["ambiguous"]:
                return {"ok": False, "reason": "ambiguous_drink", "name": name, "candidates": lookup["items"]}
            drink = lookup["items"][0]
            if not drink["available"]:
                return {"ok": False, "reason": "unavailable", "name": drink["name"]}
            line_total = drink["price"] * quantity
            lines.append({"name": drink["name"], "unit_price": drink["price"], "quantity": quantity, "line_total": line_total})
            subtotal += line_total
        discount_rate = Decimal("0")
        if student_discount:
            info = self.get_store_info("student_discount")
            match = re.search(r"(\d+(?:\.\d+)?)\s*%", info.get("content", ""))
            if not match:
                return {"ok": False, "reason": "discount_rate_unavailable"}
            discount_rate = Decimal(match.group(1)) / 100
        total = int((Decimal(subtotal) * (1 - discount_rate)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        rate_percent = discount_rate * 100
        rate_text = str(int(rate_percent)) if rate_percent == rate_percent.to_integral_value() else format(rate_percent, "f")
        return {"ok": True, "lines": lines, "subtotal": subtotal,
                "student_discount_applied": student_discount,
                "discount_rate": f"{rate_text}%", "total": total, "currency": "NTD"}

    def transfer_to_human(self):
        phone = self.get_store_info("phone")
        if not phone["found"]:
            return {"ok": False, "reason": "phone_unavailable"}
        return {"ok": True, "phone": phone["content"], "connected": False}


SYSTEM_PROMPT = """You are the text customer-service agent for 50嵐 Kaohsiung Tuku Store.
Grounding: Answer shop facts only from tool results in this conversation. Never guess a price, availability, policy, address, phone, or calculation. If data is missing or unconfirmed, say so and call transfer_to_human. Do not claim you have connected the customer to staff.
Scope: Help with this shop only. Politely refuse unrelated requests and invite a shop question.
Routing: Use get_store_info for shop policies and location, find_drink for drink prices and availability, calculate_drink_order for every order total, and transfer_to_human for complaints, human requests, unknown shop facts, or unconfirmed delivery. Do not treat a partial drink-name match as a unique item. Only set student_discount=true if the customer explicitly says they qualify by presenting a partner-school student ID; otherwise calculate full price and mention the conditional discount separately. Ask for clarification if drink names or quantities are unclear.
Language and format: Reply in the customer's language; for Chinese use Traditional Chinese. Be concise, polite, and state prices in NT$. Use exact drink names from the data. Do not claim delivery is available at this branch because its service area is unconfirmed.
"""


TOOL_SPECS = [
    {"type": "function", "function": {"name": "get_store_info", "description": "Look up this shop's opening hours, address, phone, payment, student-discount condition, parking, large-order rule, or delivery status. Delivery is unconfirmed and requires human handover.", "parameters": {"type": "object", "properties": {"topic": {"type": "string", "enum": ["hours", "address", "phone", "payment", "student_discount", "delivery", "parking", "Large orders"]}}, "required": ["topic"]}}},
    {"type": "function", "function": {"name": "find_drink", "description": "Look up drink names, prices and availability from the drink database. Search by exact or partial Chinese name; multiple matches require clarification.", "parameters": {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]}}},
    {"type": "function", "function": {"name": "calculate_drink_order", "description": "Calculate an order in code from database prices. Always use for totals. Apply a 10% student discount only when the customer explicitly states they qualify with a partner-school student ID; otherwise use false. Never do arithmetic yourself.", "parameters": {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"name": {"type": "string"}, "quantity": {"type": "integer"}}, "required": ["name", "quantity"]}}, "student_discount": {"type": "boolean"}}, "required": ["items", "student_discount"]}}},
    {"type": "function", "function": {"name": "transfer_to_human", "description": "Get this shop's staff phone number for complaints, requests for a human, missing shop facts, or unconfirmed delivery. Does not actually connect the call.", "parameters": {"type": "object", "properties": {}, "required": []}}},
]


SAFE_FALLBACK = "抱歉，我目前無法根據已確認的資料回答。請稍後再試，或洽詢門市人員。"
OUT_OF_SCOPE = "我只能協助 50嵐高雄土庫店的門市與飲料問題；歡迎詢問營業時間、飲料價格或訂單金額。"


def run_agent(chat_history, client, model, tools, max_steps=6):
    """Return (answer, visible tool trace), rejecting untrusted model tool requests."""
    if not chat_history or chat_history[-1].get("role") != "user":
        return SAFE_FALLBACK, []
    messages = [{"role": "system", "content": SYSTEM_PROMPT}] + list(chat_history)
    functions = {
        "get_store_info": tools.get_store_info,
        "find_drink": tools.find_drink,
        "calculate_drink_order": tools.calculate_drink_order,
        "transfer_to_human": tools.transfer_to_human,
    }
    trace = []
    for step in range(max_steps):
        response = client.chat.completions.create(
            model=model, messages=messages, tools=TOOL_SPECS, temperature=0.2
        )
        message = response.choices[0].message
        calls = message.tool_calls or []
        if not calls:
            if not trace:
                return OUT_OF_SCOPE, trace
            answer = (message.content or SAFE_FALLBACK).strip()
            handovers = [entry for entry in trace if entry["tool"] == "transfer_to_human" and entry["result"].get("ok")]
            if handovers and re.search(r"已.{0,8}(?:轉接|連線|接通)", answer):
                phone = handovers[-1]["result"]["phone"]
                answer = f"目前無法在聊天室直接轉接真人；請撥打 {phone} 洽詢門市人員。"
            return answer, trace
        messages.append({"role": "assistant", "content": message.content or "", "tool_calls": [
            {"id": call.id, "type": "function", "function": {"name": call.function.name, "arguments": call.function.arguments}}
            for call in calls
        ]})
        for call in calls:
            name = call.function.name
            try:
                if name not in functions:
                    raise ValueError("unknown_tool")
                arguments = json.loads(call.function.arguments or "{}")
                if not isinstance(arguments, dict):
                    raise ValueError("invalid_arguments")
                result = functions[name](**arguments)
            except (ValueError, TypeError, KeyError) as error:
                result = {"ok": False, "reason": str(error)}
                trace.append({"step": step + 1, "tool": name, "arguments": call.function.arguments, "result": result})
                return SAFE_FALLBACK, trace
            trace.append({"step": step + 1, "tool": name, "arguments": arguments, "result": result})
            messages.append({"role": "tool", "tool_call_id": call.id, "content": json.dumps(result, ensure_ascii=False)})
    return SAFE_FALLBACK, trace
