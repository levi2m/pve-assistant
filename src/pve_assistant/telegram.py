"""Telegram bridge: long polling straight from the agent (no n8n hop). Only the owner's chat is answered."""
import asyncio
import json
import os
import time
import urllib.error
import urllib.request

from .config import STATE_DIR

STATE = os.path.join(STATE_DIR, "telegram-offset")
STALE_S = 600           # messages older than this (sent while the service was down) are ignored


def plain(text):
    """Telegram is sent as plain text: drop the Markdown the model likes to add."""
    return text.replace("**", "").replace("__", "").replace("`", "").replace("### ", "").replace("## ", "").replace("# ", "")


def chunks(text, size=3800):
    while text:
        cut = text.rfind("\n", 0, size) if len(text) > size else len(text)
        cut = cut if cut > 0 else min(size, len(text))
        yield text[:cut]
        text = text[cut:].lstrip("\n")


class Telegram:
    def __init__(self, token, chat_id, handler, log, on_callback=None):
        self.token, self.chat_id, self.handler, self.log, self.on_callback = token, str(chat_id), handler, log, on_callback

    def _call(self, method, params=None, timeout=60):
        req = urllib.request.Request(f"https://api.telegram.org/bot{self.token}/{method}", json.dumps(params or {}).encode(), {"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.load(r)

    async def api(self, method, params=None, timeout=60):
        return await asyncio.to_thread(self._call, method, params, timeout)

    async def send(self, text, buttons=None, reply_to=None):
        """Send plain text (split if long). buttons = [[(label, callback_data), ...], ...]. Returns the id of the last message sent."""
        parts = list(chunks(plain(text) or "(empty answer)"))
        mid = None
        for i, part in enumerate(parts):
            params = {"chat_id": self.chat_id, "text": part, "disable_web_page_preview": True}
            if reply_to:
                params["reply_parameters"] = {"message_id": reply_to, "allow_sending_without_reply": True}
            if buttons and i == len(parts) - 1:
                params["reply_markup"] = {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in buttons]}
            mid = (await self.api("sendMessage", params))["result"]["message_id"]
        return mid

    async def clear_buttons(self, message_id):
        await self.api("editMessageReplyMarkup", {"chat_id": self.chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": []}})

    async def _handle_callback(self, update):
        cb = update["callback_query"]
        chat = str(((cb.get("message") or {}).get("chat") or {}).get("id"))
        toast = ""
        if chat == self.chat_id and self.on_callback:
            try:
                toast = await self.on_callback(cb.get("data", ""), cb["message"]["message_id"])
            except Exception as e:  # noqa: BLE001
                self.log(f"telegram: callback error {type(e).__name__}: {e}")
                toast = "Something went wrong."
        else:
            self.log(f"telegram: ignored button press from chat {chat}")
        try:
            await self.api("answerCallbackQuery", {"callback_query_id": cb["id"], "text": (toast or "")[:180]})
        except Exception:  # noqa: BLE001
            pass

    async def _typing(self):
        while True:
            try:
                await self.api("sendChatAction", {"chat_id": self.chat_id, "action": "typing"}, timeout=10)
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(4)

    async def _handle(self, update):
        msg = update.get("message") or {}
        text, chat = (msg.get("text") or "").strip(), str((msg.get("chat") or {}).get("id"))
        if chat != self.chat_id:
            self.log(f"telegram: ignored message from chat {chat}")
            return
        if time.time() - msg.get("date", 0) > STALE_S:
            return
        if not text:           # voice, photo, file, sticker...: say so instead of staying silent
            try:
                await self.send("I only understand text messages. Please type your question.")
            except Exception:  # noqa: BLE001
                pass
            return
        typing = asyncio.create_task(self._typing())
        try:
            answer = await self.handler(text)
        except Exception as e:  # noqa: BLE001
            self.log(f"telegram: handler error {type(e).__name__}: {e}")
            answer = "Something went wrong while answering. It has been logged."
        finally:
            typing.cancel()
        try:
            await self.send(answer)
        except Exception as e:  # noqa: BLE001
            self.log(f"telegram: send failed {type(e).__name__}: {e}")

    async def run(self):
        try:
            offset = int(open(STATE).read().strip())
        except (OSError, ValueError):
            offset = 0
        backoff = 5
        while True:
            try:
                data = await self.api("getUpdates", {"offset": offset, "timeout": 40, "allowed_updates": ["message", "callback_query"]}, timeout=55)
                backoff = 5
            except urllib.error.HTTPError as e:
                self.log(f"telegram: getUpdates HTTP {e.code} ({'another poller is using this bot' if e.code == 409 else 'error'}); retry in 30 s")
                await asyncio.sleep(30)
                continue
            except Exception as e:  # noqa: BLE001
                self.log(f"telegram: getUpdates failed {type(e).__name__}; retry in {backoff} s")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)
                continue
            for u in data.get("result", []):
                offset = u["update_id"] + 1
                try:
                    open(STATE, "w").write(str(offset))
                except OSError:
                    pass
                asyncio.create_task(self._handle_callback(u) if "callback_query" in u else self._handle(u))
