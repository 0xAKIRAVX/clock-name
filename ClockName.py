#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ClockName — ساعتِ زنده کنار اسم پروفایل تلگرام (نسخهٔ ۲)
==========================================================
سلف‌بات (userbot) که «اسم دوم» پروفایل تو را هر دقیقه، هم‌زمان با دقیقهٔ
گوشی، به ساعت زنده تبدیل می‌کند.

حالت‌ها:
    python ClockName.py            اجرای محلی (Termux / سیستم شخصی) — دائمی
    python ClockName.py --loop     حالت حلقه برای GitHub Actions — خروج خودکار
                                    بعد از LOOP_MINUTES دقیقه (ساعت می‌ماند)
    python ClockName.py --once     فقط یک بار ست کن و خارج شو (تست)
    python ClockName.py --restore  اسم دوم را به حالت اولیه برگردان
    python ClockName.py --selftest تست منطق بدون اتصال به تلگرام

🤖 بات مدیریت (اگر BOT_TOKEN داده شود): فرمان‌ها را همان‌جا می‌گیرد:
    /status /pause /resume /restore /format /tz /help

متغیرهای محیطی:
    BOT_TOKEN     توکن بات مدیریت (اختیاری — از Secret گیت‌هاب می‌آید)
    LOOP_MINUTES  طول عمر حلقه در دقیقه (۰ یا خالی = بی‌نهایت)
    GH_STATE=yes  state در ریپوی گیت‌هاب ذخیره/کامیت شود

⚠️ امنیت: فایل clockname_session.session مثل پسورد حساب توست؛ به هیچ‌کس نده!
"""
from __future__ import annotations

import asyncio
import json
import os
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
    "api_id": 0,
    "api_hash": "",
    "timezone": "Asia/Tehran",
    "format": "｜ {hhm}:{mmm}",
    "interval_minutes": 1,
    "max_daily_changes": 1500,
    "notify_saved_messages": False,
    "restore_on_exit": True,
    "owner_chat_id": 0,
}

CLOCK_EMOJIS = ["🕛", "🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚"]
# ارقام مونو‌اسپیس ریاضی — همان فونتِ اسم‌های فانسی (مثل 𝚝𝟷𝚙𝚜𝚢)
MONO_DIGITS = "𝟶𝟷𝟸𝟹𝟺𝟻𝟼𝟽𝟾𝟿"

BOT_HELP = (
    "🤖 مدیریت ClockName — فرمان‌ها:\n\n"
    "/status — وضعیت ساعت\n"
    "/pause — توقف موقت ساعت\n"
    "/resume — ادامهٔ ساعت\n"
    "/restore — برگرداندن اسم اصلی و توقف\n"
    "/format <متن> — تغییر فرمت ساعت\n"
    "   نمونه: /format ｜ {hhm}:{mmm}\n"
    "   متغیرها: {hhm} {mmm} فونت فانسی · {hh} {mm} عدد معمولی · {emoji} · {H12}\n"
    "/tz <منطقه> — تغییر منطقهٔ زمانی\n"
    "   نمونه: /tz Asia/Tehran\n\n"
    "💡 ساعت هر دقیقه، هم‌زمان با دقیقهٔ گوشی آپدیت می‌شود."
)


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


def to_mono(num: str) -> str:
    return "".join(MONO_DIGITS[int(c)] for c in num)


def hour_emoji(h: int) -> str:
    return CLOCK_EMOJIS[h % 12]


def render(dt: datetime, fmt: str) -> str:
    out = fmt
    out = out.replace("{emoji}", hour_emoji(dt.hour))
    out = out.replace("{hhm}", to_mono(f"{dt.hour:02d}"))
    out = out.replace("{mmm}", to_mono(f"{dt.minute:02d}"))
    out = out.replace("{hh}", f"{dt.hour:02d}")
    out = out.replace("{h}", str(dt.hour))
    out = out.replace("{mm}", f"{dt.minute:02d}")
    out = out.replace("{H12}", str(((dt.hour + 11) % 12) + 1))
    return out.strip()[:64]  # سقف تلگرام برای اسم


def looks_like_clock(text: str) -> bool:
    """تشخیص اینکه اسم دوم الان ساعت است."""
    if not text:
        return False
    has_emoji = any(e in text for e in CLOCK_EMOJIS)
    has_pipe = ("|" in text) or ("｜" in text)
    has_time = (
        re.search(r"\d{1,2}\s*[:：]\s*\d{2}", text) is not None
        or re.search(r"[𝟶-𝟿]{1,2}\s*[:：]\s*[𝟶-𝟿]{2}", text) is not None
    )
    return has_time and (has_emoji or has_pipe)


def make_now(tz_name: str) -> datetime:
    if tz_name:
        try:
            from zoneinfo import ZoneInfo
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            pass
    return datetime.now()


def try_wakelock() -> bool:
    try:
        r = subprocess.run(["termux-wake-lock"], capture_output=True, timeout=5)
        if r.returncode == 0:
            print("🔒 WakeLock فعال شد (Termux).")
            return True
    except Exception:
        pass
    return False


# ------------------------- خودتست (بدون تلگرام) -------------------------

def selftest() -> None:
    cfg = load_config()
    print("⏱ ClockName v2 — خودتست منطق (بدون اتصال به تلگرام)")
    print(f"   فرمت: {cfg['format']} | هر {cfg['interval_minutes']} دقیقه | سقف روزانه: {cfg['max_daily_changes']}")
    for h, m in [(9, 5), (12, 34), (0, 1), (19, 59), (23, 0)]:
        print(f"   ساعت {h:02d}:{m:02d} → {render(datetime(2026, 10, 7, h, m), cfg['format'])}")
    assert looks_like_clock(render(datetime(2026, 1, 1, 9, 5), cfg["format"])), "تشخیص ساعت خراب است!"
    assert not looks_like_clock("علی"), "اسم عادی را ساعت تشخیص داد!"
    assert len(render(datetime(2026, 1, 1, 23, 59), cfg["format"])) <= 64
    print("✅ خودتست OK — منطق سالم است.")


# ------------------------- حالت --once / --restore -------------------------

async def run(mode_once: bool = False, mode_restore: bool = False) -> None:
    cfg = load_config()

    if not cfg.get("api_id") or not cfg.get("api_hash"):
        print("🔑 api_id و api_hash لازم است (my.telegram.org یا فایل کانفیگ آماده).")
        try:
            cfg["api_id"] = int(input("api_id: ").strip())
            cfg["api_hash"] = input("api_hash: ").strip()
            save_json(CONFIG_PATH, cfg)
        except ValueError:
            sys.exit("❌ api_id باید عدد باشد.")

    from telethon import TelegramClient, errors
    from telethon.tl.functions.account import UpdateProfileRequest

    client = TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])
    print("📡 در حال اتصال به تلگرام…")
    await client.start()

    me = await client.get_me()
    state = load_json(STATE_PATH, {})

    original = me.last_name or ""
    if looks_like_clock(original):
        if state.get("original_last_name") is not None:
            original = state["original_last_name"] or ""
        else:
            original = ""

    async def set_last_name(value: str) -> None:
        await client(UpdateProfileRequest(last_name=value))

    async def notify(text: str) -> None:
        if cfg.get("notify_saved_messages", False):
            try:
                await client.send_message("me", text)
            except Exception:
                pass

    if mode_restore:
        target = state.get("original_last_name")
        if target is None:
            target = original
        await set_last_name(target or "")
        print("↩️ اسم دوم به حالت اولیه برگشت:", repr(target))
        await notify("↩️ ClockName: اسم دومت به حالت اولیه برگردانده شد.")
        await client.disconnect()
        return

    now0 = make_now(cfg.get("timezone") or "")
    today0 = now0.strftime("%Y-%m-%d")
    state["date"] = today0
    state["count"] = state.get("count", 0) if state.get("date") == today0 else 0
    state["original_last_name"] = original
    save_json(STATE_PATH, state)

    if mode_once:
        new_last = render(now0, cfg["format"])
        await set_last_name(new_last)
        print("✅ یک بار ست شد:", new_last)
        await notify(f"✅ ClockName تست: «{new_last}»")
        await client.disconnect()
        return

    # اجرای محلی دائمی (Termux) — منطق سادهٔ هر دقیقه
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, stop.set)
        except (NotImplementedError, RuntimeError):
            pass
    try_wakelock()
    print(f"👤 {me.first_name} — اجرای محلی دائمی. Ctrl+C برای توقف.")
    current_last = me.last_name or ""
    fmt = cfg["format"]
    interval = max(1, int(cfg.get("interval_minutes", 1)))
    cap = max(1, int(cfg.get("max_daily_changes", 1500)))
    while not stop.is_set():
        now = make_now(cfg.get("timezone") or "")
        today = now.strftime("%Y-%m-%d")
        if state.get("date") != today:
            state["date"] = today
            state["count"] = 0
            save_json(STATE_PATH, state)
        target = now.replace(second=0, microsecond=0) + timedelta(minutes=interval)
        lead = random.uniform(0.8, 2.5)
        wait_s = (target - now).total_seconds() - lead
        try:
            await asyncio.wait_for(stop.wait(), timeout=max(0.05, wait_s))
            break
        except asyncio.TimeoutError:
            pass
        new_last = render(target, fmt)
        if new_last == current_last:
            continue
        try:
            await set_last_name(new_last)
            current_last = new_last
            state["count"] = int(state.get("count", 0)) + 1
            save_json(STATE_PATH, state)
            print(f"✅ {target.strftime('%H:%M')} → {new_last} ({state['count']}/{cap})")
        except errors.FloodWaitError as e:
            wait = int(getattr(e, "seconds", 60)) + 15
            print(f"⚠️ FLOOD_WAIT {e.seconds}s — {wait}s استراحت")
            await asyncio.sleep(wait)
        except Exception as e:
            print("❌", e)
            await asyncio.sleep(30)

    if cfg.get("restore_on_exit", True):
        try:
            await set_last_name(original or "")
            print("↩️ اسم به حالت اولیه برگشت:", repr(original))
        except Exception as e:
            print("⚠️ برگرداندن نشد:", e, "→ python ClockName.py --restore")
    await client.disconnect()


# ------------------------- حالت --loop (GitHub Actions / بات) -------------------------

async def run_loop() -> None:
    cfg = load_config()
    from telethon import TelegramClient, errors
    from telethon.tl.functions.account import UpdateProfileRequest
    import requests as rq

    bot_token = os.environ.get("BOT_TOKEN", "").strip()
    loop_minutes = int(os.environ.get("LOOP_MINUTES", "0") or 0)  # ۰ = بی‌نهایت
    in_gh = os.environ.get("GH_STATE") == "yes"
    allowed_chat = int(cfg.get("owner_chat_id", 0) or 0)

    client = TelegramClient(str(SESSION_PATH), int(cfg["api_id"]), cfg["api_hash"])
    print("📡 اتصال…", flush=True)
    await client.connect()
    if not await client.is_user_authorized():
        sys.exit("❌ session معتبر نیست — TELEGRAM_SESSION را بررسی کن")
    me = await client.get_me()

    state = load_json(STATE_PATH, {})
    current_last = me.last_name or ""
    if looks_like_clock(current_last):
        if state.get("original_last_name") is None:
            state["original_last_name"] = ""
    else:
        state["original_last_name"] = current_last
    state.setdefault("paused", False)
    state.setdefault("count", 0)
    save_json(STATE_PATH, state)

    stop = asyncio.Event()
    got_signal = {"v": False}

    def _sig():
        got_signal["v"] = True
        stop.set()

    loop = asyncio.get_running_loop()
    for s in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(s, _sig)
        except (NotImplementedError, RuntimeError):
            pass

    BAPI = f"https://api.telegram.org/bot{bot_token}" if bot_token else None

    def bot_send_sync(text: str):
        try:
            rq.post(BAPI + "/sendMessage", json={"chat_id": allowed_chat, "text": text},
                    timeout=(10, 30))
        except Exception:
            pass

    async def bot_send(text: str):
        if BAPI and allowed_chat:
            await asyncio.to_thread(bot_send_sync, text)

    def commit_state(reason: str) -> None:
        if not in_gh:
            return
        try:
            save_json(STATE_PATH, state)
            subprocess.run(["git", "add", STATE_PATH.name], cwd=BASE, timeout=30, check=False)
            subprocess.run(["git", "commit", "-m", f"state: {reason}"], cwd=BASE,
                           timeout=30, check=False)
            subprocess.run(["git", "push"], cwd=BASE, timeout=120, check=False)
        except Exception as e:
            print("state commit failed:", e)

    async def set_last(value: str) -> None:
        await client(UpdateProfileRequest(last_name=value))

    async def handle(text: str) -> None:
        t = text.strip()
        low = t.split("@")[0].lower()  # پشتیبانی از /cmd@botname

        if low in ("/start", "/help", "help"):
            await bot_send(BOT_HELP)

        elif low == "/status":
            tz_now = state.get("tz") or cfg.get("timezone") or ""
            conn = "برقرار ✅" if client.is_connected() else "قطع ❌ (بازاتصال خودکار…)"
            txt = (
                "⏰ وضعیت ClockName\n"
                f"وضعیت: {'متوقف ⏸' if state.get('paused') else 'فعال ✅'}\n"
                f"ساعت روی اسم: {current_last or '—'}\n"
                f"آخرین تیک: {state.get('last_set_at') or '—'}\n"
                f"فرمت: {state.get('format') or cfg.get('format')}\n"
                f"منطقهٔ زمانی: {tz_now}\n"
                f"تغییرات امروز: {state.get('count', 0)}\n"
                f"اتصال تلگرام: {conn}\n"
                f"محل اجرا: {'GitHub Actions' if in_gh else 'سیستم شخصی/Termux'}"
            )
            await bot_send(txt)

        elif low == "/pause":
            state["paused"] = True
            commit_state("pause")
            await bot_send("⏸ ساعت متوقف شد (اسم همان می‌ماند، تیک نمی‌زند).\nبرای ادامه: /resume")

        elif low == "/resume":
            state["paused"] = False
            commit_state("resume")
            await bot_send("▶️ ساعت ادامه می‌یابد — تیک بعدی در دقیقهٔ بعد.")

        elif low == "/restore":
            try:
                await set_last(state.get("original_last_name") or "")
                state["paused"] = True
                state["last_set_at"] = None
                commit_state("restore")
                await bot_send("↩️ اسم به حالت اولیه برگشت و ساعت متوقف شد.\nبرای روشن‌کردن دوباره: /resume")
            except Exception as e:
                await bot_send(f"❌ خطا در برگرداندن اسم: {e}")

        elif low.startswith("/format"):
            arg = t[len("/format"):].strip()
            if not arg:
                await bot_send(
                    "فرمت فعلی:\n" + str(state.get("format") or cfg.get("format")) +
                    "\n\nنمونه: /format ｜ {hhm}:{mmm}\nمتغیرها: {hhm} {mmm} {hh} {mm} {emoji} {H12}"
                )
                return
            preview = render(make_now(state.get("tz") or cfg.get("timezone") or ""), arg)
            if len(preview) > 64 or len(arg) > 80:
                await bot_send("❌ فرمت خیلی طولانی است (سقف اسم تلگرام ۶۴ کاراکتر).")
                return
            state["format"] = arg
            commit_state("format")
            await bot_send(f"✅ فرمت جدید ذخیره شد.\nپیش‌نمایش همین الان: {preview}")

        elif low.startswith("/tz"):
            arg = t[len("/tz"):].strip()
            if not arg:
                await bot_send("منطقهٔ زمانی فعلی: " + str(state.get("tz") or cfg.get("timezone")) +
                               "\nنمونه: /tz Asia/Tehran")
                return
            try:
                from zoneinfo import ZoneInfo
                ZoneInfo(arg)
            except Exception:
                await bot_send("❌ منطقهٔ زمانی شناخته نشد. نمونه‌ها: Asia/Tehran, Europe/London, UTC")
                return
            state["tz"] = arg
            commit_state("tz")
            await bot_send(f"✅ منطقهٔ زمانی جدید: {arg}")

        else:
            await bot_send("فرمان ناشناخته — /help را بفرست.")

    async def bot_task():
        if not BAPI:
            print("ℹ️ BOT_TOKEN نیست — بدون بات مدیریت.", flush=True)
            return
        if not allowed_chat:
            print("⚠️ owner_chat_id در کانفیگ نیست — بات غیرفعال.", flush=True)
            return
        print("🤖 بات مدیریت فعال — منتظر فرمان…", flush=True)
        offset = None
        while not stop.is_set():
            params = {"timeout": 45, "allowed_updates": json.dumps(["message"])}
            if offset is not None:
                params["offset"] = offset
            try:
                def call():
                    return rq.get(BAPI + "/getUpdates", params=params, timeout=(10, 55))
                r = await asyncio.to_thread(call)
                d = r.json()
            except Exception:
                await asyncio.sleep(3)
                continue
            if not d.get("ok"):
                await asyncio.sleep(3)
                continue
            for u in d.get("result", []):
                offset = u["update_id"] + 1
                m = u.get("message") or {}
                chat_id = (m.get("chat") or {}).get("id")
                text = (m.get("text") or "").strip()
                if not text:
                    continue
                if allowed_chat and chat_id != allowed_chat:
                    continue  # فقط صاحب اکانت
                try:
                    await handle(text)
                except Exception as e:
                    print("cmd error:", e, flush=True)

    async def clock_task():
        nonlocal current_last  # ← بدون این، انتساب پایین‌تر متغیر را محلی می‌کند و خواندنش UnboundLocalError می‌دهد
        interval = max(1, int(cfg.get("interval_minutes", 1)))
        cap = max(1, int(cfg.get("max_daily_changes", 1500)))
        flood_until = None
        err_streak = 0
        while not stop.is_set():
            tz = state.get("tz") or cfg.get("timezone") or ""
            now = make_now(tz)
            today = now.strftime("%Y-%m-%d")
            if state.get("date") != today:
                state["date"] = today
                state["count"] = 0
                save_json(STATE_PATH, state)
            target = now.replace(second=0, microsecond=0) + timedelta(minutes=interval)
            lead = random.uniform(0.8, 2.5)
            wait_s = (target - now).total_seconds() - lead
            try:
                await asyncio.wait_for(stop.wait(), timeout=max(0.05, wait_s))
                break
            except asyncio.TimeoutError:
                pass
            if stop.is_set():
                break
            if flood_until is not None:
                remaining = (flood_until - make_now(tz)).total_seconds()
                if remaining > 0:
                    await asyncio.sleep(min(remaining, 30))
                    continue
                flood_until = None
            if state.get("paused"):
                continue
            if int(state.get("count", 0)) >= cap:
                continue
            fmt = state.get("format") or cfg.get("format", "｜ {hhm}:{mmm}")
            new_last = render(target, fmt)
            if new_last == current_last:
                continue
            try:
                await set_last(new_last)
                current_last = new_last
                err_streak = 0
                state["count"] = int(state.get("count", 0)) + 1
                state["last_set"] = new_last
                state["last_set_at"] = target.strftime("%H:%M")
                save_json(STATE_PATH, state)
                print(f"✅ {target.strftime('%H:%M')} → {new_last} ({state['count']}/{cap})", flush=True)
            except errors.FloodWaitError as e:
                wait = int(getattr(e, "seconds", 60)) + 15
                flood_until = make_now(tz) + timedelta(seconds=wait)
                print(f"⚠️ FLOOD_WAIT {e.seconds}s — {wait}s استراحت", flush=True)
                await bot_send(f"⚠️ تلگرام FLOOD_WAIT {e.seconds}s داد؛ ساعت موقتاً می‌ایستد و خودکار ادامه می‌دهد.")
            except errors.AuthKeyDuplicatedError:
                await bot_send("🚨 session تلگرام باطل شد (استفادهٔ همزمان از دو جا). ساعت ایستاد — به سازنده پیام بده.")
                print("🚨 AUTH_KEY_DUPLICATED — خروج", flush=True)
                stop.set()
                break
            except Exception as e:
                err_streak += 1
                print("❌", type(e).__name__, e, flush=True)
                if err_streak == 3:
                    await bot_send(f"⚠️ تیک ساعت ۳ بار پشت‌سرهم خطا خورد ({type(e).__name__}). تلاش ادامه دارد — /status برای جزئیات.")
                try:
                    if not client.is_connected():
                        await client.disconnect()
                        await asyncio.sleep(2)
                        await client.connect()
                        print("🔄 بازاتصال انجام شد", flush=True)
                        await bot_send("🔄 اتصال تلگرام قطع شده بود؛ خودکار وصل شدم و ساعت ادامه می‌یابد.")
                except Exception as re:
                    print("reconnect failed:", re, flush=True)
                await asyncio.sleep(20)

    async def deadline_task():
        if loop_minutes > 0:
            await asyncio.sleep(max(60, loop_minutes * 60 - 20))
            print("⏳ زمان این اجرا تمام شد — خروج تمیز (ساعت روی پروفایل می‌ماند)", flush=True)
            stop.set()

    if not in_gh:
        try_wakelock()
    print(f"👤 {me.first_name} — حالت حلقهٔ ۲۴/۷ | تیک هر {cfg.get('interval_minutes')} دقیقه "
          f"| عمر: {loop_minutes or '∞'} دقیقه | بات: {'فعال' if BAPI else 'خاموش'}", flush=True)
    await bot_send("🤖 ClockName فعال شد — /help برای فرمان‌ها، /status برای وضعیت.")

    tasks = [asyncio.create_task(clock_task()), asyncio.create_task(bot_task())]
    if loop_minutes > 0:
        tasks.append(asyncio.create_task(deadline_task()))
    await stop.wait()
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)

    if got_signal["v"] and cfg.get("restore_on_exit", False):
        try:
            await set_last(state.get("original_last_name") or "")
            print("↩️ اسم به حالت اولیه برگشت (خروج با سیگنال).")
        except Exception as e:
            print("⚠️ برگرداندن نشد:", e)
    else:
        commit_state("loop-exit")
    await client.disconnect()
    print("👋 خروج.", flush=True)


# ------------------------- ورودی -------------------------

def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "--selftest":
        selftest()
    elif arg == "--restore":
        asyncio.run(run(mode_restore=True))
    elif arg == "--once":
        asyncio.run(run(mode_once=True))
    elif arg == "--loop":
        asyncio.run(run_loop())
    elif arg in ("", "-h", "--help"):
        print(__doc__)
        if not arg:
            try:
                asyncio.run(run())
            except KeyboardInterrupt:
                print("\n👋 خروج (اگر اسم برنگشت: python ClockName.py --restore)")
    else:
        print("آرگومان ناشناخته. گزینه‌ها: --loop | --once | --restore | --selftest")


if __name__ == "__main__":
    main()
