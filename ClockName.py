#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClockName — ساعتِ زنده کنار اسم پروفایل تلگرام
================================================
سلف‌بات (userbot) که «اسم دوم» پروفایل تو را هر دقیقه به ساعت زنده تبدیل
می‌کند؛ مثلاً اسم اولت «علی» دست‌نخورده می‌ماند و کنارش «🕖 19:05» می‌نشیند.

اجرا روی گوشی خودت با Termux (بدون سرور) یا روی هر کامپیوتر با پایتون ۳.

اولین اجرا:
    python ClockName.py
    ← اول api_id و api_hash را می‌پرسد (از my.telegram.org فقط یک بار)،
      بعد شماره گوشی و کد لاگین تلگرام. فقط بار اول لاگین می‌شود.

گزینه‌ها:
    --once      فقط یک بار ساعت را ست کن و خارج شو (تست)
    --restore   اسم دوم را به حالت اولیه برگردان و خارج شو
    --selftest  تست منطق بدون اتصال به تلگرام

توقف: Ctrl+C  →  اسم دوم خودکار به حالت اولیه برمی‌گردد.

⚠️ امنیت: فایل clockname_session.session مثل پسورد حساب توست؛ به هیچ‌کس نده!
⚠️ صادقانه: هیچ سلف‌باتی ۱۰۰٪ ضد بن نیست؛ این اسکریپت امن‌ترین روش رایج است:
   اکانت خودت، آپدیت نرم با تاخیر تصادفی، مدیریت خودکار FloodWait،
   سقف تغییر روزانه، و توقف تمیز.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "clockname_config.json"
STATE_PATH = BASE / "clockname_state.json"
SESSION_PATH = BASE / "clockname_session"

DEFAULTS = {
    "api_id": 0,                # از my.telegram.org
    "api_hash": "",             # از my.telegram.org
    "timezone": "Asia/Tehran",  # تایم‌زون ساعت نام
    "format": "{emoji} {hh}:{mm}",   # {emoji} {h} {hh} {mm} {H12} پشتیبانی می‌شوند
    "interval_minutes": 1,      # هر چند دقیقه آپدیت شود (1 = هر دقیقه)
    "max_daily_changes": 800,   # سقف ایمن تغییر در روز
    "notify_saved_messages": True,
    "restore_on_exit": True,
}

CLOCK_EMOJIS = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]
# ارقام مونو‌اسپیس ریاضی — همان فونتِ اسم‌های فانسی (مثل 𝚝𝟷𝚙𝚜𝚢)
MONO_DIGITS = "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"


def to_mono(num: str) -> str:
    return "".join(MONO_DIGITS[int(c)] for c in num)


# ------------------------- ابزارهای کوچک -------------------------

def load_json(path: Path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: Path, data) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_config() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update(load_json(CONFIG_PATH, {}))
    return cfg


def hour_emoji(h: int) -> str:
    return CLOCK_EMOJIS[h % 12]


def render(dt: datetime, fmt: str) -> str:
    out = fmt
    out = out.replace("{emoji}", hour_emoji(dt.hour))
    out = out.replace("{hh}", f"{dt.hour:02d}")
    out = out.replace("{h}", str(dt.hour))
    out = out.replace("{mm}", f"{dt.minute:02d}")
    out = out.replace("{H12}", str(((dt.hour + 11) % 12) + 1))
    out = out.replace("{hhm}", to_mono(f"{dt.hour:02d}"))   # 𝟶𝟸 — فونت فانسی
    out = out.replace("{mmm}", to_mono(f"{dt.minute:02d}"))  # 𝟺𝟻 — فونت فانسی
    return out.strip()[:64]  # سقف تلگرام برای اسم


def looks_like_clock(text: str) -> bool:
    """تشخیص اینکه اسم دوم الان ساعت است (مثلاً بعد از خاموشی ناگهانی)."""
    if not text:
        return False
    has_emoji = any(e in text for e in CLOCK_EMOJIS)
    has_pipe = ("|" in text) or ("｜" in text)
    has_time = (
        re.search(r"\d{1,2}\s*[:：]\s*\d{2}", text) is not None
        or re.search(r"[𝟶-𝟿]{1,2}\s*[:：]\s*[𝟶-𝟿]{2}", text) is not None
    )
    return has_time and (has_emoji or has_pipe)


def make_now(cfg: dict) -> datetime:
    tz_name = cfg.get("timezone") or ""
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass  # tzdata نبود → از ساعت خود گوشی استفاده می‌کنیم
    return datetime.now()


def try_wakelock() -> bool:
    """روی Termux اجازه می‌دهد اندروید برنامه را نکشد؛ جای دیگر بی‌اثر است."""
    try:
        r = subprocess.run(["termux-wake-lock"], capture_output=True, timeout=5)
        if r.returncode == 0:
            print("🔒 WakeLock فعال شد (Termux زنده می‌ماند).")
            return True
    except Exception:
        pass
    return False


# ------------------------- خودتست (بدون تلگرام) -------------------------

def selftest() -> None:
    cfg = load_config()
    print("⏱ ClockName — خودتست منطق (بدون اتصال به تلگرام)")
    print(f"   فرمت: {cfg['format']} | هر {cfg['interval_minutes']} دقیقه | سقف روزانه: {cfg['max_daily_changes']}")
    samples = [(9, 5), (12, 34), (0, 1), (19, 59), (23, 0)]
    for h, m in samples:
        print(f"   ساعت {h:02d}:{m:02d} → {render(datetime(2026, 10, 7, h, m), cfg['format'])}")
    assert looks_like_clock(render(datetime(2026, 1, 1, 9, 5), cfg["format"])), "تشخیص ساعت خراب است!"
    assert not looks_like_clock("علی"), "اسم عادی را ساعت تشخیص داد!"
    assert len(render(datetime(2026, 1, 1, 23, 59), cfg["format"])) <= 64
    print("✅ خودتست OK — منطق سالم است.")


# ------------------------- اجرای اصلی -------------------------

async def run(mode_once: bool = False, mode_restore: bool = False) -> None:
    cfg = load_config()

    # --- api_id / api_hash فقط بار اول پرسیده می‌شود ---
    if not cfg.get("api_id") or not cfg.get("api_hash"):
        print("🔑 برای بار اول به api_id و api_hash نیاز داریم (از my.telegram.org — فقط یک بار).")
        print("   my.telegram.org ← شماره و کد ← API development tools ← Create new app")
        try:
            cfg["api_id"] = int(input("api_id: ").strip())
            cfg["api_hash"] = input("api_hash: ").strip()
        except ValueError:
            sys.exit("❌ api_id باید عدد باشد. دوباره اجرا کن.")
        if not cfg["api_hash"]:
            sys.exit("❌ api_hash خالی است. دوباره اجرا کن.")
        save_json(CONFIG_PATH, cfg)
        print(f"✅ ذخیره شد در {CONFIG_PATH.name}\n")

    # telethon فقط همین‌جا import می‌شود (خودتست بدون آن کار می‌کند)
    from telethon import TelegramClient, errors
    from telethon.tl.functions.account import UpdateProfileRequest

    client = TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])
    print("📡 در حال اتصال به تلگرام…")
    print("   (پرامپت‌های انگلیسی: phone = شماره‌ات با +98… | code = کدی که تلگرام فرستاد")
    print("    | password = پسورد دو مرحله‌ای‌ات اگر فعال کرده باشی)")
    await client.start()

    me = await client.get_me()
    state = load_json(STATE_PATH, {})

    # اگر اسم الان ساعت است (خاموشی ناگهانی قبلی)، اسم اصلی را از حافظه می‌خوانیم
    original = me.last_name or ""
    if looks_like_clock(original):
        if state.get("original_last_name") is not None:
            original = state["original_last_name"] or ""
        else:
            original = ""  # متن ساعت بدون state → اسم اصلی واقعاً خالی بوده

    async def set_last_name(value: str) -> None:
        await client(UpdateProfileRequest(last_name=value))

    async def notify(text: str) -> None:
        if cfg.get("notify_saved_messages", True):
            try:
                await client.send_message("me", text)
            except Exception:
                pass

    # --- حالت --restore: فقط برگرداندن اسم ---
    if mode_restore:
        target = state.get("original_last_name")
        if target is None:
            target = original
        await set_last_name(target or "")
        print("↩️ اسم دوم به حالت اولیه برگشت:", repr(target))
        await notify("↩️ ClockName: اسم دومت به حالت اولیه برگردانده شد.")
        await client.disconnect()
        return

    now0 = make_now(cfg)
    today0 = now0.strftime("%Y-%m-%d")
    state["date"] = today0
    state["count"] = state.get("count", 0) if state.get("date") == today0 else 0
    if state.get("count") is None:
        state["count"] = 0
    state["original_last_name"] = original
    save_json(STATE_PATH, state)

    # --- حالت --once: یک آپدیت و خروج (بدون برگرداندن) ---
    if mode_once:
        new_last = render(now0, cfg["format"])
        await set_last_name(new_last)
        print("✅ یک بار ست شد:", new_last)
        await notify(f"✅ ClockName تست: اسم دوم الان «{new_last}» است.")
        await client.disconnect()
        return

    # --- سیگنال‌های توقف (Ctrl+C) ---
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    try_wakelock()

    fmt = cfg.get("format", DEFAULTS["format"])
    interval = max(1, int(cfg.get("interval_minutes", 1)))
    cap = max(1, int(cfg.get("max_daily_changes", 800)))

    print(f"👤 سلام {me.first_name}! اسم اولت دست نمی‌خورد؛ اسم دوم از این به بعد ساعت است ⏰")
    print(f"   تایم‌زون: {cfg.get('timezone')} | هر {interval} دقیقه | سقف روزانه: {cap} تغییر")
    await notify(
        f"⏰ ClockName روشن شد — ساعت هر {interval} دقیقه کنار اسمت آپدیت می‌شود.\n"
        "توقف: Ctrl+C (اسم خودکار برمی‌گردد) یا هر وقت خواستی: python ClockName.py --restore"
    )

    current_last = me.last_name or ""
    flood_until = None
    last_flood_notify = 0.0

    while not stop.is_set():
        now = make_now(cfg)
        today = now.strftime("%Y-%m-%d")
        if state.get("date") != today:  # نیمه‌شب شد → شمارنده صفر
            state["date"] = today
            state["count"] = 0
            save_json(STATE_PATH, state)

        # ساعتِ دقیقهٔ بعدی را کمی «قبل از» تیک زدن ست می‌کنیم تا درست روی دقیقه بچرخد
        target = now.replace(second=0, microsecond=0) + timedelta(minutes=interval)
        lead = random.uniform(0.8, 2.5)  # تاخیر تصادفی = رفتار انسانی‌تر
        wait_s = (target - now).total_seconds() - lead
        if wait_s < 0.05:
            wait_s = 0.05
        try:
            await asyncio.wait_for(stop.wait(), timeout=wait_s)
            break  # سیگنال توقف آمد
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break

        # اگر تلگرام قبلاً FloodWait داده، تا پایانش فقط صبر می‌کنیم
        if flood_until is not None:
            remaining = (flood_until - make_now(cfg)).total_seconds()
            if remaining > 0:
                print(f"⏳ استراحت FloodWait — {int(remaining)}s دیگر…")
                continue
            flood_until = None

        # سقف روزانه ایمنی
        if int(state.get("count", 0)) >= cap:
            print(f"🛑 سقف روزانه ({cap}) پر شد؛ تا نیمه‌شب می‌خوابیم…")
            midnight = (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(5.0, (midnight - now).total_seconds()))
                break
            except asyncio.TimeoutError:
                state["count"] = 0
                save_json(STATE_PATH, state)
                continue

        new_last = render(target, fmt)
        if new_last == current_last:
            continue
        try:
            await set_last_name(new_last)
            current_last = new_last
            state["count"] = int(state.get("count", 0)) + 1
            save_json(STATE_PATH, state)
            print(f"✅ {target.strftime('%H:%M')} → {new_last}   (امروز: {state['count']}/{cap})")
        except errors.FloodWaitError as e:
            wait = int(getattr(e, "seconds", 60)) + 15
            flood_until = make_now(cfg) + timedelta(seconds=wait)
            print(f"⚠️ تلگرام FLOOD_WAIT {e.seconds}s گفت — {wait}s استراحت می‌کنیم (طبیعی است؛ نبند!)")
            if time.time() - last_flood_notify > 1800:  # حداکثر هر نیم‌ساعت یک پیام
                last_flood_notify = time.time()
                await notify(
                    f"⚠️ تلگرام FLOOD_WAIT {e.seconds}s داد؛ ساعت موقتاً ایستاده و خودکار ادامه می‌دهد.\n"
                    "اگر زیاد تکرار شد، در clockname_config.json مقدار interval_minutes را 2 یا 3 کن."
                )
        except Exception as e:
            print("❌ خطا در آپدیت:", e, "— ۳۰ ثانیه دیگر دوباره…")
            await asyncio.sleep(30)

    # --- خروج تمیز: اسم را به حالت اولیه برگردان ---
    if cfg.get("restore_on_exit", True):
        try:
            await set_last_name(original or "")
            print("↩️ اسم دوم به حالت اولیه برگشت:", repr(original))
            await notify("⏰ ClockName خاموش شد و اسم دومت به حالت اولیه برگشت.")
        except Exception as e:
            print("⚠️ برگرداندن اسم نشد (شبکه؟):", e, "→ بعداً اجرا کن: python ClockName.py --restore")
    else:
        await notify("⏰ ClockName خاموش شد (اسم ساعت همان‌طور ماند).")
    await client.disconnect()


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        selftest()
    elif arg == "--restore":
        asyncio.run(run(mode_restore=True))
    elif arg == "--once":
        asyncio.run(run(mode_once=True))
    elif arg in ("", "-h", "--help"):
        print(__doc__)
        if not arg:
            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                print("\n👋 خروج (اگر اسم برنگشت: python ClockName.py --restore)")
    else:
        print("آرگومان ناشناخته. گزینه‌ها: --once | --restore | --selftest")


if __name__ == "__main__":
    main()
