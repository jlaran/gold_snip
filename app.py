import uuid
from flask import Flask, request, jsonify
import threading
from telethon import TelegramClient, events
from dotenv import load_dotenv
import os
import re
import asyncio
import time
from datetime import datetime, timedelta

load_dotenv()

# === Configuración ===
api_id = int(os.getenv("TELEGRAM_API"))
api_hash = os.getenv("TELEGRAM_API_HASH")

latest_signal_gold = None

# Canales que vamos a escuchar
TELEGRAM_CHANNEL_GOLD_SNIPERS_VIP = int(os.getenv("TELEGRAM_CHANNEL_GOLD_SNIPERS_VIP"))
TELEGRAM_CHANNEL_GOLD_SNIPERS_FREE = int(os.getenv("TELEGRAM_CHANNEL_GOLD_SNIPERS_FREE"))
TELEGRAM_CHANNEL_PRUEBA = int(os.getenv("TELEGRAM_CHANNEL_PRUEBA"))

TIME_TO_EXPIRE_SIGNAL = int(os.getenv("TIME_TO_EXPIRE_SIGNAL"))

WATCHED_CHANNELS = [TELEGRAM_CHANNEL_GOLD_SNIPERS_VIP, TELEGRAM_CHANNEL_GOLD_SNIPERS_FREE, TELEGRAM_CHANNEL_PRUEBA]

# Inicializar cliente de Telethon
client_telegram = TelegramClient('server_session', api_id, api_hash)
telethon_event_loop = None

app = Flask(__name__)

# GOLD SNIPER SIGNAL

def is_gold_sniper_signal(text):
    """
    Valida si un texto es una señal del formato:

    XAUUSD SELL
    ENTRY 3425-2430
    SL 3432
    TP 3423
    TP 3420
    TP 3418
    """
    if not text or not isinstance(text, str):
        return False

    # Normaliza el texto: elimina espacios redundantes y pasa todo a mayúsculas
    text = re.sub(r'[ \t]+', ' ', text.strip().upper())

    # 1. Encabezado con símbolo y dirección
    header_match = re.search(r'\b([A-Z]{3,6})\s+(BUY|SELL)\b', text)
    if not header_match:
        return False

    # 2. ENTRY con dos precios separados por guion
    entry_match = re.search(r'\bENTRY\s+([\d\.]+)\s*-\s*([\d\.]+)', text)
    if not entry_match:
        return False

    # 3. SL (stop loss)
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return False

    # 4. Al menos un TP
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    if len(tp_matches) < 1:
        return False

    return True

def parse_gold_sniper_signal(text):
    """
    Parsea una señal con el siguiente formato:

    XAUUSD SELL
    ENTRY 3425-2430
    SL 3432
    TP 3423
    TP 3420
    TP 3418

    Retorna un diccionario con:
    - symbol: str
    - side: BUY / SELL
    - entry: list[float] (rango como [min, max])
    - sl: float
    - tps: list[float]
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip().upper()

    # 1. Encabezado
    header_match = re.search(r'\b([A-Z]{3,6})\s+(BUY|SELL)\b', text)
    if not header_match:
        return None

    symbol = header_match.group(1).strip()
    side = header_match.group(2).strip()

    # 2. ENTRY
    entry_match = re.search(r'\bENTRY\s+([\d\.]+)\s*-\s*([\d\.]+)', text)
    if not entry_match:
        return None

    try:
        entry = [float(entry_match.group(1)), float(entry_match.group(2))]
    except ValueError:
        return None

    # 3. SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None

    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # 4. TPs
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    try:
        tps = [float(tp) for tp in tp_matches]
    except ValueError:
        return None

    if not tps:
        return None

    return {
        'symbol': symbol,
        'side': side,
        'entry': entry,
        'sl': sl,
        'tps': tps
    }

# READY PARSED SIGNALS

def send_order_to_mt5(order_data):
    global latest_signal_gold

    vendor = order_data.get("vendor", "").lower()

    if vendor == "gold_snip_free":
        latest_signal_gold = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de GOLD SNIP almacenada: {order_data['symbol']} [{order_data['side']}]")
    elif vendor == "gold_snip_vip":
        latest_signal_gold = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de GOLD SNIP almacenada: {order_data['symbol']} [{order_data['side']}]")

    else:
        print("❌ Vendor desconocido en la señal:", vendor)

def format_signal_for_telegram(order_data):
    global latest_signal_gold
    
    """
    Formatea una señal de trading para enviar como mensaje de Telegram (Markdown),
    soportando distintos formatos de `order_data`.
    """
    # Extraer campos con respaldo alternativo
    symbol = order_data.get("symbol", "🆔 ACTIVO NO DEFINIDO")
    direction = order_data.get("direction") or order_data.get("side") or "🧐"
    sl = order_data.get("sl")
    tps = order_data.get("tps")
    entry = order_data.get("entry", "⏳ Esperando ejecución")
    vendor = order_data.get("vendor")

    # Armar líneas condicionalmente
    if vendor == "gold_snip_free":
        lines = ["📢 Nueva Señal de GOLD FREE CHANNEL\n"]
    elif vendor == "gold_snip_vip":
        lines = ["📢 Nueva Señal de GOLD VIP CHANNEL\n"]

    if direction and symbol:
        lines.append(f"📈 {direction} - `{symbol}`\n")
    
    # lines.append(f"🎯 Entry: `{entry}`")

    if isinstance(tps, list) and len(tps) > 0:
        for i, tp in enumerate(tps):
            lines.append(f"🎯 TP{i+1}: `{tp}`")

    if sl:
        lines.append(f"🛑 SL: `{sl}`")

    return "\n".join(lines)

# === Handler principal ===

@client_telegram.on(events.NewMessage(chats=WATCHED_CHANNELS))
async def handler(event):
    global signal_id_mrpip
    sender_id = int(event.chat_id)
    message = event.message.message

    print(f"sender: {sender_id}")
    print(f"message: {message}")

    #GOLD SNIPERS FREE CHANNEL
    if sender_id in [TELEGRAM_CHANNEL_GOLD_SNIPERS_FREE, TELEGRAM_CHANNEL_PRUEBA] and is_gold_sniper_signal(message):
        header = "📡 Señal de GOLD FREE CHANNEL Recibida con SL y TP"

        print(f"\n🪙 Señal GOLD FREE CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_gold_sniper_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "gold_snip_free"
            }
            signal_id_gold = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_gold

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA, message=f"{format_signal_for_telegram(order_data)}")
            return
    
    #GOLD SNIPERS VIP CHANNEL
    if sender_id in [TELEGRAM_CHANNEL_GOLD_SNIPERS_VIP, TELEGRAM_CHANNEL_PRUEBA] and is_gold_sniper_signal(message):
        header = "📡 Señal de GOLD VIP CHANNEL Recibida con SL y TP"

        print(f"\n🪙 Señal GOLD VIP CHANNEL detectada:\n{message}\n{'='*60}")

        signal_data = parse_gold_sniper_signal(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "gold_snip_vip"
            }
            signal_id_gold = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_gold

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA, message=f"{format_signal_for_telegram(order_data)}")
            return
    
    else:
        # if sender_id  == TELEGRAM_CHANNEL_TARGET:
            # header = "⚠️ Se recibió un mensaje de Easy Forex, pero no es una señal"
        # elif sender_id  == TELEGRAM_CHANNEL_PRUEBA:
            # header = "⚠️ Se recibió un mensaje del grupo de prueba, pero no es una señal"
        # else:
            # header = "⚠️ Se recibió un mensaje, pero no es de otro canal"
        print(f"\n📭 Mensaje ignorado de canal {sender_id}.\n{'='*60}")
    # Enviar mensaje al canal
    try:
        # await client_telegram.send_message(entity=target_channel, message=f"{header}\n\n{message}")
        await client_telegram.send_message(entity=TELEGRAM_CHANNEL_PRUEBA, message=f"{header}\n\n{message}")
        print("✅ Mensaje enviado al canal destino.")
    except Exception as e:
        print(f"❌ Error al enviar mensaje al canal: {e}")

# === Ejecutar cliente ===
def start_flask():
    port = int(os.getenv("PORT", 3000))
    print(f"🌐 Flask escuchando en puerto {port}")
    app.run(host="0.0.0.0", port=port)

def main():
    print("🚀 Bot y backend MT5 iniciando...")
    flask_thread = threading.Thread(target=start_flask)
    flask_thread.start()
    with client_telegram:
        telethon_event_loop = client_telegram.loop  # 🔥 capturamos el loop real
        client_telegram.run_until_disconnected()

@app.route("/")
def index():
    return {"status": "ok", "message": "API activa!"}

@app.route("/ping")
def ping():
    return {"status": "ok", "message": "bot activo!"}

@app.route("/mt5/gold/execute", methods=["GET"])
def get_forexpremium_signal():
    global latest_signal_gold
    if not latest_signal_gold:
        return "", 204
    
    now = datetime.utcnow()
    created = latest_signal_gold["timestamp"]
    ttl = latest_signal_gold["ttl"]

    if now - created > ttl:
        latest_signal_gold = None
        return "", 204

    return jsonify(latest_signal_gold["data"])

if __name__ == "__main__":
    main()