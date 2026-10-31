import sqlite3
import tempfile
import unittest
from pathlib import Path

from agent_core import StoreTools, build_database, run_agent, explicit_student_eligibility


class FakeClient:
    def __init__(self, messages):
        self.messages = iter(messages)
        self.requests = []
        self.chat = self
        self.completions = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        return type("Response", (), {"choices": [type("Choice", (), {"message": next(self.messages)})()]})()


def model_message(content="", calls=None):
    return type("Message", (), {"content": content, "tool_calls": calls or []})()


def tool_call(name, arguments):
    return type("Call", (), {"id": "call-1", "function": type("Function", (), {"name": name, "arguments": arguments})()})()


ROOT = Path(__file__).resolve().parents[1]


class StoreToolsTests(unittest.TestCase):
    def test_student_eligibility_rejects_questions_denials_and_retractions(self):
        for question in ['合作學校學生出示學生證有折扣嗎？', '我不是合作學校學生，也沒有學生證',
                         'Does a partner school student ID get a discount?', 'I am not from a partner school and have no student ID']:
            self.assertFalse(explicit_student_eligibility([{'role':'user', 'content':question}]))
        history = [{'role':'user','content':'我是合作學校學生，會出示學生證。'},
                   {'role':'user','content':'更正，我沒有學生證。'}]
        self.assertFalse(explicit_student_eligibility(history))

    def test_no_tool_clarification_is_not_replaced_with_off_topic_refusal(self):
        client = FakeClient([model_message('請問您要哪款飲料、幾杯呢？')])
        answer, trace = run_agent([{'role':'user','content':'幫我算飲料總價'}], client, 'unit-model', self.tools)
        self.assertIn('幾杯', answer)
        self.assertEqual(trace, [])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "shop.db"
        self.connection = build_database(
            self.db_path, ROOT / "my_shop_info.csv", ROOT / "my_menu.csv"
        )
        self.tools = StoreTools(self.connection)

    def tearDown(self):
        self.connection.close()
        self.temp.cleanup()

    def test_builds_tables_from_supplied_csvs(self):
        info_count = self.connection.execute("SELECT COUNT(*) FROM shop_info").fetchone()[0]
        menu_count = self.connection.execute("SELECT COUNT(*) FROM drinks").fetchone()[0]
        self.assertEqual((info_count, menu_count), (8, 82))

    def test_info_lookup_and_unconfirmed_delivery(self):
        self.assertEqual(self.tools.get_store_info("hours")["content"], "Open every day, 09:00 to 22:00.")
        self.assertFalse(self.tools.get_store_info("delivery")["found"])
        self.assertEqual(self.tools.get_store_info("delivery")["reason"], "unconfirmed")

    def test_drink_lookup_and_ambiguity(self):
        self.assertEqual(self.tools.find_drink("珍珠奶茶")["items"][0]["price"], 55)
        self.assertGreater(len(self.tools.find_drink("珍珠")["items"]), 1)
        self.assertFalse(self.tools.find_drink("不存在的飲料")["found"])

    def test_order_total_and_student_discount(self):
        items = [{"name": "珍珠奶茶", "quantity": 1}, {"name": "茉莉綠茶", "quantity": 1}]
        self.assertEqual(self.tools.calculate_drink_order(items)["total"], 85)
        discounted = self.tools.calculate_drink_order(items, student_discount=True)
        self.assertEqual(discounted["subtotal"], 85)
        self.assertEqual(discounted["total"], 77)
        self.assertEqual(discounted["discount_rate"], "10%")

    def test_half_up_rounding(self):
        result = self.tools.calculate_drink_order([{"name": "茉莉綠茶", "quantity": 3}], student_discount=True)
        self.assertEqual(result["total"], 81)
        result = self.tools.calculate_drink_order([{"name": "茉莉綠茶", "quantity": 1}], student_discount=True)
        self.assertEqual(result["total"], 27)

    def test_invalid_quantity_and_ambiguous_item(self):
        for quantity in [0, -1, 1.5, True, "2"]:
            with self.subTest(quantity=quantity):
                self.assertFalse(self.tools.calculate_drink_order([{"name": "珍珠奶茶", "quantity": quantity}])["ok"])
        self.assertFalse(self.tools.calculate_drink_order([{"name": "珍珠", "quantity": 1}])["ok"])

    def test_unavailable_item_and_handover(self):
        self.connection.execute("UPDATE drinks SET available = 0 WHERE name = ?", ("珍珠奶茶",))
        self.connection.commit()
        self.assertFalse(self.tools.calculate_drink_order([{"name": "珍珠奶茶", "quantity": 1}])["ok"])
        self.assertEqual(self.tools.transfer_to_human()["phone"], "07-3552006")

    def test_agent_uses_tool_and_forwards_history(self):
        client = FakeClient([
            model_message(calls=[tool_call("get_store_info", '{"topic":"hours"}')]),
            model_message("營業時間為 09:00–22:00。"),
        ])
        history = [
            {"role": "user", "content": "你們在哪裡？"},
            {"role": "assistant", "content": "請問想查哪間門市？"},
            {"role": "user", "content": "幾點營業？"},
        ]
        answer, trace = run_agent(history, client, "Furen-large", self.tools)
        self.assertIn("09:00", answer)
        self.assertEqual(trace[0]["tool"], "get_store_info")
        self.assertIn(history[0], client.requests[0]["messages"])
        self.assertEqual(client.requests[0]["model"], "Furen-large")

    def test_unknown_tool_and_bad_json_are_refused(self):
        for call in [tool_call("delete_database", "{}"), tool_call("get_store_info", "{bad")]:
            with self.subTest(call=call.function.name):
                answer, trace = run_agent([{"role": "user", "content": "幾點營業？"}],
                                          FakeClient([model_message(calls=[call])]), "Furen-large", self.tools)
                self.assertEqual(len(trace), 1)
                self.assertFalse(trace[0]["result"]["ok"])
                self.assertIn("無法", answer)

    def test_no_tool_answer_and_step_limit_are_safe(self):
        history = [{"role": "user", "content": "你們幾點營業？"}]
        answer, trace = run_agent(history, FakeClient([model_message("我猜是 24 小時")]), "Furen-large", self.tools)
        self.assertEqual(trace, [])
        self.assertNotIn("24 小時", answer)
        client = FakeClient([model_message(calls=[tool_call("get_store_info", '{"topic":"hours"}')])])
        answer, trace = run_agent(history, client, "Furen-large", self.tools, max_steps=1)
        self.assertIn("無法", answer)
        self.assertEqual(len(trace), 1)

    def test_handover_never_claims_a_connection_was_made(self):
        client = FakeClient([
            model_message(calls=[tool_call("transfer_to_human", "{}")]),
            model_message("我已為您轉接至人工客服。"),
        ])
        answer, trace = run_agent([{"role": "user", "content": "我要找店員"}], client, "Furen-large", self.tools)
        self.assertEqual(trace[0]["tool"], "transfer_to_human")
        self.assertNotIn("已為您轉接", answer)
        self.assertIn("07-3552006", answer)

    def test_english_handover_never_claims_connection(self):
        client = FakeClient([
            model_message(calls=[tool_call("transfer_to_human", "{}")]),
            model_message("I've connected you to our staff."),
        ])
        answer, _ = run_agent([{"role": "user", "content": "I want to speak to staff"}], client, "Furen-large", self.tools)
        self.assertNotIn("connected you", answer)
        self.assertIn("07-3552006", answer)

    def test_student_discount_requires_customer_statement(self):
        arguments = '{"items":[{"name":"珍珠奶茶","quantity":1},{"name":"茉莉綠茶","quantity":1}],"student_discount":true}'
        for question, expected_total in [
            ("一杯珍珠奶茶和一杯茉莉綠茶多少錢？", 85),
            ("我是合作學校學生，會出示學生證；一杯珍珠奶茶和一杯茉莉綠茶折扣後多少錢？", 77),
        ]:
            with self.subTest(question=question):
                client = FakeClient([
                    model_message(calls=[tool_call("calculate_drink_order", arguments)]),
                    model_message("已計算。"),
                ])
                _, trace = run_agent([{"role": "user", "content": question}], client, "Furen-large", self.tools)
                self.assertEqual(trace[0]["result"]["total"], expected_total)


if __name__ == "__main__":
    unittest.main()
