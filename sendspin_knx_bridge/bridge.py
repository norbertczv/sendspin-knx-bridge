import asyncio, json, math, time
import websockets
from xknx import XKNX
from xknx.io import ConnectionConfig, ConnectionType
from xknx.remote_value import RemoteValueDPT5

def opt():
    with open("/data/options.json", "r", encoding="utf-8") as f:
        return json.load(f)

CFG = opt()
URL = CFG["sendspin_url"]
MAX_HZ = max(1, min(10, int(CFG.get("max_knx_hz", 4))))
BMIN = max(0, min(100, int(CFG.get("brightness_min", 12))))
BMAX = max(BMIN, min(100, int(CFG.get("brightness_max", 65))))

def clamp(v, a, b): return max(a, min(b, v))

async def main():
    cc = ConnectionConfig(
        connection_type=ConnectionType.TUNNELING,
        gateway_ip=CFG["knx_gateway_host"],
        gateway_port=int(CFG.get("knx_gateway_port", 3671)),
    )
    xknx = XKNX(connection_config=cc)
    await xknx.start()
    hue = RemoteValueDPT5(xknx, group_address=CFG["hue_address"], dpt="5.003")
    sat = RemoteValueDPT5(xknx, group_address=CFG["saturation_address"], dpt="5.001")
    val = RemoteValueDPT5(xknx, group_address=CFG["brightness_address"], dpt="5.001")

    last_send = 0.0
    smoothed_energy = 0.0
    hue_deg = 130.0

    while True:
        try:
            print("Connecting Sendspin:", URL, flush=True)
            async with websockets.connect(URL, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                hello = {
                    "type": "client/hello",
                    "payload": {
                        "name": "KNX Mood Lights",
                        "version": "0.1.0",
                        "supported_roles": [{"role": "visualizer@v1", "formats": ["spectrum", "level", "beat"]}]
                    }
                }
                await ws.send(json.dumps(hello))
                async for raw in ws:
                    now = time.monotonic()
                    if now - last_send < 1.0 / MAX_HZ:
                        continue
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    text = json.dumps(msg.get("payload", msg)).lower()
                    payload = msg.get("payload", {})
                    # Tolerant extraction while Sendspin visualizer remains Technical Preview.
                    nums = []
                    def walk(o):
                        if isinstance(o, dict):
                            for k,v in o.items():
                                if isinstance(v,(int,float)) and any(s in k.lower() for s in ("level","loud","energy","peak","magnitude")):
                                    nums.append(float(v))
                                else: walk(v)
                        elif isinstance(o, list):
                            for v in o: walk(v)
                    walk(payload)
                    if not nums:
                        continue
                    e = max(abs(x) for x in nums)
                    if e > 1.5: e /= 100.0
                    e = clamp(e, 0.0, 1.0)
                    smoothed_energy = 0.68 * smoothed_energy + 0.32 * e
                    beat = ("beat" in text or "onset" in text or "peak" in text) and e > 0.35
                    hue_deg = (hue_deg + (28 if beat else 3 + 10*smoothed_energy)) % 360
                    saturation = clamp(70 + 25*smoothed_energy, 65, 95)
                    brightness = BMIN + (BMAX-BMIN) * math.sqrt(smoothed_energy)
                    if beat:
                        brightness = min(BMAX, brightness + 10)
                    await hue.set(hue_deg)
                    await sat.set(saturation)
                    await val.set(brightness)
                    last_send = now
        except Exception as exc:
            print("Bridge error:", repr(exc), "retrying in 5 s", flush=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())
