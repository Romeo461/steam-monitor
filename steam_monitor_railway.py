"""
===========================================
  STEAM MONITOR — versión Railway (nube)
  Corre 24/7 sin necesitar la PC prendida
===========================================
Configuración: variables de entorno en Railway
  STEAM_API_KEY   → tu API Key de Steam
  STEAM_ID        → tu Steam ID (76561198302366953)
  NTFY_TOPIC      → nombre de tu canal ntfy
"""

import os
import time
import csv
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote
from collections import defaultdict

# Zona horaria Argentina (UTC-3, sin cambio de horario)
ARG = timezone(timedelta(hours=-3))

# ─── Config desde variables de entorno ───────────────────────────────────────
API_KEY       = os.environ["STEAM_API_KEY"]
USER_STEAM_ID = os.environ["STEAM_ID"]
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "")
FRIEND_NAME   = "mumis"
POLL_INTERVAL = 60   # segundos entre consultas
LOG_FILE      = "mumis_sessions.csv"

DIAS = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]

# Estados de Steam
STATES = {
    0: ("OFFLINE",  "🔴"),
    1: ("ONLINE",   "🟢"),
    2: ("BUSY",     "🔴"),
    3: ("AUSENTE",  "🟡"),
    4: ("SNOOZE",   "🟡"),
    5: ("TRADE",    "🟢"),
    6: ("PLAY",     "🟢"),
}

def state_name(code):
    return STATES.get(code, ("DESCONOCIDO", "⚫"))[0]

def state_emoji(code):
    return STATES.get(code, ("DESCONOCIDO", "⚫"))[1]

def is_active(code):
    return code != 0

# ─── API Steam ────────────────────────────────────────────────────────────────

def get_friend_list():
    r = requests.get(
        "https://api.steampowered.com/ISteamUser/GetFriendList/v0001/",
        params={"key": API_KEY, "steamid": USER_STEAM_ID, "relationship": "friend"},
        timeout=10,
    )
    return r.json().get("friendslist", {}).get("friends", [])


def get_player_summaries(steam_ids):
    if not steam_ids:
        return []
    r = requests.get(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
        params={"key": API_KEY, "steamids": ",".join(steam_ids)},
        timeout=10,
    )
    return r.json().get("response", {}).get("players", [])


def find_friend_by_name(name):
    friends = get_friend_list()
    ids = [f["steamid"] for f in friends]
    for i in range(0, len(ids), 100):
        for p in get_player_summaries(ids[i:i+100]):
            if name.lower() in p.get("personaname", "").lower():
                return p
    return None

# ─── Notificaciones ───────────────────────────────────────────────────────────

def notify(title, message, tags="steam", priority="high"):
    if not NTFY_TOPIC:
        return
    try:
        url = f"https://ntfy.sh/{NTFY_TOPIC}"
        print(f"[NTFY] Enviando: {title}")
        r = requests.post(
            url,
            data=message.encode("utf-8"),
            headers={"Title": quote(title), "Priority": priority, "Tags": tags},
            timeout=8,
        )
        print(f"[NTFY] Respuesta: {r.status_code}")
    except Exception as e:
        print(f"[NTFY] Error: {e}")

# ─── Log CSV ──────────────────────────────────────────────────────────────────

def log_event(name, event, timestamp, duration=None):
    new_file = not os.path.isfile(LOG_FILE)
    with open(LOG_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["Nombre", "Evento", "Fecha/Hora", "Duracion (min)"])
        w.writerow([name, event, timestamp, duration or ""])

# ─── Formato ──────────────────────────────────────────────────────────────────

def fmt(minutes):
    if minutes < 60:
        return f"{int(minutes)} min"
    return f"{int(minutes//60)}h {int(minutes%60)}min"

# ─── Análisis de patrones ─────────────────────────────────────────────────────

class PatternTracker:
    """Acumula sesiones en memoria y genera reportes de rutina."""

    def __init__(self):
        self.sessions = []          # lista de dicts con hora_conexion, hora_desconexion, duracion, dia
        self.connect_hours  = defaultdict(int)   # hora → cantidad de conexiones
        self.disconnect_hours = defaultdict(int) # hora → cantidad de desconexiones
        self.day_counts     = defaultdict(int)   # dia semana → cantidad de sesiones
        self.durations      = []                 # minutos por sesión
        self.last_report_week = None             # semana del último reporte enviado

    def record_connect(self, dt: datetime):
        self.connect_hours[dt.hour] += 1
        self.day_counts[dt.weekday()] += 1

    def record_disconnect(self, dt: datetime, duration_min: float):
        self.disconnect_hours[dt.hour] += 1
        if duration_min:
            self.durations.append(duration_min)
        self.sessions.append({
            "dia": dt.weekday(),
            "hora_desconexion": dt.hour,
            "duracion": duration_min,
        })

    def peak_hours(self, counter, top=3):
        """Devuelve las top horas más frecuentes con formato HH:00."""
        if not counter:
            return "sin datos aun"
        sorted_hours = sorted(counter.items(), key=lambda x: x[1], reverse=True)[:top]
        return ", ".join(f"{h:02d}:00 ({c}x)" for h, c in sorted_hours)

    def peak_days(self, top=3):
        if not self.day_counts:
            return "sin datos aun"
        sorted_days = sorted(self.day_counts.items(), key=lambda x: x[1], reverse=True)[:top]
        return ", ".join(f"{DIAS[d]} ({c}x)" for d, c in sorted_days)

    def avg_duration(self):
        if not self.durations:
            return "sin datos aun"
        avg = sum(self.durations) / len(self.durations)
        return fmt(avg)

    def total_sessions(self):
        return len(self.sessions)

    def should_send_weekly(self, now: datetime) -> bool:
        """Envía reporte los domingos a las 20:00 hora Argentina, una vez por semana."""
        week = now.isocalendar()[1]
        if now.weekday() == 6 and now.hour == 20 and week != self.last_report_week:
            self.last_report_week = week
            return True
        return False

    def build_report(self, fname: str) -> str:
        n = self.total_sessions()
        if n < 3:
            return (
                f"Reporte semanal de {fname}\n\n"
                f"Todavia hay pocas sesiones registradas ({n}) "
                f"para detectar patrones. Seguimos acumulando datos!"
            )

        lines = [
            f"Reporte semanal — {fname}",
            f"Sesiones registradas: {n}",
            "",
            f"Suele conectarse: {self.peak_hours(self.connect_hours)}",
            f"Suele desconectarse: {self.peak_hours(self.disconnect_hours)}",
            f"Dias mas activos: {self.peak_days()}",
            f"Duracion promedio: {self.avg_duration()}",
        ]

        # Franja horaria predominante
        if self.connect_hours:
            top_hour = max(self.connect_hours, key=self.connect_hours.get)
            if 6 <= top_hour < 12:
                franja = "manana (6-12)"
            elif 12 <= top_hour < 18:
                franja = "tarde (12-18)"
            elif 18 <= top_hour < 24:
                franja = "noche (18-24)"
            else:
                franja = "madrugada (0-6)"
            lines.append(f"Franja principal: {franja}")

        return "\n".join(lines)


# ─── Monitor principal ────────────────────────────────────────────────────────

def main():
    print(f"[INICIO] Buscando a '{FRIEND_NAME}'...")
    friend = find_friend_by_name(FRIEND_NAME)
    if not friend:
        print(f"[ERROR] No se encontro a '{FRIEND_NAME}' en la lista de amigos.")
        return

    fid           = friend["steamid"]
    fname         = friend["personaname"]
    prev_state    = friend.get("personastate", 0)
    session_start = datetime.now(ARG) if is_active(prev_state) else None
    away_start    = None
    tracker       = PatternTracker()

    print(f"[OK] Monitoreando a {fname} — ahora {state_emoji(prev_state)} {state_name(prev_state)}")

    notify(
        "Steam Monitor activo",
        f"Monitoreando a {fname} - ahora {state_name(prev_state)}",
        "white_check_mark,steam",
        priority="default",
    )

    while True:
        try:
            now = datetime.now(ARG)

            # ── Reporte semanal de patrones ────────────────────────────────────
            if tracker.should_send_weekly(now):
                report = tracker.build_report(fname)
                print(f"[PATRON] Enviando reporte semanal...")
                notify("Reporte semanal de rutina", report, "bar_chart,steam", priority="default")

            players = get_player_summaries([fid])
            if not players:
                time.sleep(POLL_INTERVAL)
                continue

            p         = players[0]
            cur_state = p.get("personastate", 0)
            ts        = now.strftime("%Y-%m-%d %H:%M:%S")
            ts_short  = now.strftime("%H:%M")

            if cur_state == prev_state:
                time.sleep(POLL_INTERVAL)
                continue

            emoji = state_emoji(cur_state)
            sname = state_name(cur_state)
            print(f"[{ts}] {emoji} {sname} — {fname}")

            # ── OFFLINE → ONLINE ──────────────────────────────────────────────
            if not is_active(prev_state) and is_active(cur_state):
                session_start = now
                away_start    = None
                tracker.record_connect(now)
                log_event(fname, "CONECTADO", ts)
                notify(
                    f"mumis entro a Steam",
                    f"Se conecto a las {ts_short}",
                    "green_circle,steam",
                )

            # ── ONLINE → AUSENTE ──────────────────────────────────────────────
            elif prev_state == 1 and cur_state in (3, 4):
                away_start = now
                time_online = ""
                if session_start:
                    mins = round((now - session_start).total_seconds() / 60, 1)
                    time_online = f" (lleva {fmt(mins)} conectado)"
                log_event(fname, "AUSENTE", ts)
                notify(
                    f"mumis esta ausente en Steam",
                    f"Inactivo desde las {ts_short}{time_online}",
                    "yellow_circle,steam",
                )

            # ── AUSENTE → ONLINE ──────────────────────────────────────────────
            elif prev_state in (3, 4) and cur_state == 1:
                away_txt = ""
                if away_start:
                    away_mins = round((now - away_start).total_seconds() / 60, 1)
                    away_txt  = f" (estuvo {fmt(away_mins)} ausente)"
                away_start = None
                log_event(fname, "ACTIVO DE NUEVO", ts)
                notify(
                    f"mumis volvio a estar activo",
                    f"Volvio a las {ts_short}{away_txt}",
                    "green_circle,steam",
                )

            # ── Cualquier estado activo → OFFLINE ─────────────────────────────
            elif is_active(prev_state) and not is_active(cur_state):
                dur     = None
                dur_txt = ""
                if session_start:
                    dur     = round((now - session_start).total_seconds() / 60, 1)
                    dur_txt = f" — estuvo {fmt(dur)} online"
                    tracker.record_disconnect(now, dur)
                log_event(fname, "DESCONECTADO", ts, dur)
                notify(
                    f"mumis se fue de Steam",
                    f"Se desconecto a las {ts_short}{dur_txt}",
                    "red_circle,steam",
                )
                session_start = None
                away_start    = None

            prev_state = cur_state

        except Exception as e:
            print(f"[WARN] Error temporal: {e}")

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
