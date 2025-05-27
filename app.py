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

latest_signal_easy_forex_long = None
latest_signal_easy_forex_vip = None

# Canales que vamos a escuchar
TELEGRAM_CHANNEL_EASY_FOREX_LONG = int(os.getenv("TELEGRAM_CHANNEL_EASY_FOREX_LONG"))
TELEGRAM_CHANNEL_EASY_FOREX_VIP = int(os.getenv("TELEGRAM_CHANNEL_EASY_FOREX_VIP"))
TELEGRAM_CHANNEL_TARGET = int(os.getenv("TELEGRAM_TARGET_CHANNEL"))

TIME_TO_EXPIRE_SIGNAL = int(os.getenv("TIME_TO_EXPIRE_SIGNAL"))

WATCHED_CHANNELS = [TELEGRAM_CHANNEL_TARGET, TELEGRAM_CHANNEL_EASY_FOREX_LONG, TELEGRAM_CHANNEL_EASY_FOREX_VIP]

# Inicializar cliente de Telethon
client_telegram = TelegramClient('server_session', api_id, api_hash)
telethon_event_loop = None

app = Flask(__name__)

# EASY FOREX LONG

def is_easy_forex_signal_long(text):
    """
    Valida si un texto es una señal estructurada del tipo:

    NZDUSD SELL
    ENTRY @ 0.60136
    SL: 0.60494 (-30) pips
    TP1: 0.59831 (+30) pips
    TP2: 0.59436 (+70) pips
    TP3: 0.59081 (+110) pips
    """
    if not text or not isinstance(text, str):
        return False

    text = text.strip().upper()

    # 1. Buscar encabezado con par y tipo de operación
    header_match = re.search(r'\b([A-Z]{6})\s+(BUY|SELL)\b', text)
    if not header_match:
        return False

    # 2. Buscar línea de ENTRY @ precio
    entry_match = re.search(r'\bENTRY\s*@\s*([\d\.]+)', text)
    if not entry_match:
        return False

    # 3. Buscar SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return False

    # 4. Buscar al menos un TP (TP o TP1, TP2, etc.)
    tp_matches = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    if len(tp_matches) < 1:
        return False

    return True

def parse_easy_forex_signal_long(text):
    """
    Parsea una señal estructurada con el formato:
    
    SYMBOL SELL
    ENTRY @ 0.60136
    SL: 0.60494
    TP1: 0.59831
    TP2: 0.59436
    TP3: 0.59081

    Retorna:
        {
            'symbol': str,
            'side': 'BUY' or 'SELL',
            'entry': float,
            'sl': float,
            'tps': list[float]
        }
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip().upper()

    # 1. Encabezado: símbolo y dirección
    header_match = re.search(r'\b([A-Z]{6})\s+(BUY|SELL)\b', text)
    if not header_match:
        return None

    symbol = header_match.group(1)
    side = header_match.group(2)

    # 2. Entrada
    entry_match = re.search(r'\bENTRY\s*@\s*([\d\.]+)', text)
    if not entry_match:
        return None

    try:
        entry = float(entry_match.group(1))
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

# EASY FOREX VIP

def is_easy_forex_vip(text):
    """
    Valida si el texto contiene una señal del tipo:
    SYMBOL BUY/SELL @ precio / precio

    Debe contener:
    - Encabezado con símbolo y dirección
    - Entrada doble separada por "/"
    - Al menos un TP
    - SL
    """
    if not text or not isinstance(text, str):
        return False

    text = text.strip().upper()

    # 1. Encabezado y doble entrada
    header = re.search(r'\b([A-Z]{6})\s+(BUY|SELL)\s*@\s*([\d\.]+)\s*/\s*([\d\.]+)', text)
    if not header:
        return False

    # 2. SL
    sl = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl:
        return False

    # 3. TPs
    tps = re.findall(r'\bTP\d*\s*[:=]?\s*([\d\.]+)', text)
    if len(tps) < 1:
        return False

    return True

def parse_easy_forex_vip(text):
    """
    Parsea señales tipo:
    AUDUSD SELL @ 0.6528 / 0.6521
    TP: 0.6508
    TP: 0.6478
    SL: 0.6598

    Retorna:
        {
            'symbol': str,
            'side': 'BUY' or 'SELL',
            'entry': list[float],
            'sl': float,
            'tps': list[float]
        }
    """
    if not text or not isinstance(text, str):
        return None

    text = text.strip().upper()

    # Encabezado con doble entry
    match = re.search(r'\b([A-Z]{6})\s+(BUY|SELL)\s*@\s*([\d\.]+)\s*/\s*([\d\.]+)', text)
    if not match:
        return None

    symbol = match.group(1)
    side = match.group(2)

    try:
        entry1 = float(match.group(3))
        entry2 = float(match.group(4))
        entry = [entry1, entry2]
    except ValueError:
        return None

    # SL
    sl_match = re.search(r'\bSL\s*[:=]?\s*([\d\.]+)', text)
    if not sl_match:
        return None
    try:
        sl = float(sl_match.group(1))
    except ValueError:
        return None

    # TPs
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
    global latest_signal_easy_forex_long, latest_signal_easy_forex_vip

    vendor = order_data.get("vendor", "").lower()

    if vendor == "easy_forex_long":
        latest_signal_easy_forex_long = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de Easy Forex Long almacenada: {order_data['symbol']} [{order_data['side']}]")

    elif vendor == "easy_forex_vip":
        latest_signal_easy_forex_vip = {
            "data": order_data,
            "timestamp": datetime.utcnow(),
            "ttl": timedelta(seconds=TIME_TO_EXPIRE_SIGNAL)
        }
        print(f"📤 Señal de Easy Forex VIP almacenada: {order_data['symbol']} [{order_data['side']}]")

    else:
        print("❌ Vendor desconocido en la señal:", vendor)

def format_signal_for_telegram(order_data):
    global latest_signal_mrpip
    
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
    if vendor == "easy_forex_long":
        lines = ["📢 Nueva Señal de Easy Forex Long\n"]
    elif vendor == "easy_forex_vip":
        lines = ["📢 Nueva Señal de Easy Forex VIP\n"]

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

    #CHANNEL_CRYPTO
    if sender_id in [TELEGRAM_CHANNEL_TARGET, TELEGRAM_CHANNEL_EASY_FOREX_LONG] and is_easy_forex_signal_long(message):
        header = "📡 Señal de EASY Forex Long Recibida con SL y TP"

        print(f"\n🪙 Señal EASY Forex Long detectada:\n{message}\n{'='*60}")

        signal_data = parse_easy_forex_signal_long(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "easy_forex_long"
            }
            signal_id_forex = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_forex

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_TARGET, message=f"{format_signal_for_telegram(order_data)}")
            return
    
    elif sender_id in [TELEGRAM_CHANNEL_TARGET, TELEGRAM_CHANNEL_EASY_FOREX_VIP] and is_easy_forex_vip(message):
        header = "📡 Señal de EASY Forex VIP Recibida con SL y TP"

        print(f"\n🪙 Señal EASY Forex VIP detectada:\n{message}\n{'='*60}")

        signal_data = parse_easy_forex_vip(message)
        if signal_data:
            order_data = {
                "symbol": signal_data['symbol'],         # Ej: "CRASH 1000 INDEX"
                "side": signal_data['side'],   # "BUY" o "SELL"
                "sl": signal_data['sl'],
                "tps": signal_data['tps'],
                "vendor": "easy_forex_vip"
            }
            signal_id_forex = str(uuid.uuid4())
            order_data['signal_id'] = signal_id_forex

            send_order_to_mt5(order_data)
            print(signal_data)
            await client_telegram.send_message(entity=TELEGRAM_CHANNEL_TARGET, message=f"{format_signal_for_telegram(order_data)}")
            return
    else:
        if sender_id  == TELEGRAM_CHANNEL_EASY_FOREX_LONG:
            header = "⚠️ Se recibió un mensaje de Easy Forex, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_EASY_FOREX_VIP:
            header = "⚠️ Se recibió un mensaje del grupo, pero no es una señal"
        elif sender_id  == TELEGRAM_CHANNEL_TARGET:
            header = "⚠️ Se recibió un mensaje del grupo, pero no es una señal"
        else:
            header = "⚠️ Se recibió un mensaje, pero no es de otro canal"
        
        print(f"\n📭 Mensaje ignorado de canal {sender_id}.\n{'='*60}")
        
    # Enviar mensaje al canal
    try:
        # await client_telegram.send_message(entity=target_channel, message=f"{header}\n\n{message}")
        await client_telegram.send_message(entity=TELEGRAM_CHANNEL_TARGET, message=f"{header}\n\n{message}")
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

@app.route("/mt5/forexlong/execute", methods=["GET"])
def get_forexpremium_signal():
    global latest_signal_forexpremim
    if not latest_signal_forexpremim:
        return "", 204
    
    now = datetime.utcnow()
    created = latest_signal_forexpremim["timestamp"]
    ttl = latest_signal_forexpremim["ttl"]

    if now - created > ttl:
        latest_signal_forexpremim = None
        return "", 204

    return jsonify(latest_signal_forexpremim["data"])

if __name__ == "__main__":
    main()