import html
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
CHAT_ID = str(os.environ.get("TELEGRAM_CHAT_ID", "")).strip()
PORT = int(os.environ.get("PORT", "10000"))

FIREBASE_PROJECT_ID = "generated-canto-vw2pc"
FIRESTORE_DB_ID = "ai-studio-761ac1aa-e65e-4267-966a-13ae590a82d0"
POLL_INTERVAL_SEC = 15

if not BOT_TOKEN or not CHAT_ID:
    raise SystemExit("ERROR: Render Environment Variables mein TELEGRAM_BOT_TOKEN aur TELEGRAM_CHAT_ID set karein!")

FIRESTORE_QUERY_URL = (
    f"https://firestore.googleapis.com/v1/projects/{FIREBASE_PROJECT_ID}"
    f"/databases/{FIRESTORE_DB_ID}/documents:runQuery"
)
TELEGRAM_API = f"https://api.telegram.org/bot{urllib.parse.quote(BOT_TOKEN, safe='')}"

PKT_TZ = timezone(timedelta(hours=5))
seen_request_ids = set()
initialized = False
alerts_sent = 0
started_at = time.time()


def fmt_pkt(ms: int | float | None) -> str:
    ts = (ms / 1000.0) if ms else time.time()
    return datetime.fromtimestamp(ts, tz=PKT_TZ).strftime("%d %b %Y, %I:%M %p")


def http_json(url: str, payload: dict | None = None, timeout: int = 25):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def send_telegram(text: str, target_chat_id: str = CHAT_ID) -> bool:
    try:
        res = http_json(
            f"{TELEGRAM_API}/sendMessage",
            {
                "chat_id": target_chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=15,
        )
        if not res.get("ok"):
            print("[Telegram Error]", res.get("description"))
            return False
        return True
    except Exception as e:
        print("[Telegram Send Failed]", e)
        return False


def read_field(fields: dict, key: str, default=""):
    f = fields.get(key)
    if not isinstance(f, dict):
        return default
    if "stringValue" in f:
        return str(f["stringValue"])
    if "integerValue" in f:
        return int(f["integerValue"])
    if "doubleValue" in f:
        return float(f["doubleValue"])
    return default


def fetch_pending_withdraws() -> list[dict]:
    rows = http_json(
        FIRESTORE_QUERY_URL,
        {
            "structuredQuery": {
                "from": [{"collectionId": "withdrawRequests"}],
                "where": {
                    "fieldFilter": {
                        "field": {"fieldPath": "status"},
                        "op": "EQUAL",
                        "value": {"stringValue": "PENDING"},
                    }
                },
            }
        },
        timeout=15,
    )
    if not isinstance(rows, list):
        return []

    items = []
    for r in rows:
        doc = r.get("document") if isinstance(r, dict) else None
        if not doc or "fields" not in doc:
            continue
        f = doc["fields"]
        doc_id = str(doc.get("name", "")).rsplit("/", 1)[-1]
        items.append(
            {
                "requestId": str(read_field(f, "requestId", doc_id)),
                "uid": str(read_field(f, "uid", "")),
                "email": str(read_field(f, "email", "")),
                "displayName": str(read_field(f, "displayName", "Google Member")),
                "method": str(read_field(f, "method", "")),
                "accountNumber": str(read_field(f, "accountNumber", "")),
                "accountTitle": str(read_field(f, "accountTitle", "")),
                "amountPkr": int(read_field(f, "amountPkr", 0) or 0),
                "createdAtMillis": int(read_field(f, "createdAtMillis", int(time.time() * 1000)) or 0),
            }
        )
    items.sort(key=lambda x: x["createdAtMillis"])
    return items


def format_withdraw_card(w: dict) -> str:
    lines = [
        "💸 <b>NEW WITHDRAW REQUEST — NOVA PREDICTOR 2.0</b>",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"💰 <b>Amount:</b> Rs {w['amountPkr']:,} PKR",
        f"🏦 <b>Method:</b> {html.escape(w['method'])}",
        f"📱 <b>Account #:</b> <code>{html.escape(w['accountNumber'])}</code> <i>(tap to copy)</i>",
        f"👤 <b>Account Title:</b> {html.escape(w['accountTitle'])}",
        "━━━━━━━━━━━━━━━━━━━━━━",
        f"🙋 <b>User:</b> {html.escape(w['displayName'])}",
    ]
    if w["email"]:
        lines.append(f"📧 <b>Email:</b> {html.escape(w['email'])}")
    lines.extend(
        [
            f"🆔 <b>UID:</b> <code>{html.escape(w['uid'])}</code>",
            f"🧾 <b>Request ID:</b> <code>{html.escape(w['requestId'])}</code>",
            f"🕒 <b>Time (PKT):</b> {html.escape(fmt_pkt(w['createdAtMillis']))}",
            "",
            "✅ Payment bhej kar Admin Panel → Withdraw Session mein <b>Mark Complete</b> kar dein.",
        ]
    )
    return "\n".join(lines)


def firestore_poll_loop():
    global initialized, alerts_sent
    while True:
        try:
            pending = fetch_pending_withdraws()
            if not initialized:
                for w in pending:
                    seen_request_ids.add(w["requestId"])
                initialized = True
                print(f"✅ Bot online. Existing pending withdraws: {len(pending)}")
                send_telegram(
                    f"🤖 <b>Nova Withdraw Bot 24/7 ONLINE!</b>\n"
                    f"⏳ Abhi <b>{len(pending)}</b> PENDING withdraw request(s) maujood hain.\n\n"
                    f"Commands:\n• /pending — Sab pending withdraws dekhein\n• /status — Bot uptime check karein"
                )
            else:
                for w in pending:
                    if w["requestId"] not in seen_request_ids:
                        seen_request_ids.add(w["requestId"])
                        if send_telegram(format_withdraw_card(w)):
                            alerts_sent += 1
        except Exception as e:
            print("[Firestore Poll Error]", e)
        time.sleep(POLL_INTERVAL_SEC)


def telegram_commands_loop():
    last_update_id = 0
    while True:
        try:
            url = f"{TELEGRAM_API}/getUpdates?timeout=20&offset={last_update_id + 1}"
            data = http_json(url, timeout=28)
            if data.get("ok") and isinstance(data.get("result"), list):
                for upd in data["result"]:
                    last_update_id = max(last_update_id, int(upd.get("update_id", 0)))
                    msg = upd.get("message") or {}
                    text = str(msg.get("text") or "").strip()
                    from_chat = str((msg.get("chat") or {}).get("id", ""))
                    if not text or from_chat != CHAT_ID:
                        continue

                    cmd = text.lower().split()[0]
                    if cmd == "/start":
                        send_telegram(
                            "✅ <b>Nova Predictor 2.0 Withdraw Bot Connected!</b>\n"
                            "Har nayi website withdraw request foran yahan aayegi.\n\n"
                            "• /pending — Sab pending withdraws\n• /status — Live bot status"
                        )
                    elif cmd == "/pending":
                        items = fetch_pending_withdraws()
                        if not items:
                            send_telegram("✅ Koi PENDING withdraw request nahi hai.")
                        else:
                            for w in items:
                                send_telegram(format_withdraw_card(w))
                    elif cmd == "/status":
                        mins = int((time.time() - started_at) // 60)
                        send_telegram(
                            f"🟢 <b>Bot Status: ONLINE</b>\n"
                            f"⏱ Uptime: {mins} min\n"
                            f"📨 Alerts Sent: {alerts_sent}\n"
                            f"🕒 PKT: {html.escape(fmt_pkt(None))}"
                        )
        except Exception:
            time.sleep(2)


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        body = json.dumps(
            {
                "ok": True,
                "service": "nova-withdraw-telegram-bot",
                "uptimeSec": int(time.time() - started_at),
                "alertsSent": alerts_sent,
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        return


if __name__ == "__main__":
    threading.Thread(target=firestore_poll_loop, daemon=True).start()
    threading.Thread(target=telegram_commands_loop, daemon=True).start()
    print(f"🚀 Render Python Health Server running on port {PORT}")
    ThreadingHTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()
