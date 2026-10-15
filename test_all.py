import asyncio, urllib.request, json, websockets

async def test_all():
    req = urllib.request.Request('http://127.0.0.1:8000/api/missions')
    with urllib.request.urlopen(req) as response:
        mission_id = json.loads(response.read().decode())[0]['id']
        
    req = urllib.request.Request(f'http://127.0.0.1:8000/api/engineering/sitl/missions/{mission_id}/execute', method='POST')
    # to avoid 422, let's just make it a POST with no body
    req.add_header('Content-Length', '0')
    req.add_header('Accept', 'application/json')
    try:
        with urllib.request.urlopen(req) as response:
            print("Executed:", response.read().decode())
    except urllib.error.HTTPError as e:
        print("Execute error:", e.read().decode())
        
    uri = f'ws://127.0.0.1:8000/ws/mission/{mission_id}'
    try:
        async with websockets.connect(uri) as ws:
            print('Connected!')
            for i in range(15):
                msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                data = json.loads(msg)
                print(f"[{i}] Received: {data.get('type')}")
    except Exception as e:
        print(f'WS Error: {e}')

asyncio.run(test_all())
