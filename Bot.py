import requests, time, os
BOT_TOKEN = os.environ.get("BOT_TOKEN")
API_KEY = os.environ.get("API_KEY")
URL = f"https://api.telegram.org/bot{BOT_TOKEN}"
def send(chat_id, text):
    requests.post(f"{URL}/sendMessage", data={"chat_id": chat_id, "text": text})
def get_live():
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get("https://api.football-data.org/v4/matches?status=LIVE", headers=headers, timeout=20).json()
        ms = r.get('matches', [])
        if not ms: return "😴 Hakuna LIVE sasa"
        msg = "🔴 LIVE:\n\n"
        for m in ms[:15]:
            msg += f"{m['homeTeam']['shortName']} {m['score']['fullTime']['home']}-{m['score']['fullTime']['away']} {m['awayTeam']['shortName']}\n"
        return msg
    except Exception as e:
        return f"Error: {e}"
def get_today():
    headers = {"X-Auth-Token": API_KEY}
    try:
        r = requests.get("https://api.football-data.org/v4/matches", headers=headers, timeout=20).json()
        msg = "📅 LEO:\n\n"
        for m in r.get('matches', [])[:20]:
            msg += f"{m['utcDate'][11:16]} - {m['homeTeam']['shortName']} vs {m['awayTeam']['shortName']}\n"
        return msg
    except Exception as e:
        return f"Error {e}"
print("Bot inaanza...")
offset=0
while True:
    try:
        resp = requests.get(f"{URL}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35).json()
        for u in resp.get('result', []):
            offset = u['update_id']+1
            chat_id = u.get('message',{}).get('chat',{}).get('id')
            text = u.get('message',{}).get('text','')
            if text == "/start": send(chat_id, "⚽ Wanimoo LIVE 24/7!\n/score - LIVE\n/today - leo")
            elif text == "/score": send(chat_id, get_live())
            elif text == "/today": send(chat_id, get_today())
        time.sleep(1)
    except Exception as e:
        print(e)
        time.sleep(5)
