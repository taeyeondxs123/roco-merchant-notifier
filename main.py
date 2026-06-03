import os
import requests
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from jinja2 import Environment, FileSystemLoader
from playwright.async_api import async_playwright

# ================= 1. 配置区域 =================
ROCOM_API_KEY = os.environ.get("ROCOM_API_KEY")
IMGBB_KEY = os.environ.get("IMGBB_KEY")
NOTIFYME_UUID = os.environ.get("NOTIFYME_UUID")
BARK_KEY = os.environ.get("BARK_KEY")

# Cloudflare Worker 群发配置（可选，未配置时走原有直接推送）
CF_WORKER_URL = os.environ.get("CF_WORKER_URL", "").strip()
CF_API_KEY = os.environ.get("CF_API_KEY", "").strip()

GAME_API_URL = "https://wegame.shallow.ink/api/v1/games/rocom/merchant/info?refresh=true"
NOTIFYME_SERVER = "https://notifyme-server.wzn556.top/api/send"
ASSETS_DIR = os.path.abspath("assets/yuanxing-shangren")
HTML_TEMPLATE_FILE = "index.html"
TEMP_RENDER_FILE = "temp_render.html"

# ================= 2. 时间与数据处理逻辑 =================

def get_beijing_time():
    return datetime.now(timezone(timedelta(hours=8)))

def format_timestamp(ts_ms):
    if not ts_ms: return "--:--"
    dt = datetime.fromtimestamp(int(ts_ms) / 1000, tz=timezone(timedelta(hours=8)))
    return dt.strftime("%H:%M")

def get_round_info():
    now = get_beijing_time()
    start_time = now.replace(hour=8, minute=0, second=0, microsecond=0)
    
    if now < start_time:
        return {"current": "未开放", "total": 4, "countdown": "尚未开市"}
    
    delta_seconds = int((now - start_time).total_seconds())
    round_index = (delta_seconds // (4 * 3600)) + 1
    
    if round_index > 4:
        return {"current": 4, "total": 4, "countdown": "今日已收市"}
    
    round_end = start_time + timedelta(hours=round_index * 4)
    remaining = round_end - now
    hours, rem = divmod(int(remaining.total_seconds()), 3600)
    minutes, _ = divmod(rem, 60)
    
    countdown_str = f"{hours}小时{minutes}分钟" if hours > 0 else f"{minutes}分钟"
    
    return {
        "current": round_index,
        "total": 4,
        "countdown": countdown_str
    }

def process_data_for_template(data):
    if not data: return {}
    
    now_ms = int(get_beijing_time().timestamp() * 1000)
    round_info = get_round_info()
    
    activities = data.get("merchantActivities") or []
    activity = activities[0] if activities else {}
    all_items = (activity.get("get_props") or []) + (activity.get("get_pets") or [])
    
    active_products = []
    for item in all_items:
        s_time = item.get("start_time")
        e_time = item.get("end_time")
        
        if s_time and e_time:
            if int(s_time) <= now_ms < int(e_time):
                active_products.append({
                    "name": item.get("name", "未知"),
                    "image": item.get("icon_url", ""),
                    "time_label": f"{format_timestamp(s_time)} - {format_timestamp(e_time)}"
                })
        else:
            active_products.append({
                "name": item.get("name", "未知"),
                "image": item.get("icon_url", ""),
                "time_label": "全天供应"
            })
            
    return {
        "title": activity.get("name", "远行商人"),
        "subtitle": activity.get("start_date", "每日 08:00 / 12:00 / 16:00 / 20:00 刷新"),
        "product_count": len(active_products),
        "round_info": round_info,
        "products": active_products,
        "_res_path": "",
        "background": "img/bg.C8CUoi7I.jpg",
        "titleIcon": True
    }

# ================= 3. 图像渲染与上传 =================

async def render_to_image(processed_data):
    if not processed_data or processed_data["product_count"] == 0:
        print("当前无活跃商品，跳过渲染")
        return None
    
    screenshot_file = "merchant_render.jpg"
    temp_html_path = os.path.join(ASSETS_DIR, TEMP_RENDER_FILE)
    
    try:
        env = Environment(loader=FileSystemLoader(ASSETS_DIR))
        template = env.get_template(HTML_TEMPLATE_FILE)
        rendered_html = template.render(processed_data)
        
        with open(temp_html_path, "w", encoding="utf-8") as f:
            f.write(rendered_html)
            
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page()
            await page.set_viewport_size({"width": 900, "height": 1200})
            await page.goto(f"file://{temp_html_path}")
            await page.evaluate("document.fonts.ready")
            await page.wait_for_load_state("networkidle")
            
            data_region = page.locator('.merchant-page')
            await data_region.screenshot(path=screenshot_file, type="jpeg", quality=90)
            
            await browser.close()
            print(f"✅ 图片渲染成功: {screenshot_file}")
            return screenshot_file
            
    except Exception as e:
        print(f"❌ 渲染图片失败: {e}")
        return None
    finally:
        if os.path.exists(temp_html_path): os.remove(temp_html_path)

async def upload_to_imgbb(image_path):
    if not image_path or not IMGBB_KEY: return None
    try:
        with open(image_path, "rb") as f:
            res = requests.post("https://api.imgbb.com/1/upload", data={"key": IMGBB_KEY}, files={"image": f}, timeout=30)
            json_data = res.json()
            if json_data.get("status") == 200:
                print("✅ 图床上传成功")
                return json_data["data"]["url"]
            else:
                print(f"❌ 图床上传失败: {json_data.get('error', {}).get('message')}")
                return None
    except Exception as e:
        print(f"❌ 图床请求异常: {e}")
        return None

# ================= 4. CF Worker 群发（可选） =================

def push_via_cf(title, body, image_url):
    """通过 Cloudflare Worker 群发给所有订阅者"""
    if not CF_WORKER_URL or not CF_API_KEY:
        print("CF_WORKER_URL / CF_API_KEY 未配置，跳过群发")
        return False
    
    try:
        payload = {
            "title": title,
            "body": body,
            "image_url": image_url or ""
        }
        headers = {
            "Content-Type": "application/json",
            "X-Api-Key": CF_API_KEY
        }
        resp = requests.post(
            f"{CF_WORKER_URL}/broadcast",
            json=payload,
            headers=headers,
            timeout=60
        )
        print(f"CF Worker 群发响应: {resp.status_code} {resp.text[:300]}")
        return resp.status_code == 200
    except Exception as e:
        print(f"CF Worker 群发异常: {e}")
        return False

# ================= 5. 直接推送（原有方式，仍然保留） =================

def push_direct(title, body, image_url):
    """直接推送给你自己（原有方式）"""
    # 同时推 NotifyMe + Bark
    if NOTIFYME_UUID:
        payload = {
            "data": {
                "uuid": NOTIFYME_UUID, "ttl": 86400, "priority": "high",
                "data": {
                    "title": title, "body": body, "group": "洛克王国", "bigText": True, "record": 1,
                    "markdown": f"{body}\n\n![render]({image_url})" if image_url else body
                }
            }
        }
        try:
            resp = requests.post(NOTIFYME_SERVER, json=payload, timeout=10)
            print("NotifyMe HTTP:", resp.status_code, resp.text[:200])
        except Exception as e:
            print("NotifyMe exception:", type(e).__name__, str(e))
    
    if BARK_KEY:
        try:
            requests.post(f"https://api.day.app/{BARK_KEY}", data={
                "title": title, "body": body, "group": "洛克王国", "image": image_url, "isArchive": 1
            }, timeout=10)
            print("✅ Bark 推送已发送")
        except: pass

# ================= 6. 主入口 =================

async def main():
    # --- 获取数据 ---
    try:
        resp = requests.get(GAME_API_URL, headers={"X-API-Key": ROCOM_API_KEY}, timeout=30)
        resp.raise_for_status()
        raw_data = resp.json().get("data", {})
        err = None if resp.json().get("code") == 0 else resp.json().get("message")
    except Exception as e:
        raw_data, err = None, f"请求异常: {e}"
    
    if err or not raw_data:
        push_direct("⚠️ 监控异常", err or "无法获取数据", None)
        return

    processed = process_data_for_template(raw_data)
    item_names = [p["name"] for p in processed["products"]]
    push_body = f"当前售卖: {'、'.join(item_names)}" if item_names else "当前暂无商品"
    
    # --- 渲染图片 ---
    local_img = await render_to_image(processed)
    img_url = await upload_to_imgbb(local_img)
    
    title = "📢 远行商人已刷新"
    
    # --- 双重推送 ---
    # 1. 推送给你自己（原有逻辑，永久保留）
    push_direct(title, push_body, img_url)
    
    # 2. 通过 CF Worker 群发给所有订阅者（可选）
    if CF_WORKER_URL and CF_API_KEY:
        print("正在通过 CF Worker 群发给所有订阅者...")
        cf_ok = push_via_cf(title, push_body, img_url)
        if cf_ok:
            print("✅ CF Worker 群发成功")
        else:
            print("⚠️ CF Worker 群发失败（已直接推送给你）")
    else:
        print("CF Worker 未配置，跳过群发（已有直接推送）")

if __name__ == "__main__":
    asyncio.run(main())