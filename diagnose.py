import json
import os
import urllib.error
import urllib.request

token = os.environ["DISCORD_BOT_TOKEN"]
API = "https://discord.com/api/v10"


def req(path):
    r = urllib.request.Request(f"{API}{path}")
    r.add_header("Authorization", f"Bot {token}")
    r.add_header("User-Agent", "DiscordDelayRelay (https://github.com/TuanAnh17N/apex-bot-delay-relay, 1.0)")
    try:
        with urllib.request.urlopen(r, timeout=15) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:300]


status, body = req("/users/@me")
print(f"=== /users/@me -> {status} ===")
print(body)

status, body = req("/users/@me/guilds")
print(f"\n=== /users/@me/guilds -> {status} ===")
print(body)

channels = [
    "1507159472156704908", "1503837558017360034", "1503838210755657828",
    "1503838275964768418", "1503838319019167885", "1504811070110437386",
    "1551967531114168391", "1551967578279121009", "1551967608503279696",
    "1551967630737412158", "1551967653009035304", "1551967672491712542",
]
print("\n=== per-channel GET /channels/{id} ===")
for cid in channels:
    status, body = req(f"/channels/{cid}")
    print(cid, "->", status, str(body)[:200])
