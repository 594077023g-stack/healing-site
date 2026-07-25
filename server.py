"""
心里有事 — Stripe 支付后端 (Billing + Invoicing)
启动: python3 server.py
密钥: 放 .env 文件或环境变量 STRIPE_SECRET_KEY
"""

import os
import json
import time
import hashlib
import secrets
from pathlib import Path
from datetime import datetime, timezone
from http.server import HTTPServer, SimpleHTTPRequestHandler
from urllib.parse import parse_qs, urlparse
import stripe

# 从 .env 文件加载配置（优先环境变量，其次 .env 文件）
_ENV_FILE = Path(__file__).parent / ".env"
if _ENV_FILE.exists():
    for line in _ENV_FILE.read_text().strip().split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, val = line.partition("=")
            key, val = key.strip(), val.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = val

PORT = int(os.environ.get("PORT", 8080))
STRIPE_SECRET_KEY = os.environ.get("STRIPE_SECRET_KEY", "").strip()
STRIPE_WEBHOOK_SECRET = os.environ.get("STRIPE_WEBHOOK_SECRET", "").strip()
YOUR_DOMAIN = os.environ.get("DOMAIN", "https://healing-site-520.onrender.com").strip()

stripe.api_key = STRIPE_SECRET_KEY

# Admin
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "healing2025").strip()
ADMIN_SESSIONS = {}  # token → expiry_timestamp

def check_admin(request):
    """Check if request has valid admin cookie"""
    cookie = request.headers.get("Cookie", "")
    for part in cookie.split(";"):
        part = part.strip()
        if part.startswith("admin_token="):
            token = part.split("=", 1)[1]
            if token in ADMIN_SESSIONS and ADMIN_SESSIONS[token] > time.time():
                return True
    return False

# ============================================================
# 数据存储
# ============================================================
DATA_DIR = Path(__file__).parent / "data"
DATA_DIR.mkdir(exist_ok=True)
PAYMENTS_FILE = DATA_DIR / "payments.json"
CUSTOMERS_FILE = DATA_DIR / "customers.json"


def load_json(path):
    if path.exists():
        return json.loads(path.read_text())
    return []


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


def save_payment(record):
    payments = load_json(PAYMENTS_FILE)
    payments.append(record)
    save_json(PAYMENTS_FILE, payments)


def save_customer(record):
    customers = load_json(CUSTOMERS_FILE)
    customers.append(record)
    save_json(CUSTOMERS_FILE, customers)


# ============================================================
# 产品配置 (USD)
# ============================================================
# 轻语 → $0.90 一次性
# 深谈 → $9.90 一次性
# 陪伴 → $999/周 订阅
PRODUCTS = {
    "qingyu_090": {
        "name": "心里有事 · 轻语",
        "description": "一次温柔的倾听 — 48小时内回复",
        "price": 90,
        "currency": "usd",
        "mode": "payment",
        "plan_name": "轻语",
        "plan_label": "一次温柔的倾听",
        "features": [
            "提交你的故事",
            "收到一封暖心回信",
            "48小时内回复",
            "匿名倾诉空间",
        ],
    },
    "shentan_990": {
        "name": "心里有事 · 深谈",
        "description": "三次深入的对话 — 24小时内回复",
        "price": 990,
        "currency": "usd",
        "mode": "payment",
        "plan_name": "深谈",
        "plan_label": "三次深入的对话",
        "features": [
            "提交你的故事",
            "三次来回深入对话",
            "24小时内回复",
            "个性化疗愈建议",
            "可上传语音故事",
        ],
    },
    "peiban_9900": {
        "name": "心里有事 · 陪伴",
        "description": "一周的持续陪伴，每周自动续费 — 12小时内回复",
        "price": 99900,
        "currency": "usd",
        "mode": "subscription",
        "plan_name": "陪伴",
        "plan_label": "一周的持续陪伴",
        "interval": "week",
        "interval_count": 1,
        "features": [
            "不限次数倾诉",
            "一周内随时对话",
            "12小时内回复",
            "专属疗愈计划",
            "语音通话支持",
        ],
    },
}


# ============================================================
# 发票生成
# ============================================================
INVOICE_DIR = DATA_DIR / "invoices"
INVOICE_DIR.mkdir(exist_ok=True)


def generate_invoice(customer_email, plan_name, amount, currency, stripe_invoice_id=None):
    """生成本地发票 HTML 并返回绝对路径"""
    invoice_id = f"INV-{datetime.now().strftime('%Y%m%d')}-{hashlib.md5(str(time.time()).encode()).hexdigest()[:6].upper()}"
    invoice_date = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>发票 {invoice_id}</title>
<style>
  body {{ font-family: 'PingFang SC','Microsoft YaHei',sans-serif; color:#3d3835; max-width:680px; margin:40px auto; padding:0 20px; }}
  .header {{ border-bottom:2px solid #8b9d83; padding-bottom:16px; margin-bottom:24px; }}
  .header h1 {{ font-size:1.4rem; color:#8b9d83; margin:0 0 4px; }}
  .header .id {{ font-size:0.85rem; color:#9a918b; }}
  table {{ width:100%; border-collapse:collapse; margin:24px 0; }}
  th {{ background:#f5f2ed; text-align:left; padding:10px 12px; font-size:0.85rem; }}
  td {{ padding:10px 12px; font-size:0.9rem; border-bottom:1px solid #e8e3de; }}
  .total {{ font-weight:600; font-size:1.1rem; text-align:right; padding:16px 0; }}
  .footer {{ margin-top:32px; font-size:0.8rem; color:#9a918b; border-top:1px solid #e8e3de; padding-top:16px; }}
  .stripe-ref {{ font-size:0.75rem; color:#9a918b; }}
</style>
</head>
<body>
<div class="header">
  <h1>🌿 心里有事</h1>
  <div class="id">发票编号: {invoice_id}</div>
  <div>日期: {invoice_date}</div>
</div>

<p><strong>致：</strong>{customer_email}</p>

<table>
  <tr><th>项目</th><th>描述</th><th style="text-align:right">金额</th></tr>
  <tr>
    <td>疗愈方案 · {plan_name}</td>
    <td>心灵疗愈对话服务</td>
    <td style="text-align:right">¥{amount:.2f} {currency.upper()}</td>
  </tr>
</table>

<div class="total">合计: ¥{amount:.2f} {currency.upper()}</div>

<div class="footer">
  <p>心里有事 — 你不是有病，你只是心里有事。</p>
  <p>此发票由 Stripe 支付系统自动生成。</p>
  {f'<p class="stripe-ref">Stripe Invoice: {stripe_invoice_id}</p>' if stripe_invoice_id else ''}
</div>
</body>
</html>"""

    filename = f"{invoice_id}.html"
    filepath = INVOICE_DIR / filename
    filepath.write_text(html)
    return str(filepath), invoice_id


# ============================================================
# HTTP Handler
# ============================================================
class StripeHandler(SimpleHTTPRequestHandler):

    def do_GET(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/create-checkout-session":
            return self.create_checkout_session(parsed)

        if parsed.path == "/api/payments":
            if not check_admin(self):
                return self.serve_admin_login()
            return self.serve_payments_list()

        if parsed.path == "/api/stories":
            if not check_admin(self):
                return self.serve_admin_login()
            return self.serve_stories_list()

        if parsed.path == "/admin":
            if check_admin(self):
                return self.serve_admin_dashboard()
            return self.serve_admin_login()

        if parsed.path == "/admin/logout":
            self.send_response(302)
            self.send_header("Set-Cookie", "admin_token=; Path=/; Max-Age=0")
            self.send_header("Location", "/admin")
            self.end_headers()
            return

        if parsed.path == "/success":
            return self.serve_success()

        if parsed.path == "/cancel":
            return self.serve_cancel()

        # English version
        if parsed.path == "/en" or parsed.path == "/en.html":
            return self.serve_en()

        return super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)

        if parsed.path == "/api/create-checkout-session":
            return self.create_checkout_session(parsed)

        if parsed.path == "/api/webhook":
            return self.handle_webhook()

        if parsed.path == "/api/submit-story":
            return self.handle_story_submission()

        if parsed.path == "/admin/login":
            return self.handle_admin_login()

        return super().do_POST()

    # ========================
    # Checkout Session
    # ========================
    def create_checkout_session(self, parsed):
        params = {}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(length)
            try:
                params = json.loads(raw)
            except json.JSONDecodeError:
                pass
        else:
            params = {k: v[0] for k, v in parse_qs(parsed.query).items()}

        product_key = params.get("product", "shentan_990")
        customer_email = params.get("email", "").strip()

        if product_key not in PRODUCTS:
            return self.json_response(400, {"error": "Invalid product"})

        product = PRODUCTS[product_key]

        try:
            # 查找或创建 Stripe Customer
            customer_id = None
            if customer_email and STRIPE_SECRET_KEY:
                existing = stripe.Customer.list(email=customer_email, limit=1)
                if existing.data:
                    customer_id = existing.data[0].id
                else:
                    customer = stripe.Customer.create(
                        email=customer_email,
                        metadata={"source": "healing-site"},
                    )
                    customer_id = customer.id
                    save_customer({
                        "stripe_customer_id": customer_id,
                        "email": customer_email,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                    })

            session_kwargs = {
                "payment_method_types": ["card", "alipay", "wechat_pay"],
                "payment_method_options": {
                    "wechat_pay": {"client": "web"},
                },
                "line_items": [{
                    "price_data": {
                        "currency": product["currency"],
                        "product_data": {
                            "name": product["name"],
                            "description": product["description"],
                        },
                        "unit_amount": product["price"],
                        **(dict(recurring={
                            "interval": product["interval"],
                            "interval_count": product["interval_count"],
                        }) if product["mode"] == "subscription" else {}),
                    },
                    "quantity": 1,
                }],
                "mode": product["mode"],
                "success_url": YOUR_DOMAIN + "/success?session_id={CHECKOUT_SESSION_ID}",
                "cancel_url": YOUR_DOMAIN + "/cancel",
                "metadata": {
                    "plan": product["plan_name"],
                    "source": "healing-site",
                    "product_key": product_key,
                },
                "invoice_creation": {"enabled": True} if product["mode"] == "payment" else {},
            }

            if customer_id:
                session_kwargs["customer"] = customer_id
            elif customer_email:
                session_kwargs["customer_email"] = customer_email

            checkout_session = stripe.checkout.Session.create(**session_kwargs)
            return self.json_response(200, {
                "url": checkout_session.url,
                "session_id": checkout_session.id,
            })

        except stripe.error.StripeError as e:
            return self.json_response(500, {"error": str(e)})

    # ========================
    # Webhook
    # ========================
    def handle_webhook(self):
        length = int(self.headers.get("Content-Length", 0))
        payload = self.rfile.read(length)
        sig_header = self.headers.get("Stripe-Signature", "")

        event = None
        try:
            if STRIPE_WEBHOOK_SECRET:
                event = stripe.Webhook.construct_event(
                    payload, sig_header, STRIPE_WEBHOOK_SECRET
                )
            else:
                event = json.loads(payload)
        except (ValueError, stripe.error.SignatureVerificationError) as e:
            return self.json_response(400, {"error": str(e)})

        event_type = event.get("type", "")

        if event_type == "checkout.session.completed":
            session = event["data"]["object"]
            customer_email = session.get("customer_details", {}).get("email", "")
            amount_total = (session.get("amount_total", 0) or 0) / 100
            currency = (session.get("currency", "cny") or "cny").upper()
            plan = session.get("metadata", {}).get("plan", "未知")
            invoice_id = session.get("invoice", "")

            # 生成发票
            invoice_path, local_invoice_id = generate_invoice(
                customer_email or "未知用户",
                plan,
                amount_total,
                currency,
                invoice_id,
            )

            # 保存支付记录
            record = {
                "session_id": session.get("id", ""),
                "customer_email": customer_email,
                "plan": plan,
                "amount": amount_total,
                "currency": currency,
                "stripe_invoice_id": invoice_id,
                "local_invoice_id": local_invoice_id,
                "invoice_path": invoice_path,
                "payment_status": session.get("payment_status", ""),
                "created_at": datetime.now(timezone.utc).isoformat(),
            }
            save_payment(record)
            print(f"  ✅ 支付成功: {plan} ¥{amount_total} — {customer_email}")
            print(f"  📄 发票: {local_invoice_id}")

        elif event_type == "invoice.paid":
            inv = event["data"]["object"]
            print(f"  📄 发票已支付: {inv.get('id', '')} — ¥{inv.get('amount_paid', 0)/100}")

        elif event_type == "customer.subscription.updated":
            sub = event["data"]["object"]
            print(f"  🔁 订阅更新: {sub.get('id')} — status={sub.get('status')}")

        return self.json_response(200, {"received": True})

    # ========================
    # Story Submission
    # ========================
    def handle_story_submission(self):
        """Save anonymous story submissions"""
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return self.json_response(400, {"error": "Invalid JSON"})

        story = data.get("story", "").strip()
        email = data.get("email", "").strip()
        lang = data.get("lang", "zh")

        if not story or len(story) < 10:
            return self.json_response(400, {"error": "Story too short"})

        record = {
            "story": story,
            "email": email if email else "anonymous",
            "lang": lang,
            "length": len(story),
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save to stories file
        stories = load_json(DATA_DIR / "stories.json")
        stories.append(record)
        save_json(DATA_DIR / "stories.json", stories)

        print(f"  📖 新故事 ({lang}): {len(story)}字 — {email if email else '匿名'}")
        print(f"  📊 总计: {len(stories)} 篇")

        return self.json_response(200, {
            "received": True,
            "message": "你的心事已经收到 💚 我会在48小时内回信。"
        })

    # ========================
    # 支付记录列表（管理用）
    # ========================
    def serve_payments_list(self):
        payments = load_json(PAYMENTS_FILE)
        customers = load_json(CUSTOMERS_FILE)

        rows = ""
        total_revenue = 0
        for p in reversed(payments[-50:]):  # 最近 50 条
            total_revenue += p.get("amount", 0)
            rows += f"""
            <tr>
              <td>{p.get('created_at', '')[:19]}</td>
              <td>{p.get('customer_email', '')}</td>
              <td>{p.get('plan', '')}</td>
              <td style="text-align:right">¥{p.get('amount', 0):.2f}</td>
              <td><a href="/data/invoices/{Path(p.get('invoice_path', '')).name}">📄</a></td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><title>支付记录 — 心里有事</title>
<style>
  body {{ font-family:'PingFang SC','Microsoft YaHei',sans-serif; color:#3d3835; max-width:900px; margin:40px auto; padding:0 20px; }}
  h1 {{ font-size:1.5rem; color:#8b9d83; }}
  .summary {{ background:#e8efe4; padding:16px 20px; border-radius:12px; margin-bottom:24px; display:flex; gap:40px; }}
  .summary div {{ font-size:1.2rem; }}
  .summary .label {{ font-size:0.8rem; color:#6b635e; }}
  table {{ width:100%; border-collapse:collapse; }}
  th {{ background:#f5f2ed; text-align:left; padding:10px 12px; font-size:0.85rem; }}
  td {{ padding:10px 12px; font-size:0.9rem; border-bottom:1px solid #e8e3de; }}
  a {{ color:#8b9d83; }}
</style></head>
<body>
<h1>💳 支付记录</h1>
<div class="summary">
  <div><span class="label">总订单</span><br>{len(payments)}</div>
  <div><span class="label">客户数</span><br>{len(customers)}</div>
  <div><span class="label">总收入</span><br>¥{total_revenue:.2f}</div>
</div>
<table>
  <tr><th>时间</th><th>客户</th><th>方案</th><th style="text-align:right">金额</th><th>发票</th></tr>
  {rows}
</table>
<p style="margin-top:16px;font-size:0.8rem;color:#9a918b;">
  ⚠️ 此页面仅在本地可访问。部署到公网后请添加密码保护。
</p>
</body></html>"""
        return self.html_response(html)

    # ========================
    # 页面
    # ========================
    def serve_success(self):
        html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>支付成功 — 心里有事</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif; background:#fef9f0; color:#3d3835; display:flex; align-items:center; justify-content:center; min-height:100vh; text-align:center; }
  .card { background:#fff; border-radius:20px; padding:3rem 2.5rem; max-width:440px; width:90%; box-shadow:0 8px 40px rgba(60,50,40,0.08); }
  .icon { font-size:4rem; margin-bottom:1rem; }
  h1 { font-family:'Noto Serif SC',serif; font-size:1.8rem; margin-bottom:0.8rem; color:#8b9d83; }
  p { color:#6b635e; font-size:1rem; line-height:1.8; margin-bottom:1.5rem; }
  .btn { display:inline-block; padding:14px 36px; border-radius:50px; background:#8b9d83; color:#fff; text-decoration:none; font-size:0.95rem; font-weight:500; transition:all .3s; box-shadow:0 4px 16px rgba(139,157,131,0.3); }
  .btn:hover { background:#7d8f75; transform:translateY(-2px); }
</style>
</head>
<body>
<div class="card">
  <div class="icon">💚</div>
  <h1>支付成功</h1>
  <p>你的心事已经收到。<br>我会在承诺的时间内认真阅读，<br>用最温柔的文字给你回信。</p>
  <p style="font-size:0.85rem;color:#9a918b;">📬 发票已发送至你的邮箱。<br>这不是结束，这是一段对话的开始。</p>
  <a href="/" class="btn">🌿 回到首页</a>
</div>
</body>
</html>"""
        return self.html_response(html)

    def serve_cancel(self):
        html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>支付取消 — 心里有事</title>
<style>
  * { margin:0; padding:0; box-sizing:border-box; }
  body { font-family:'PingFang SC','Microsoft YaHei','Noto Sans SC',sans-serif; background:#fef9f0; color:#3d3835; display:flex; align-items:center; justify-content:center; min-height:100vh; text-align:center; }
  .card { background:#fff; border-radius:20px; padding:3rem 2.5rem; max-width:440px; width:90%; box-shadow:0 8px 40px rgba(60,50,40,0.08); }
  .icon { font-size:4rem; margin-bottom:1rem; }
  h1 { font-family:'Noto Serif SC',serif; font-size:1.8rem; margin-bottom:0.8rem; }
  p { color:#6b635e; font-size:1rem; line-height:1.8; margin-bottom:1.5rem; }
  .btn { display:inline-block; padding:14px 36px; border-radius:50px; background:transparent; color:#c4826b; text-decoration:none; font-size:0.95rem; font-weight:500; transition:all .3s; border:1.5px solid #c4826b; }
  .btn:hover { background:#f5e6df; transform:translateY(-2px); }
  .btn.p { background:#8b9d83; color:#fff; border:none; margin-left:0.5rem; box-shadow:0 4px 16px rgba(139,157,131,0.3); }
  .btn.p:hover { background:#7d8f75; }
</style>
</head>
<body>
<div class="card">
  <div class="icon">🌿</div>
  <h1>支付已取消</h1>
  <p>没关系，慢慢来。<br>当你准备好了，我一直在这里。</p>
  <a href="/" class="btn">回到首页</a>
  <a href="/#pricing" class="btn p">查看方案</a>
</div>
</body>
</html>"""
        return self.html_response(html)

    def serve_en(self):
        """Serve English version of the site"""
        en_path = Path(__file__).parent / "en.html"
        if en_path.exists():
            html = en_path.read_text()
            return self.html_response(html)
        return self.serve_cancel()  # fallback

    # ========================
    # Admin Auth & Pages
    # ========================
    def handle_admin_login(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            data = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            data = parse_qs(raw.decode()) if raw else {}

        password = ""
        if isinstance(data, dict):
            password = data.get("password", "")
        else:
            password = data.get("password", [""])[0]

        if password == ADMIN_PASSWORD:
            token = secrets.token_hex(32)
            ADMIN_SESSIONS[token] = time.time() + 86400  # 24h
            self.send_response(302)
            self.send_header("Set-Cookie", f"admin_token={token}; Path=/; HttpOnly; SameSite=Lax; Max-Age=86400")
            self.send_header("Location", "/admin")
            self.end_headers()
        else:
            return self.serve_admin_login("密码错误")

    def serve_admin_login(self, error=""):
        msg = f'<p style="color:#c4826b;margin-bottom:1rem;">{error}</p>' if error else ""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>管理后台 — 心里有事</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#fef9f0;color:#3d3835;display:flex;align-items:center;justify-content:center;min-height:100vh}}
.card{{background:#fff;border-radius:16px;padding:3rem;max-width:380px;width:90%;box-shadow:0 8px 40px rgba(60,50,40,0.08);text-align:center}}
h1{{font-family:'Noto Serif SC',serif;color:#8b9d83;margin-bottom:1.5rem}}
input{{width:100%;padding:12px;border:1.5px solid #e8e3de;border-radius:10px;font-size:1rem;margin-bottom:1rem;font-family:inherit}}
button{{width:100%;padding:12px;background:#8b9d83;color:#fff;border:none;border-radius:50px;font-size:1rem;cursor:pointer;font-weight:500}}
button:hover{{background:#7d8f75}}
</style>
</head>
<body>
<div class="card">
<h1>🌿 管理后台</h1>
{msg}
<form method="post" action="/admin/login">
<input type="password" name="password" placeholder="输入管理密码" autofocus required>
<button type="submit">登录</button>
</form>
</div>
</body></html>"""
        return self.html_response(html)

    def serve_admin_dashboard(self):
        payments = load_json(PAYMENTS_FILE)
        stories = load_json(DATA_DIR / "stories.json")
        total = sum(p.get("amount", 0) for p in payments)

        pay_rows = ""
        for p in reversed(payments[-20:]):
            pay_rows += f'<tr><td>{p.get("created_at","")[:16]}</td><td>{p.get("customer_email","")}</td><td>{p.get("plan","")}</td><td style="text-align:right">${p.get("amount",0):.2f}</td></tr>'

        story_rows = ""
        for s in reversed(stories[-20:]):
            preview = s.get("story","")[:50] + ("..." if len(s.get("story",""))>50 else "")
            story_rows += f'<tr><td>{s.get("submitted_at","")[:16]}</td><td>{s.get("email","")}</td><td>{s.get("lang","")}</td><td>{preview}</td></tr>'

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>管理后台 — 心里有事</title>
<style>
*{{margin:0;padding:0;box-sizing:border-box}}
body{{font-family:'PingFang SC','Microsoft YaHei',sans-serif;background:#fef9f0;color:#3d3835;padding:40px 20px;max-width:1000px;margin:0 auto}}
h1{{color:#8b9d83;margin-bottom:1.5rem}}
.stats{{display:flex;gap:24px;margin-bottom:2rem}}
.stat{{background:#fff;padding:20px 24px;border-radius:12px;box-shadow:0 2px 12px rgba(60,50,40,0.06);flex:1;text-align:center}}
.stat .num{{font-size:2rem;font-weight:600;color:#8b9d83}}
.stat .label{{font-size:0.8rem;color:#9a918b;margin-top:4px}}
h2{{font-size:1.2rem;margin:2rem 0 1rem;color:#3d3835}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 12px rgba(60,50,40,0.06);margin-bottom:2rem}}
th{{background:#f5f2ed;text-align:left;padding:10px 12px;font-size:0.85rem}}
td{{padding:10px 12px;font-size:0.9rem;border-bottom:1px solid #e8e3de}}
.btn{{display:inline-block;padding:8px 20px;background:#c4826b;color:#fff;border-radius:20px;text-decoration:none;font-size:0.8rem}}
.logout{{background:transparent;color:#c4826b;border:1px solid #c4826b}}
</style>
</head>
<body>
<h1>🌿 心里有事 · 管理后台</h1>
<a href="/admin/logout" class="btn logout" style="float:right;margin-top:-3rem">退出</a>

<div class="stats">
<div class="stat"><div class="num">¥{total:.2f}</div><div class="label">总收入</div></div>
<div class="stat"><div class="num">{len(payments)}</div><div class="label">订单</div></div>
<div class="stat"><div class="num">{len(stories)}</div><div class="label">故事</div></div>
</div>

<h2>💳 最近支付</h2>
<table><tr><th>时间</th><th>客户</th><th>方案</th><th style="text-align:right">金额</th></tr>
{pay_rows if pay_rows else '<tr><td colspan="4" style="text-align:center;color:#9a918b;padding:2rem">暂无支付记录</td></tr>'}
</table>

<h2>📖 最近故事</h2>
<table><tr><th>时间</th><th>邮箱</th><th>语言</th><th>预览</th></tr>
{story_rows if story_rows else '<tr><td colspan="4" style="text-align:center;color:#9a918b;padding:2rem">暂无故事</td></tr>'}
</table>

<p style="font-size:0.8rem;color:#9a918b;text-align:center;margin-top:2rem">🔒 此页面仅管理员可访问</p>
</body></html>"""
        return self.html_response(html)

    def serve_stories_list(self):
        stories = load_json(DATA_DIR / "stories.json")
        rows = ""
        for s in reversed(stories[-50:]):
            story = s.get("story","").replace("<","&lt;").replace(">","&gt;")
            rows += f"""<tr>
<td>{s.get("submitted_at","")[:16]}</td>
<td>{s.get("email","")}</td>
<td>{s.get("lang","")}</td>
<td style="max-width:400px;word-break:break-word">{story}</td>
</tr>"""
        html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8"><title>故事列表</title>
<style>body{{font-family:sans-serif;padding:20px}}table{{width:100%;border-collapse:collapse}}
th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #ddd;font-size:14px}}
th{{background:#f5f5f5}}</style></head>
<body><h2>📖 收件箱 ({len(stories)})</h2>
<table><tr><th>时间</th><th>邮箱</th><th>语言</th><th>内容</th></tr>{rows}</table></body></html>"""
        return self.html_response(html)

    # ========================
    # Helpers
    # ========================
    def json_response(self, status, data):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def html_response(self, html):
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def log_message(self, format, *args):
        print(f"  {args[0]}" if args else "")


# ============================================================
# Main
# ============================================================
def main():
    if not STRIPE_SECRET_KEY:
        print("=" * 55)
        print("  ⚠️  未设置 STRIPE_SECRET_KEY")
        print("  获取密钥: https://dashboard.stripe.com/apikeys")
        print("=" * 55)
    else:
        print(f"  💳 Stripe: 已配置 ✅")

    print(f"  🌿 心里有事 — http://localhost:{PORT}")
    print(f"  📊 支付记录: http://localhost:{PORT}/api/payments")
    print(f"  🔔 Webhook: POST /api/webhook")
    print(f"  ⏎  Ctrl+C 停止")
    print()

    server = HTTPServer(("0.0.0.0", PORT), StripeHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n  👋 服务器已停止")
        server.server_close()


if __name__ == "__main__":
    main()
