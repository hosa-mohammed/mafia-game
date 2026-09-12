# -*- coding: utf-8 -*-
"""
server.py — الخادم: يخدم الواجهة، يبث التحديثات (SSE)، ويستقبل الاختيارات.
"""
import json
import queue
import threading
import time
import os

from flask import Flask, Response, jsonify, request

import game as G

app = Flask(__name__, static_folder="static", static_url_path="")
game = G.Game()
LOCK = threading.RLock()
CLIENTS = []


def broadcast():
    for c in list(CLIENTS):
        try:
            c["q"].put_nowait(game.snapshot(c["pid"]))
        except Exception:
            pass


def auth(token):
    for p in game.players.values():
        if p.token == token:
            return p
    return None


@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.post("/api/join")
def api_join():
    data = request.get_json(force=True)
    name = (data.get("name") or "").strip()[:20]
    if not name:
        return jsonify({"error": "اكتب اسمك"}), 400
    with LOCK:
        if game.phase != G.LOBBY:
            return jsonify({"error": "اللعبة بدأت بالفعل، انتظر الجولة القادمة"}), 400
        p = game.add_player(name)
        broadcast()
        return jsonify({"pid": p.id, "token": p.token, "state": game.snapshot(p.id)})


@app.route("/api/events")
def api_events():
    pid = request.args.get("pid", "")
    token = request.args.get("token", "")
    with LOCK:
        p = game.players.get(pid)
        if not p or p.token != token:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        q = queue.Queue()
        entry = {"pid": pid, "q": q}
        CLIENTS.append(entry)
        q.put(game.snapshot(pid))

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


# ---- نبض الإبقاء على الخدمة صاحية (يُضاف بعد اكتمال الدوال السابقة، وليس داخلها!) ----
@app.post("/api/ping")
def api_ping():
    return jsonify({"ok": True})


@app.route("/api/whoami")
def api_whoami():
    p = game.players.get(request.args.get("pid", ""))
    if not p or p.token != request.args.get("token", ""):
        return jsonify({"error": "جلسة غير صالحة"}), 403
    return jsonify({"ok": True, "name": p.name})


@app.post("/api/pick")
def api_pick():
    data = request.get_json(force=True)
    with LOCK:
        p = auth(data.get("token", ""))
        if not p:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        if data.get("confirm"):
            err = game.confirm_pick(p.id)
        else:
            err = game.set_pick(p.id, data.get("target_id"))
        broadcast()
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": game.snapshot(p.id)})


@app.post("/api/inquirer/decide")
def api_inq_decide():
    data = request.get_json(force=True)
    with LOCK:
        p = auth(data.get("token", ""))
        if not p:
            return jsonify({"error": "جلسة غير صالحة"}), 403
        err = game.inquirer_decide(p.id, bool(data.get("approve")))
        broadcast()
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": game.snapshot(p.id)})


@app.post("/api/host/start")
def api_start():
    data = request.get_json(force=True)
    with LOCK:
        p = auth(data.get("token", ""))
        if not p or not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        err = game.host_start(data.get("settings") or {})
        broadcast()
        if err:
            return jsonify({"error": err}), 400
        return jsonify({"state": game.snapshot(p.id)})


@app.post("/api/host/skip")
def api_skip():
    data = request.get_json(force=True)
    with LOCK:
        p = auth(data.get("token", ""))
        if not p or not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        game.advance()
        broadcast()
        return jsonify({"state": game.snapshot(p.id)})


@app.post("/api/host/restart")
def api_restart():
    data = request.get_json(force=True)
    with LOCK:
        p = auth(data.get("token", ""))
        if not p or not p.is_host:
            return jsonify({"error": "للمضيف فقط"}), 403
        game.restart()
        broadcast()
        return jsonify({"state": game.snapshot(p.id)})


def ticker():
    while True:
        time.sleep(0.5)
        with LOCK:
            if (game.phase not in (G.LOBBY, G.GAMEOVER)
                    and game.phase_end and time.time() >= game.phase_end):
                game.advance()
                broadcast()


_Ticker_started = [False]
def _start_ticker_once():
    if not _Ticker_started[0]:
        _Ticker_started[0] = True
        threading.Thread(target=ticker, daemon=True).start()

_start_ticker_once()

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, threaded=True)