#!/usr/bin/env python3
"""
✈️ FlightBot Telegram - Recherche de vols pas chers
Départs: Lille, Charleroi, Bruxelles, Paris
Destinations: Marrakech, Rabat, Agadir
"""

import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)
import httpx

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "VOTRE_TOKEN_ICI")
SERPAPI_KEY    = os.getenv("SERPAPI_KEY", "VOTRE_SERPAPI_KEY_ICI")  # serpapi.com (gratuit 100 req/mois)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── DONNÉES ────────────────────────────────────────────────────────────────────
ORIGINS = {
    "LIL": "✈️ Lille (LIL)",
    "CRL": "✈️ Charleroi (CRL)",
    "BRU": "✈️ Bruxelles (BRU)",
    "CDG": "✈️ Paris CDG (CDG)",
    "ORY": "✈️ Paris Orly (ORY)",
    "ALL": "🌍 Tous les départs",
}

DESTINATIONS = {
    "RAK": "🏙️ Marrakech (RAK)",
    "RBA": "🏛️ Rabat (RBA)",
    "AGA": "🌊 Agadir (AGA)",
    "ALL": "🌍 Toutes les destinations",
}

AIRLINES_LABEL = {
    "direct":  "🚀 Sans escale uniquement",
    "any":     "🔄 Avec ou sans escale",
    "stopover":"⏱️ Avec escale acceptée",
}

# États de la conversation
(
    STATE_ORIGIN, STATE_DEST, STATE_STOPOVER,
    STATE_DATE_TYPE, STATE_DATE_FROM, STATE_DATE_TO
) = range(6)

# Session utilisateur (en mémoire, simple)
user_sessions = {}

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def build_keyboard(options: dict, cols: int = 2) -> InlineKeyboardMarkup:
    """Crée un clavier inline depuis un dict {callback_data: label}."""
    buttons = [
        InlineKeyboardButton(label, callback_data=key)
        for key, label in options.items()
    ]
    rows = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    return InlineKeyboardMarkup(rows)


async def search_flights_serpapi(origin: str, destination: str, date: str, stops: str) -> list:
    """
    Recherche de vols via SerpApi (Google Flights).
    Retourne une liste de vols triés par prix.
    """
    origins_list      = list(ORIGINS.keys())[:-1]      if origin      == "ALL" else [origin]
    destinations_list = list(DESTINATIONS.keys())[:-1] if destination == "ALL" else [destination]

    results = []

    async with httpx.AsyncClient(timeout=30) as client:
        for orig in origins_list:
            for dest in destinations_list:
                params = {
                    "engine":          "google_flights",
                    "departure_id":    orig,
                    "arrival_id":      dest,
                    "outbound_date":   date,
                    "currency":        "EUR",
                    "hl":              "fr",
                    "api_key":         SERPAPI_KEY,
                    "adults":          1,
                }
                if stops == "direct":
                    params["stops"] = "1"  # 1 = sans escale sur SerpApi

                try:
                    r = await client.get("https://serpapi.com/search", params=params)
                    data = r.json()

                    # Vols directs
                    for flight in data.get("best_flights", []) + data.get("other_flights", []):
                        for segment in flight.get("flights", []):
                            results.append({
                                "origin":      segment.get("departure_airport", {}).get("id", orig),
                                "destination": segment.get("arrival_airport", {}).get("id", dest),
                                "airline":     segment.get("airline", "?"),
                                "flight_num":  segment.get("flight_number", ""),
                                "depart":      segment.get("departure_airport", {}).get("time", date),
                                "arrive":      segment.get("arrival_airport", {}).get("time", ""),
                                "duration":    flight.get("total_duration", 0),
                                "stops":       flight.get("layovers") and len(flight["layovers"]) or 0,
                                "price":       flight.get("price", 0),
                                "type":        "direct" if not flight.get("layovers") else "escale",
                                "book_url":    data.get("search_metadata", {}).get("google_flights_url", ""),
                            })
                except Exception as e:
                    logger.error(f"SerpApi error {orig}->{dest}: {e}")

    # Filtre et tri
    if stops == "direct":
        results = [f for f in results if f["stops"] == 0]

    results.sort(key=lambda x: x["price"])
    return results[:5]


def format_flight(f: dict, rank: int) -> str:
    """Formate un vol pour l'affichage Telegram."""
    medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][rank]
    stops_label = "✅ Direct" if f["stops"] == 0 else f"🔄 {f['stops']} escale(s)"
    duration_h  = f["duration"] // 60
    duration_m  = f["duration"] % 60
    dur_str     = f"{duration_h}h{duration_m:02d}" if duration_h else f"{duration_m}min"

    origin_lbl = ORIGINS.get(f["origin"],      f["origin"])
    dest_lbl   = DESTINATIONS.get(f["destination"], f["destination"])

    return (
        f"{medal} *{f['airline']} {f['flight_num']}*\n"
        f"   {origin_lbl.split('(')[0].strip()} → {dest_lbl.split('(')[0].strip()}\n"
        f"   🕐 Départ : `{f['depart']}`  |  🕒 Arrivée : `{f['arrive']}`\n"
        f"   ⏱️ Durée : {dur_str}  |  {stops_label}\n"
        f"   💶 Prix : *{f['price']} €*\n"
    )


# ─── COMMANDES ─────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    """Démarre la recherche."""
    user_id = update.effective_user.id
    user_sessions[user_id] = {}

    text = (
        "✈️ *FlightBot* — Recherche de vols pas chers\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "Départs : Lille, Charleroi, Bruxelles, Paris\n"
        "Destinations : Marrakech, Rabat, Agadir\n\n"
        "👇 *Choisissez votre aéroport de départ :*"
    )

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=build_keyboard(ORIGINS, cols=2)
    )
    return STATE_ORIGIN


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Aide FlightBot*\n\n"
        "• /search — Lancer une recherche de vol\n"
        "• /start  — Recommencer\n"
        "• /help   — Cette aide\n\n"
        "💡 *Astuces :*\n"
        "  – Choisissez `ALL` pour chercher depuis tous les aéroports\n"
        "  – Les 5 vols les moins chers sont affichés\n"
        "  – Compagnies low-cost ET régulières incluses\n"
        "    (Ryanair, Transavia, TUIfly, Royal Air Maroc, etc.)"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─── CONVERSATION ──────────────────────────────────────────────────────────────

async def cb_origin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    user_sessions[user_id]["origin"] = q.data

    label = ORIGINS[q.data]
    await q.edit_message_text(
        f"✅ Départ : *{label}*\n\n👇 *Choisissez votre destination :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard(DESTINATIONS, cols=2)
    )
    return STATE_DEST


async def cb_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    user_sessions[user_id]["destination"] = q.data

    label = DESTINATIONS[q.data]
    await q.edit_message_text(
        f"✅ Destination : *{label}*\n\n👇 *Préférence d'escales :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard({
            "direct":   "🚀 Sans escale uniquement",
            "any":      "🔄 Peu importe",
            "stopover": "⏱️ Avec escale OK",
        }, cols=1)
    )
    return STATE_STOPOVER


async def cb_stopover(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    user_sessions[user_id]["stops"] = q.data

    await q.edit_message_text(
        "📅 *Choisissez une période de recherche :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard({
            "7":  "📅 Dans les 7 prochains jours",
            "14": "📅 Dans les 14 prochains jours",
            "30": "📅 Dans les 30 prochains jours",
            "90": "📅 Dans les 3 prochains mois",
        }, cols=2)
    )
    return STATE_DATE_TYPE


async def cb_date_type(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    user_id = q.from_user.id
    days = int(q.data)
    user_sessions[user_id]["days"] = days

    session = user_sessions[user_id]
    origin  = session["origin"]
    dest    = session["destination"]
    stops   = session["stops"]

    orig_lbl = ORIGINS[origin]
    dest_lbl = DESTINATIONS[dest]
    stop_lbl = AIRLINES_LABEL[stops]

    await q.edit_message_text(
        f"🔍 *Recherche en cours...*\n\n"
        f"   ✈️ {orig_lbl} → {dest_lbl}\n"
        f"   {stop_lbl}\n"
        f"   📅 {days} prochains jours\n\n"
        f"_Cela peut prendre quelques secondes..._",
        parse_mode="Markdown"
    )

    # Chercher sur plusieurs dates
    today   = datetime.today()
    best    = []
    checked = set()

    for delta in range(1, days + 1):
        date_str = (today + timedelta(days=delta)).strftime("%Y-%m-%d")
        flights  = await search_flights_serpapi(origin, dest, date_str, stops)
        for f in flights:
            key = (f["airline"], f["flight_num"], f["depart"])
            if key not in checked:
                checked.add(key)
                best.append(f)

    best.sort(key=lambda x: x["price"])
    best = best[:5]

    if not best:
        await q.message.reply_text(
            "😕 *Aucun vol trouvé* pour ces critères.\n\n"
            "Essayez avec des dates plus larges ou `ALL` pour l'aéroport.\n\n"
            "/search pour relancer.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    # Affichage résultats
    header = (
        f"✈️ *Top 5 vols les moins chers*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"   {orig_lbl} → {dest_lbl}\n"
        f"   {stop_lbl}\n"
        f"   📅 {days} prochains jours\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )

    body = "\n".join(format_flight(f, i) for i, f in enumerate(best))

    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "💡 Prix indicatifs — vérifiez sur le site de la compagnie.\n"
        "/search pour une nouvelle recherche."
    )

    # Bouton vers Google Flights
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 Voir sur Google Flights", url=best[0].get("book_url", "https://www.google.com/flights"))
    ],[
        InlineKeyboardButton("🔄 Nouvelle recherche", callback_data="restart")
    ]])

    await q.message.reply_text(
        header + body + footer,
        parse_mode="Markdown",
        reply_markup=keyboard,
        disable_web_page_preview=True
    )
    return ConversationHandler.END


async def cb_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query
    await q.answer()
    # Simule /search
    user_id = q.from_user.id
    user_sessions[user_id] = {}

    await q.message.reply_text(
        "✈️ *Nouvelle recherche*\n\n👇 *Choisissez votre aéroport de départ :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard(ORIGINS, cols=2)
    )
    return STATE_ORIGIN


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Recherche annulée. /search pour recommencer.")
    return ConversationHandler.END


# ─── MAIN ───────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[
            CommandHandler("start",  start),
            CommandHandler("search", start),
        ],
        states={
            STATE_ORIGIN:    [CallbackQueryHandler(cb_origin,    pattern="^(LIL|CRL|BRU|CDG|ORY|ALL)$")],
            STATE_DEST:      [CallbackQueryHandler(cb_dest,      pattern="^(RAK|RBA|AGA|ALL)$")],
            STATE_STOPOVER:  [CallbackQueryHandler(cb_stopover,  pattern="^(direct|any|stopover)$")],
            STATE_DATE_TYPE: [CallbackQueryHandler(cb_date_type, pattern="^(7|14|30|90)$")],
        },
        fallbacks=[
            CommandHandler("cancel", cancel),
            CallbackQueryHandler(cb_restart, pattern="^restart$"),
        ],
        allow_reentry=True,
    )

    app.add_handler(conv)
    app.add_handler(CommandHandler("help", help_cmd))

    logger.info("🤖 FlightBot démarré !")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
