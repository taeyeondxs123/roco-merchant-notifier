import os, asyncio, json, base64, re, urllib.request, time
import requests

NOTIFYME_UUID = os.environ.get("NOTIFYME_UUID", "").strip()
BARK_KEY      = os.environ.get("BARK_KEY", "").strip()
ROCOM_API_KEY = os.environ.get("ROCOM_API_KEY", "").strip()
IMGBB_KEY     = os.environ.get("IMGBB_KEY", "").strip()

GITHUB_REPO   = os.environ.get("GITHUB_REPOSITORY", "")
GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
SUBSCRIBERS_FILE = "subscribers.json"

print(f"[DEBUG] BARK_KEY: {'已配置' if BARK_KEY else '未配置'} ({len(BARK_KEY)}字符)")
print(f"[DEBUG] NOTIFYME_UUID: {'已配置' if NOTIFYME_UUID else '未配置'} ({len(NOTIFYME_UUID)}字符)")
print(f"[DEBUG] ROCOM_API_KEY: {'已配置' if ROCOM_API_KEY else '未配置'} ({len(ROCOM_API_KEY)}字符)")
print(f"[DEBUG] IMGBB_KEY: {'已配置' if IMGBB_KEY else '未配置'} ({len(IMGBB_KEY)}字符)")
print(f"[DEBUG] GITHUB_TOKEN: {'已配置' if GITHUB_TOKEN else '未配置'} ({len(GITHUB_TOKEN)}字符)")

def gh_get_file(path):
    if not GITHUB_TOKEN:
        return None
    url  = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{path}"
    req  = urllib.request.Request(url, headers={"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github+json"})
    try:
        with urllib.request.urlopen(req) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"[DEBUG] gh_get_file 失败: {e}")
        return None

def load_subscribers():
    info = gh_get_file(SUBSCRIBERS_FILE)
    if not info:
        print("[INFO] 未找到订阅者文件，跳过群发")
        return []
    content = base64.b64decode(info["content"]).decode("utf-8")
    subs = json.loads(content)
    print(f"[INFO] 加载到 {len(subs)} 位订阅者")
    for s in subs:
        print(f"[DEBUG] 订阅者: platform={s.get('platform')}, key={s.get('key','')[:8]}...")
    return subs

def push_bark(bark_key, title, body, image_url):
    url = f"https://api.day.app/{bark_key}"
    payload = {"title": title, "body": body}
    if image_url:
        payload["icon"] = image_url
        payload["thumbnail"] = image_url
    try:
        resp = requests.get(url, params=payload, timeout=30)
        print(f"[DEBUG] Bark 响应: {resp.status_code} | key={bark_key[:8]}...")
        return resp.status_code == 200
    except Exception as e:
        print(f"[Bark 推送异常] {e}")
        return False

def push_notifyme(uuid, title, body, image_url):
    payload = {
        "data": {
            "title": title,
            "content": body,
            "picture": image_url or ""
        },
        "priority": "high",
        "ttl": 3600
    }
    try:
        resp = requests.post(
            f"https://api.notifymye.io/v2/notify/{uuid}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        print(f"[DEBUG] NotifyMe 响应: {resp.status_code}")
        return resp.status_code == 200
    except Exception as e:
        print(f"[NotifyMe 推送异常] {e}")
        return False

def push_to_subscriber(sub, title, body, image_url):
    plat = sub.get("platform", "").lower()
    key  = sub.get("key", "")
    if not key:
        return False
    if plat == "bark":
        return push_bark(key, title, body, image_url)
    elif plat == "notifyme":
        return push_notifyme(key, title, body, image_url)
    else:
        print(f"[WARN] 未知平台: {plat}")
        return False

async def fetch_goods():
    url = "https://db3.rocom.peerfun.cn/api/game/merchant/current"
    headers = {
        "ROCOM-API-KEY": ROCOM_API_KEY,
        "Accept": "application/json"
    }
    async with asyncio.Lock():
        resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 200 and data.get("retcode") != 0:
            raise Exception(f"API 返回错误: {data}")
        return data["data"]["goods"]

def upload_image_sync(image_bytes):
    import uuid
    ext   = "png"
    fname = f"roco_{uuid.uuid4().hex[:8]}.{ext}"
    url   = "https://api.imgbb.com/1/upload"
    files = {"image": (fname, image_bytes, f"image/{ext}")]
    data  = {"key": IMGBB_KEY}
    resp  = requests.post(url, files=files, data=data, timeout=30)
    resp.raise_for_status()
    resp_json = resp.json()
    if resp_json.get("success"):
        return resp_json["data"]["url"]
    raise Exception(f"图床返回失败: {resp_json}")

def render_and_upload(goods):
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        print(f"[ERROR] Pillow 导入失败: {e}")
        return None
    W, H = 750, 600
    bg = Image.new("RGB", (W, H), "#1e1e2e")
    draw = ImageDraw.Draw(bg)
    try:
        fnt = ImageFont.truetype("msyh.ttc", 36)
        fnt_sm = ImageFont.truetype("msyh.ttc", 28)
    except:
        fnt = ImageFont.load_default()
        fnt_sm = fnt

    draw.text((375, 55), "\u6d1b\u514b\u738b\u56fd \u00b7 \u8fdc\u884c\u5546\u4eba", fill="white", font=fnt, anchor="mm")
    y = 130
    COLS = 5
    CW, CH = 120, 120
    PAD   = 15
    grid_w = COLS * CW + (COLS - 1) * PAD
    start_x = (W - grid_w) // 2
    for i, g in enumerate(goods[:15]):
        row, col = divmod(i, COLS)
        cx = start_x + col * (CW + PAD)
        cy = y + row * (CH + PAD)
        try:
            from PIL import Image as PILImage
            import io, urllib.request as req
            img_data = req.urlopen(g["image"], timeout=10).read()
            img = PILImage.open(io.BytesIO(img_data)).resize((CW, CH), PILImage.LANCZOS)
            img_circle = img.copy()
            mask = PILImage.new("L", (CW, CH), 0)
            mask_draw = ImageDraw.Draw(mask)
            mask_draw.ellipse((0, 0, CW, CH), fill=255)
            img_circle.putalpha(mask)
            bg.paste(img_circle, (cx, cy), img_circle)
        except Exception as e:
            draw.ellipse([cx, cy, cx+CW, cy+CH], fill="#333")
        name = g.get("name", "?")
        draw.text((cx + CW//2, cy + CW + 14), name, fill="white", font=fnt_sm, anchor="mm")
    buf = io.BytesIO()
    bg.save(buf, format="PNG", optimize=True)
    return upload_image_sync(buf.getvalue())

def push_notifyme_direct(uuid, title, body, image_url):
    payload = {
        "data": {
            "title": title,
            "content": body,
            "picture": image_url or ""
        },
        "priority": "high",
        "ttl": 3600
    }
    try:
        resp = requests.post(
            f"https://api.notifymye.io/v2/notify/{uuid}",
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if resp.status_code == 200:
            print(f"[INFO] NotifyMe HTTP: 200")
            return True
        else:
            print(f"[WARN] NotifyMe HTTP: {resp.status_code} {resp.text[:100]}")
            return False
    except Exception as e:
        print(f"[WARN] NotifyMe 异常: {e}")
        return False

def push_bark_direct(bark_key, title, body, image_url):
    url = f"https://api.day.app/{bark_key}"
    payload = {"title": title, "body": body}
    if image_url:
        payload["icon"] = image_url
        payload["thumbnail"] = image_url
    try:
        resp = requests.get(url, params=payload, timeout=30)
        if resp.status_code == 200:
            print(f"[INFO] Bark 推送已发送 (key={bark_key[:8]}...)")
            return True
        else:
            print(f"[WARN] Bark HTTP: {resp.status_code}")
            return False
    except Exception as e:
        print(f"[WARN] Bark 异常: {e}")
        return False

def push_direct(title, body, image_url):
    ok = False
    if NOTIFYME_UUID:
        ok = push_notifyme_direct(NOTIFYME_UUID, title, body, image_url) or ok
    if BARK_KEY:
        ok = push_bark_direct(BARK_KEY, title, body, image_url) or ok
    return ok

async def main():
    try:
        print("[INFO] 正在查询洛克王国远行商人数据...")
        goods = await fetch_goods()
        print(f"[INFO] 获取到 {len(goods)} 个商品")

        if not goods:
            print("[INFO] 无商品，发送无商品通知")
            push_direct("\u8fdc\u884c\u5546\u4eba - \u5f53\u524d\u65e0\u5546\u54c1", "\u76ee\u524d\u8fdc\u884c\u5546\u4eba\u5904\u6682\u65e0\u5546\u54c1\u4e0a\u67b6\uff0c\u4e0b\u6b21\u66f4\u65b0\u8bf7\u67e5\u770b\u3002", None)

            subs = load_subscribers()
            if subs:
                for sub in subs:
                    push_to_subscriber(sub, "\u8fdc\u884c\u5546\u4eba - \u65e0\u5546\u54c1", "\u76ee\u524d\u6682\u65e0\u5546\u54c1", None)
            return

        goods_list = ", ".join([g.get("name", "?") for g in goods[:10]])
        print(f"[INFO] 商品: {goods_list}")

        title = f"\u8fdc\u884c\u5546\u4eba\u6765\u4e86\uff01\u5171 {len(goods)} \u4ef6\u5546\u54c1"
        push_body = goods_list
        img_url = render_and_upload(goods)
        if img_url:
            print(f"[INFO] 图片: {img_url}")
        else:
            print("[WARN] 图片上传失败，继续推送（无图）")

        push_direct(title, push_body, img_url)

        subs = load_subscribers()
        if subs:
            print(f"[INFO] 正在群发给 {len(subs)} 位订阅者...")
            ok_count = 0
            for sub in subs:
                key = sub.get("key", "")
                plat = sub.get("platform", "")
                if not key:
                    continue
                ok = push_to_subscriber(sub, title, push_body, img_url)
                if ok:
                    ok_count += 1
                    print(f"[INFO] 订阅者推送成功 ({plat}): {key[:6]}...")
                else:
                    print(f"[FAIL] 订阅者推送失败 ({plat}): {key[:6]}...")
            print(f"[INFO] 群发完成: {ok_count}/{len(subs)} 成功")
        else:
            print("[INFO] 无订阅者，跳过群发")

    except Exception as e:
        import traceback
        traceback.print_exc()
        push_direct("[WARN] \u76d1\u63a7\u5f02\u5e38", str(e), None)

asyncio.run(main())
