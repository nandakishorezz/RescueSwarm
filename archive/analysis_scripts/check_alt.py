import urllib.request, json
req = urllib.request.Request('http://127.0.0.1:8000/api/missions')
with urllib.request.urlopen(req) as response:
    missions = json.loads(response.read().decode())
    for m in missions:
        b = m.get('boundary')
        alt = b.get('origin', {}).get('altitude_m') if b else None
        print(f"{m['id']}: alt={alt}")
