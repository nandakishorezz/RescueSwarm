import asyncio
import websockets
import json

async def test_ws():
    # First, get the mission ID from the API
    import urllib.request
    try:
        req = urllib.request.Request("http://127.0.0.1:8000/api/missions")
        with urllib.request.urlopen(req) as response:
            data = json.loads(response.read().decode())
            if not data:
                print("No missions found")
                return
            mission_id = data[0]['id']
            print(f"Mission ID: {mission_id}")
    except Exception as e:
        print(f"Error fetching mission: {e}")
        return

    # Now connect to WS
    uri = f"ws://127.0.0.1:8000/ws/mission/{mission_id}"
    print(f"Connecting to {uri}")
    try:
        async with websockets.connect(uri) as websocket:
            print("Connected! Waiting for messages...")
            for _ in range(10):
                msg = await asyncio.wait_for(websocket.recv(), timeout=2.0)
                data = json.loads(msg)
                print(f"Received: {data.get('type')} (seq: {data.get('sequence')})")
    except Exception as e:
        print(f"WS Error: {e}")

asyncio.run(test_ws())
