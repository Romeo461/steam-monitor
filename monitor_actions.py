"""
Steam Monitor — versión GitHub Actions
Corre cada 5 minutos, guarda el estado en state.json
"""

import os
import json
import requests
from datetime import datetime, timezone, timedelta
from urllib.parse import quote

ARG = timezone(timedelta(hours=-3))

API_KEY       = os.environ["STEAM_API_KEY"]
USER_STEAM_ID = os.environ["STEAM_ID"]
NTFY_TOPIC    = os.environ.get("NTFY_TOPIC", "")
FRIEND_NAME   = "mumis"
STATE_FILE    = "state.json"

STATES = {
    0: "OFFLINE",
    1: "ONLINE",
    2: "BUSY",
    3: "AUSENTE",
    4: "SNOOZE",
}

def is_active(code):
    return code != 0

def fmt(minutes):
    if not minutes:
        return ""
    if minutes < 60:
        return f"{int(minutes)} min"
    return f"{int(minutes//60)}h {int(minutes%60)}min"

def get_player_summaries(steam_ids):
    r = requests.get(
        "https://api.steampowered.com/ISteamUser/GetPlayerSummaries/v0002/",
        params={"key": API_KEY, "steamids": ",".join(steam_ids)},
        timeout=10,
    )
    return r.json().get("response", {}).get("players", [])

def find_friend(name):
    r = requests.get(
        "https://api.steampowered.com/ISteamUser/GetFriendList/v0001/",
        params={"key": API_KEY, "steamid": USER_STEAM_ID, "relationship": "friend"},
        timeout=10,
    )
    friends = r.json().get("friendslist", {}).get("friends", [])
    ids = [f["steamid"] for f in friends]
    for i in range(0, len(ids), 100):
        for p in get_player_summaries(ids[i:i+100]):
            if name.lower() in p.get("personaname", "").lower():
                return p
    return None

def notify(title, message, tags="steam"):
    if not NTFY_TOPIC:
        return
    try:
        requests.post(
            f"https://ntfy.sh/{NTFY_TOPIC}",
            data=message.encode("utf-8"),
            headers={"Title": quote(title), "Priority": "high", "Tags": tags},
            timeout=8,
        )
        print(f"[NTFY] Enviado: {title}")
    except Exception as e:
        print(f"[NTFY] Error: {e}")

def load_state():
    if os.path.isfile(STATE_FILE):
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    return {"state": 0, "session_start": None, "away_start": None}

def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f)

def main():
    now      = datetime.now(ARG)
    ts_short = now.strftime("%H:%M")
    ts_full  = now.strftime("%Y-%m-%d %H:%M:%S")

    print(f"[{ts_full}] Chequeando estado de {FRIEND_NAME}...")

    friend = find_friend(FRIEND_NAME)
    if not friend:
        print(f"[ERROR] No se encontro a {FRIEND_NAME}")
        return

    cur_state = friend.get("personastate", 0)
    saved     = load_state()
    prev_state = saved.get("state", 0)

    print(f"  Estado anterior: {STATES.get(prev_state, '?')} → Actual: {STATES.get(cur_state, '?')}")

    if cur_state == prev_state:
        print("  Sin cambios.")
        return

    # ── OFFLINE → ONLINE ──────────────────────────────────────────────────────
    if not is_active(prev_state) and is_active(cur_state):
        saved["session_start"] = ts_full
        saved["away_start"]    = None
        notify(
            "mumis entro a Steam",
            f"Se conecto a las {ts_short}",
            "green_circle,steam",
        )

    # ── ONLINE → AUSENTE ──────────────────────────────────────────────────────
    elif prev_state == 1 and cur_state in (3, 4):
        saved["away_start"] = ts_full
        tiempo = ""
        if saved.get("session_start"):
            ini = datetime.fromisoformat(saved["session_start"]).replace(tzinfo=ARG)
            mins = round((now - ini).total_seconds() / 60, 1)
            tiempo = f" (lleva {fmt(mins)} conectado)"
        notify(
            "mumis esta ausente en Steam",
            f"Inactivo desde las {ts_short}{tiempo}",
            "yellow_circle,steam",
        )

    # ── AUSENTE → ONLINE ──────────────────────────────────────────────────────
    elif prev_state in (3, 4) and cur_state == 1:
        away_txt = ""
        if saved.get("away_start"):
            ini = datetime.fromisoformat(saved["away_start"]).replace(tzinfo=ARG)
            mins = round((now - ini).total_seconds() / 60, 1)
            away_txt = f" (estuvo {fmt(mins)} ausente)"
        saved["away_start"] = None
        notify(
            "mumis volvio a estar activo",
            f"Volvio a las {ts_short}{away_txt}",
            "green_circle,steam",
        )

    # ── ONLINE/AUSENTE → OFFLINE ──────────────────────────────────────────────
    elif is_active(prev_state) and not is_active(cur_state):
        dur_txt = ""
        if saved.get("session_start"):
            ini = datetime.fromisoformat(saved["session_start"]).replace(tzinfo=ARG)
            mins = round((now - ini).total_seconds() / 60, 1)
            dur_txt = f" — estuvo {fmt(mins)} online"
        notify(
            "mumis se fue de Steam",
            f"Se desconecto a las {ts_short}{dur_txt}",
            "red_circle,steam",
        )
        saved["session_start"] = None
        saved["away_start"]    = None

    saved["state"] = cur_state
    save_state(saved)
    print("  Estado guardado.")

if __name__ == "__main__":
    main()
