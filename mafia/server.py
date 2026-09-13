# -*- coding: utf-8 -*-
"""
server.py — الخادم v2: غرف متعددة معزولة + جلسات + بث SSE لكل غرفة.
"""
import json
import queue
import random
import threading
import time
import os

from flask import Flask, Response, jsonify, request

import game as G
from game import Room, MAX_PLAYERS

app = Flask(__name__, static_folder="static", static_url_path="")

ROOMS = {}      # code -> Room
SESSIONS = {}   # token -> {"room": code, "pid": pid}
CLIENTS = []    # {"token","room","pid","q"}
LOCK = threading.RLock()

CODE_CHARS = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"  # بدون حروف/أرقام متشابهة


def _gen_code():
    while True:
        code = "".join(random.choice(CODE_CHARS) for _ in range(5))
        if code not in ROOMS:
            return code


def snap_for(room, pid):
    s = room.game.snapshot(pid)
    s["room"] = {"code": room.code, "name": room.name,
                 "is_public": room.is_public, "locked": room.locked,
                 "max": MAX_PLAYERS}
    return s


def auth(token):
    """يرجع (غرفة، لاعب) أو (None, None) — وينظف الجلسات الميتة."""
    s = SESSIONS.get(token or "")
    if not s:
        return None, None
    room = ROOMS.get(s["room"])
    if not room:
        SESSIONS.pop(token, None)
        return None, None
    p = room.game.players.get(s["pid"])
    if not p:
        SESSIONS.pop(token, None)
        return None, None
    room.touch()
    return room, p


def broadcast(room):
    """بثّ لقطة مخصصة لكل هاتف في الغرفة — وتنظيف الاتصالات الميتة."""
    g = room.game
    for c in list(CLIENTS):
        if c["room"] != room.code:
            continue
        if c["token"] not in SESSIONS or c["pid"] not in g.players:
            try:
                c["q"].put_nowait({"__bye__": True})
            except Exception:
                pass
            try:
                CLIENTS.remove(c)
            except ValueError:
                pass
            continue
        try:
            c["q"].put_nowait(snap_for(room, c["pid"]))
        except Exception:
            pass


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/ping")
def api_ping():
    return jsonify({"ok": True})


# ---------------- القائمة والغرف ----------------
@app.get("/api/rooms")
def api_rooms():
    with LOCK:
        items = [r.info() for r in ROOMS.values()
                 if r.is_public and not r.locked and r.game.phase == G.LOBBY]
    items.sort(key=lambda x: -x["players"])
    return jsonify({"rooms": items[:30]})


@app.post("/api/room/create")
def api_room_create():
    data = request.get_json(force=True)
    player_name = (data.get("player_name") or "").strip()[:20]
    room_name = (data.get("room_name") or "").strip()[:30]
    if not player_name:
        return jsonify({"error": "اكتب اسمك"}), 400
    with LOCK:
        code = _gen_code()
        room = Room(code, room_name or f"غرفة {code}", bool(data.get("is_public")))
        ROOMS[code] = room
        p = room.game.add_player(player_name)  # أول لاعب = المضيف تلقائياً
        SESSIONS[p.token] = {"room": code, "pid": p.id}
        return jsonify({"pid": p.id, "token": p.token, "room": code,
                        "state": snap_for(room, p.id)})


@app.post("/api/room/join")
def api_room_join():
    data = request.get_json(force=True)
    code = (data.get("code") or "").strip().upper()
    name = (data.get("player_name") or "").strip()[:20]
    if not name:
        return jsonify({"error": "اكتب اسمك"}), 400
    with LOCK:
        room = ROOMS.get(code)
        if not room:
            return jsonify({"error": "لا توجد غرفة بهذا الرمز — تحقق منه"}), 404
        if room.locked:
            return jsonify({"error": "🔒 الغرفة مغلقة من المضيف"}), 403
        if room.game.phase != G.LOBBY:
            return jsonify({"error": "الجولة جارية بالفعل — انتظر انتهاءها"}), 400
        if len(room.game.players) >= MAX_PLAYERS:
            return jsonify({"error": "الغرفة ممتلئة"}), 400
        if room.game.name_taken(name):
            return jsonify({"error": "هذا الاسم مستخدم في الغرفة — اختر غيره"}), 400
        p = room.game.add_player(name)
        SESSIONS[p.token] = {"room": code, "pid": p.id}
        broadcast(room)
        return jsonify({"pid": p.id, "token": p.token, "room": code,
                        "state": snap_for(room, p.id)})


@app.get("/api/whoami")
def api_whoami():
    token = request.args.get("token", "")
    with LOCK:
        room, p = auth(token)
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        return jsonify({"ok": True, "state": snap_for(room, p.id)})


# ---------------- البث المباشر ----------------
@app.route("/api/events")
def api_events():
    token = request.args.get("token", "")
    with LOCK:
        room, p = auth(token)
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        q = queue.Queue()
        entry = {"token": token, "room": room.code, "pid": p.id, "q": q}
        CLIENTS.append(entry)
        q.put(snap_for(room, p.id))

    def gen():
        try:
            while True:
                try:
                    data = q.get(timeout=15)
                    yield "data: " + json.dumps(data, ensure_ascii=False) + "\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            try:
                CLIENTS.remove(entry)
            except ValueError:
                pass

    return Response(gen(), mimetype="text/event-stream",
                    headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ---------------- أفعال اللاعب ----------------
@app.post("/api/pick")
def api_pick():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if data.get("confirm"):
            err = room.game.confirm_pick(p.id)
        else:
            err = room.game.set_pick(p.id, data.get("target_id"))
        broadcast(room)
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/inquirer/decide")
def api_inq_decide():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        err = room.game.inquirer_decide(p.id, bool(data.get("approve")))
        broadcast(room)
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/rename")
def api_rename():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        err = room.game.rename(p.id, data.get("new_name", ""))
        broadcast(room)
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/leave")
def api_leave():
    data = request.get_json(force=True)
    token = data.get("token", "")
    with LOCK:
        room, p = auth(token)
        if room and p:
            room.game.remove_player(p.id)
            SESSIONS.pop(token, None)
            if not room.game.players:
                ROOMS.pop(room.code, None)
            else:
                broadcast(room)
    return jsonify({"ok": True})


# ---------------- أفعال المضيف ----------------
@app.post("/api/host/kick")
def api_host_kick():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        target = room.game.players.get(data.get("target_pid", ""))
        if not target:
            return jsonify({"error": "اللاعب غير موجود"}), 404
        if target.id == p.id:
            return jsonify({"error": "استخدم مغادرة الغرفة بدلاً من إخراج نفسك"}), 400
        SESSIONS.pop(target.token, None)   # قطع جلسته → سيصل هاتفه إشعار خروج
        room.game.remove_player(target.id)
        broadcast(room)
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/host/settings")
def api_host_settings():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        if "is_public" in data:
            room.is_public = bool(data["is_public"])
        if "locked" in data:
            room.locked = bool(data["locked"])
        broadcast(room)
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/host/start")
def api_start():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        err = room.game.host_start(data.get("settings") or {})
        broadcast(room)
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/host/skip")
def api_skip():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        room.game.advance()
        broadcast(room)
        return jsonify({"state": snap_for(room, p.id)})


@app.post("/api/host/restart")
def api_restart():
    data = request.get_json(force=True)
    with LOCK:
        room, p = auth(data.get("token", ""))
        if not room:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        room.game.restart()
        broadcast(room)
        return jsonify({"state": snap_for(room, p.id)})


# ---------------- خيوط الخلفية ----------------
def ticker():
    while True:
        time.sleep(0.5)
        with LOCK:
            now = time.time()
            for room in list(ROOMS.values()):
                g = room.game
                if (g.phase not in (G.LOBBY, G.GAMEOVER)
                        and g.phase_end and now >= g.phase_end):
                    g.advance()
                    broadcast(room)


def cleaner():
    """حذف الغرف المهجورة (بلا نشاط ساعتين)."""
    while True:
        time.sleep(60)
        with LOCK:
            now = time.time()
            for code in list(ROOMS):
                if now - ROOMS[code].last_activity > 7200:
                    del ROOMS[code]


def _start_threads_once():
    threading.Thread(target=ticker, daemon=True).start()
    threading.Thread(target=cleaner, daemon=True).start()

_start_threads_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)