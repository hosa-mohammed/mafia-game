# -*- coding: utf-8 -*-
"""
game.py — الحكم الآلي v4 + نظام الغرف
الأدوار: مافيا | دكتور | محقق | قناص | سجان | مستعلم | مواطن
"""
import random
import time
import uuid

# ---------- الأطوار ----------
LOBBY, REVEAL = "LOBBY", "REVEAL"
NIGHT_MAFIA, NIGHT_DOCTOR, NIGHT_JAILER = "NIGHT_MAFIA", "NIGHT_DOCTOR", "NIGHT_JAILER"
NIGHT_SNIPER, NIGHT_DETECTIVE, NIGHT_INQUIRER = "NIGHT_SNIPER", "NIGHT_DETECTIVE", "NIGHT_INQUIRER"
DAWN, DISCUSSION, VOTING, EXECUTION, GAMEOVER = "DAWN", "DISCUSSION", "VOTING", "EXECUTION", "GAMEOVER"

REVEAL_SECONDS = 12
DAWN_SECONDS = 15
EXECUTION_SECONDS = 12
INQUIRER_EVERY = 2      # كشف المستعلم في الليالي 2، 4، 6...
MAX_PLAYERS = 20        # سقف الغرفة

ROLE_AR = {
    "MAFIA": "مافيا", "DOCTOR": "دكتور", "DETECTIVE": "محقق",
    "SNIPER": "قناص", "INQUIRER": "مستعلم", "JAILER": "سجان",
    "CITIZEN": "مواطن",
}
JAIL_BLOCKED = {"NIGHT_SNIPER": "SNIPER", "NIGHT_DETECTIVE": "DETECTIVE", "NIGHT_INQUIRER": "INQUIRER"}
REVEAL_ORDER = ["MAFIA", "JAILER", "SNIPER", "INQUIRER", "DETECTIVE", "DOCTOR", "CITIZEN"]


class Player:
    def __init__(self, name):
        self.id = uuid.uuid4().hex[:10]
        self.name = name
        self.token = uuid.uuid4().hex
        self.role = None
        self.alive = True
        self.left = False          # خرج/أُخرج أثناء الجولة
        self.is_host = False
        self.pick = None
        self.confirmed = False
        self.detective_results = {}
        self.suspicions = {}
        self.jailed_last = None
        self.shot_used = False
        self.inquirer_decision = None
        self.joined_at = time.time()

    def reset_for_game(self):
        self.role, self.alive, self.left = None, True, False
        self.pick, self.confirmed = None, False
        self.detective_results = {}
        self.suspicions, self.jailed_last, self.shot_used = {}, None, False
        self.inquirer_decision = None


class Game:
    def __init__(self):
        self.players = {}
        self.phase = LOBBY
        self.phase_end = 0.0
        self.round = 0
        self.winner = None
        self.public = {}
        self.reveals = []
        self._ni = 0
        self.night_picks = {}
        self.votes = {}
        self.settings = {
            "mafia_count": 1,
            "doctor_enabled": True, "detective_enabled": True,
            "sniper_enabled": False, "inquirer_enabled": False, "jailer_enabled": False,
            "night_turn_seconds": 20, "discussion_seconds": 120, "voting_seconds": 30,
        }

    # ---------- الانضمام والتصفير ----------
    def add_player(self, name):
        p = Player(name)
        if not self.players:
            p.is_host = True
        self.players[p.id] = p
        return p

    def name_taken(self, name, exclude_pid=None):
        return any(q.name == name and q.id != exclude_pid for q in self.players.values())

    def rename(self, pid, new_name):
        p = self.players.get(pid)
        if not p:
            return "لاعب غير معروف"
        if self.phase != LOBBY:
            return "تغيير الاسم متاح قبل بدء الجولة فقط"
        name = (new_name or "").strip()[:20]
        if not name:
            return "الاسم مطلوب"
        if self.name_taken(name, pid):
            return "هذا الاسم مستخدم في الغرفة — اختر غيره"
        p.name = name
        return None

    def remove_player(self, pid):
        """في البهو: حذف كامل. أثناء الجولة: يُحسب خروجاً (كالموت بدون كشف دوره)."""
        p = self.players.get(pid)
        if not p:
            return
        was_host = p.is_host
        if self.phase == LOBBY:
            del self.players[pid]
        else:
            p.alive, p.left = False, True
            p.pick, p.confirmed = None, False
            p.is_host = False
            self.votes.pop(pid, None)
            self.night_picks.get("mafia", {}).pop(pid, None)
            self.check_win()
        if was_host:  # نقل القيادة لأقدم لاعب متبقٍ
            others = sorted((q for q in self.players.values() if q.id != pid),
                            key=lambda x: x.joined_at)
            if others:
                others[0].is_host = True

    def restart(self):
        # المغادرون يُحذفون نهائياً عند بدء جولة جديدة
        for pid in [q.id for q in self.players.values() if q.left]:
            del self.players[pid]
        for p in self.players.values():
            p.reset_for_game()
        self.phase, self.phase_end = LOBBY, 0.0
        self.round, self.winner, self.public = 0, None, {}
        self.reveals = []
        self.night_picks, self.votes = {}, {}

    # ---------- بدء اللعبة (المضيف يحدد الأعداد فقط) ----------
    def host_start(self, s):
        if self.phase != LOBBY:
            return "اللعبة بدأت بالفعل"
        n = len(self.players)
        if n < 4:
            return "يلزم 4 لاعبين على الأقل"
        try:
            m = int(s.get("mafia_count", 1))
            night_t = int(s.get("night_turn_seconds", 20))
            disc_t = int(s.get("discussion_seconds", 120))
            vote_t = int(s.get("voting_seconds", 30))
        except (TypeError, ValueError):
            return "إعدادات غير صالحة"
        if m < 1:
            return "عدد المافيا 1 على الأقل"
        if m >= n - m:
            return "المافيا كثيرة جداً مقارنة بعدد اللاعبين"
        specials = []
        if s.get("doctor_enabled", True):    specials.append("DOCTOR")
        if s.get("jailer_enabled", False):   specials.append("JAILER")
        if s.get("sniper_enabled", False):   specials.append("SNIPER")
        if s.get("detective_enabled", True): specials.append("DETECTIVE")
        if s.get("inquirer_enabled", False): specials.append("INQUIRER")
        if m + len(specials) > n:
            return "الأدوار المفعّلة أكثر من عدد اللاعبين — عطّل بعض الأدوار أو زد اللاعبين"

        self.settings.update({
            "mafia_count": m,
            "doctor_enabled": "DOCTOR" in specials,
            "detective_enabled": "DETECTIVE" in specials,
            "sniper_enabled": "SNIPER" in specials,
            "inquirer_enabled": "INQUIRER" in specials,
            "jailer_enabled": "JAILER" in specials,
            "night_turn_seconds": max(10, min(120, night_t)),
            "discussion_seconds": max(30, min(600, disc_t)),
            "voting_seconds": max(10, min(120, vote_t)),
        })

        roles = ["MAFIA"] * m + specials + ["CITIZEN"] * (n - m - len(specials))
        random.shuffle(roles)
        for p, r in zip(list(self.players.values()), roles):
            p.reset_for_game()
            p.role = r

        self.round, self.winner, self.public = 0, None, {}
        self.reveals = []
        self.night_picks, self.votes = {}, {}
        self.phase = REVEAL
        self.phase_end = time.time() + REVEAL_SECONDS
        return None

    # ---------- الليل ----------
    def night_phases(self):
        s = self.settings
        ph = [NIGHT_MAFIA]
        if s["doctor_enabled"]:    ph.append(NIGHT_DOCTOR)
        if s["jailer_enabled"]:    ph.append(NIGHT_JAILER)
        if s["sniper_enabled"]:    ph.append(NIGHT_SNIPER)
        if s["detective_enabled"]: ph.append(NIGHT_DETECTIVE)
        if s["inquirer_enabled"]:  ph.append(NIGHT_INQUIRER)
        return ph

    def _begin_night(self):
        self.round += 1
        self.night_picks = {"mafia": {}, "doctor": None, "jailer": None,
                            "sniper": None, "detective": None}
        self.votes = {}
        for p in self.players.values():
            p.pick, p.confirmed, p.inquirer_decision = None, False, None
        self._ni = 0
        self._enter_night_phase()

    def _enter_night_phase(self):
        self.phase = self.night_phases()[self._ni]
        self.phase_end = time.time() + self.settings["night_turn_seconds"]

    def _find_role(self, role):
        for p in self.players.values():
            if p.role == role:
                return p
        return None

    # ---------- قرار المستعلم ----------
    def inquirer_decide(self, pid, approve):
        p = self.players.get(pid)
        if not p:
            return "لاعب غير معروف"
        if p.role != "INQUIRER" or not p.alive:
            return "غير مصرح لك بهذا القرار"
        if self.phase != NIGHT_INQUIRER:
            return "ليس وقت القرار"
        if self.round % INQUIRER_EVERY != 0:
            return "ليست ليلة الكشف"
        if self.night_picks.get("jailer") == pid:
            return "🚫 أنت مسجون هذه الليلة — لا قرار لك"
        p.inquirer_decision = bool(approve)
        return None

    # ---------- الاختيار والتأكيد ----------
    def set_pick(self, pid, target_id):
        p = self.players.get(pid)
        if not p:
            return "لاعب غير معروف"
        if p.confirmed:
            return "لقد أكّدت بالفعل"
        t = self.players.get(target_id)
        if not t or not t.alive:
            return "اختيار غير صالح"
        ph = self.phase
        if ph not in (NIGHT_MAFIA, NIGHT_DOCTOR, NIGHT_JAILER, NIGHT_SNIPER,
                      NIGHT_DETECTIVE, NIGHT_INQUIRER, VOTING):
            return "ليس وقت الاختيار"
        if ph in JAIL_BLOCKED and JAIL_BLOCKED[ph] == p.role and p.alive \
                and self.night_picks.get("jailer") == pid:
            return "🚫 أنت مسجون هذه الليلة — قدرتك معطّلة"
        if ph == NIGHT_JAILER and p.alive and p.role == "JAILER":
            if target_id == pid:
                return "لا يمكنك سجن نفسك"
            if target_id == p.jailed_last:
                return "لا يمكنك تكرار سجن نفس الشخص ليلتين متتاليتين"

        p.pick = target_id
        if ph == VOTING:
            if p.alive:
                self.votes[pid] = target_id
            return None
        if not p.alive:
            return None

        acted = False
        if p.role == "MAFIA" and ph == NIGHT_MAFIA:
            self.night_picks["mafia"][pid] = target_id
            acted = True
        elif p.role == "DOCTOR" and ph == NIGHT_DOCTOR:
            self.night_picks["doctor"] = target_id
            acted = True
        elif p.role == "JAILER" and ph == NIGHT_JAILER:
            self.night_picks["jailer"] = target_id
            acted = True
        elif p.role == "SNIPER" and ph == NIGHT_SNIPER and not p.shot_used:
            self.night_picks["sniper"] = target_id
            acted = True
        elif p.role == "DETECTIVE" and ph == NIGHT_DETECTIVE:
            self.night_picks["detective"] = target_id
            is_m = (t.role == "MAFIA")
            p.detective_results[target_id] = {
                "text": "مافيا!" if is_m else "ليس مافيا",
                "tone": "bad" if is_m else "good"}
            acted = True

        if not acted:
            p.suspicions[target_id] = p.suspicions.get(target_id, 0) + 1
        return None

    def confirm_pick(self, pid):
        p = self.players.get(pid)
        if not p:
            return "لاعب غير معروف"
        if p.pick is None:
            return "اختر شخصاً أولاً"
        p.confirmed = True
        return None

    # ---------- حسم الليل ----------
    def _resolve_night(self):
        np = self.night_picks
        deaths = []

        sn = self._find_role("SNIPER")
        if sn and np.get("sniper") and sn.alive and not sn.shot_used \
                and np.get("jailer") != sn.id:
            sn.shot_used = True
            v = self.players[np["sniper"]]
            if v.alive:
                v.alive = False
                deaths.append(v)

        counts = {}
        for mpid, tgt in np["mafia"].items():
            mp = self.players[mpid]
            if mp.alive and np.get("jailer") != mpid:
                counts[tgt] = counts.get(tgt, 0) + 1
        kill = None
        if counts:
            mx = max(counts.values())
            kill = random.choice([t for t, c in counts.items() if c == mx])
        if kill:
            if kill == np.get("jailer") or kill == np.get("doctor"):
                pass
            else:
                v = self.players[kill]
                if v.alive:
                    v.alive = False
                    deaths.append(v)

        jr = self._find_role("JAILER")
        if jr:
            jr.jailed_last = np.get("jailer")

        reveal = None
        inq = self._find_role("INQUIRER")
        if inq and inq.alive and inq.inquirer_decision is True \
                and self.round % INQUIRER_EVERY == 0 and np.get("jailer") != inq.id:
            cnt = {}
            for q in self.players.values():
                if not q.alive:
                    cnt[q.role] = cnt.get(q.role, 0) + 1
            reveal = [{"role_ar": ROLE_AR[r], "count": cnt[r]}
                      for r in REVEAL_ORDER if r in cnt]
            self.reveals.append({"round": self.round, "roles": reveal})

        self.public = {"type": "DAWN",
                       "deaths": [{"name": v.name} for v in deaths],
                       "inquirer_reveal": reveal}
        self.check_win()
        self.phase = DAWN
        self.phase_end = time.time() + DAWN_SECONDS

    def _resolve_vote(self):
        counts = {}
        for voter, t in self.votes.items():
            if self.players[voter].alive and self.players[t].alive:
                counts[t] = counts.get(t, 0) + 1
        executed, tie = None, False
        if counts:
            mx = max(counts.values())
            top = [t for t, c in counts.items() if c == mx]
            if len(top) == 1:
                executed = self.players[top[0]]
            else:
                tie = True
        tallies = sorted(
            [{"name": self.players[t].name, "count": c} for t, c in counts.items()],
            key=lambda x: -x["count"])
        if executed:
            executed.alive = False
        self.public = {"type": "EXECUTION", "tallies": tallies, "tie": tie,
                       "executed": ({"name": executed.name} if executed else None)}
        self.check_win()
        self.phase = EXECUTION
        self.phase_end = time.time() + EXECUTION_SECONDS

    def check_win(self):
        alive = [p for p in self.players.values() if p.alive]
        m = sum(1 for p in alive if p.role == "MAFIA")
        if m == 0:
            self.winner = "CITIZENS"
        elif m >= len(alive) - m:
            self.winner = "MAFIA"

    def _game_over(self):
        self.phase = GAMEOVER
        self.phase_end = 0.0
        self.public = {"type": "GAMEOVER", "winner": self.winner}

    # ---------- تقدّم الأطوار ----------
    def advance(self):
        ph = self.phase
        if ph in (LOBBY, GAMEOVER):
            return
        if ph == REVEAL:
            self._begin_night()
        elif ph in (NIGHT_MAFIA, NIGHT_DOCTOR, NIGHT_JAILER, NIGHT_SNIPER,
                    NIGHT_DETECTIVE, NIGHT_INQUIRER):
            self._ni += 1
            if self._ni < len(self.night_phases()):
                self._enter_night_phase()
            else:
                self._resolve_night()
        elif ph == DAWN:
            if self.winner:
                self._game_over()
            else:
                self.phase = DISCUSSION
                self.phase_end = time.time() + self.settings["discussion_seconds"]
        elif ph == DISCUSSION:
            if self.winner:   # قد يفوز فريق بأكمله بسبب خروج لاعبين أثناء المناقشة
                self._game_over()
                return
            for p in self.players.values():
                p.pick, p.confirmed = None, False
            self.votes = {}
            self.phase = VOTING
            self.phase_end = time.time() + self.settings["voting_seconds"]
        elif ph == VOTING:
            self._resolve_vote()
        elif ph == EXECUTION:
            if self.winner:
                self._game_over()
            else:
                self._begin_night()

    # ---------- لقطة الحالة ----------
    def snapshot(self, pid):
        now = time.time()
        players = [{"id": p.id, "name": p.name, "alive": p.alive,
                    "left": p.left, "is_host": p.is_host}
                   for p in sorted(self.players.values(), key=lambda x: x.joined_at)]
        snap = {
            "phase": self.phase, "round": self.round,
            "server_time": now, "phase_ends_at": self.phase_end,
            "winner": self.winner, "public": self.public,
            "reveals": self.reveals,
            "night_index": self._ni + 1, "night_total": len(self.night_phases()),
            "dead_names": [p.name for p in self.players.values() if not p.alive],
            "players": players, "you": None,
        }
        me = self.players.get(pid)
        if me:
            y = {"id": me.id, "name": me.name, "alive": me.alive, "left": me.left,
                 "is_host": me.is_host, "confirmed": me.confirmed, "pick": me.pick,
                 "role": me.role, "role_ar": ROLE_AR.get(me.role, ""),
                 "shot_used": me.shot_used}
            y["suspicions"] = [{"name": self.players[k].name, "count": c}
                               for k, c in sorted(me.suspicions.items(), key=lambda x: -x[1])
                               if k in self.players]
            if me.role == "MAFIA":
                y["partners"] = [{"id": q.id, "name": q.name}
                                 for q in self.players.values()
                                 if q.role == "MAFIA" and q.id != me.id]
                if self.phase == NIGHT_MAFIA and me.alive:
                    y["partners_live"] = []
                    for q in self.players.values():
                        if q.role == "MAFIA" and q.id != me.id:
                            tg = self.players.get(q.pick) if (q.pick and q.confirmed) else None
                            y["partners_live"].append({"name": q.name, "confirmed": q.confirmed,
                                                       "target": tg.name if tg else None})
            if me.role == "DETECTIVE":
                y["private_results"] = [{"id": k, "name": self.players[k].name, **v}
                                        for k, v in me.detective_results.items()
                                        if k in self.players]
            if me.role == "INQUIRER":
                y["inquirer_tonight"] = (self.round % INQUIRER_EVERY == 0)
                y["inquirer_decision"] = me.inquirer_decision
            snap["you"] = y
        if self.phase == GAMEOVER:
            snap["reveal_all"] = [{"name": p.name, "role_ar": ROLE_AR[p.role], "alive": p.alive}
                                  for p in sorted(self.players.values(), key=lambda x: x.joined_at)]
        return snap


# ---------- الغرفة: تغلّف اللعبة بهوية ودخول مضبوط ----------
class Room:
    def __init__(self, code, name, is_public):
        self.code = code
        self.name = name
        self.is_public = is_public
        self.locked = False
        self.created_at = time.time()
        self.last_activity = time.time()
        self.game = Game()

    def touch(self):
        self.last_activity = time.time()

    def info(self):
        g = self.game
        host = next((p.name for p in g.players.values() if p.is_host), "—")
        return {"code": self.code, "name": self.name,
                "players": len(g.players), "max": MAX_PLAYERS,
                "host": host}