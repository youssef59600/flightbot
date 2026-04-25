#!/usr/bin/env python3
"""
✈️ FlightBot Telegram - Recherche de vols pas chers
Départs: Lille, Charleroi, Bruxelles, Paris
Destinations: Marrakech, Rabat, Agadir
API: Aviationstack (500 req/mois gratuit) + fallback données statiques
"""

import os
import logging
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, ConversationHandler
)
import httpx

# ─── CONFIG ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN    = os.getenv("TELEGRAM_TOKEN", "VOTRE_TOKEN_ICI")
AVIATIONSTACK_KEY = os.getenv("AVIATIONSTACK_KEY", "")  # aviationstack.com gratuit

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ─── DONNÉES ───────────────────────────────────────────────────────────────────
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

STOPS_LABEL = {
    "direct":   "🚀 Sans escale uniquement",
    "any":      "🔄 Avec ou sans escale",
    "stopover": "⏱️ Avec escale OK",
}

# Base de vols connues sur ces routes (prix de base en €)
KNOWN_FLIGHTS = [
    # (orig, dest, compagnie, num_vol, dep, arr, duree_min, prix_base, nb_escales)
    ("LIL", "RAK", "Transavia",      "TO3501", "07:00", "11:15", 255,  89, 0),
    ("LIL", "RAK", "Ryanair",        "FR8821", "06:30", "10:45", 255,  74, 0),
    ("LIL", "RAK", "TUIfly",         "TB1234", "08:00", "12:10", 250, 112, 0),
    ("LIL", "RAK", "Air France",     "AF1801", "06:00", "13:30", 450, 145, 1),
    ("LIL", "AGA", "Air France",     "AF1900", "06:00", "14:30", 510, 178, 1),
    ("CRL", "RAK", "Ryanair",        "FR9901", "07:15", "11:30", 255,  58, 0),
    ("CRL", "RAK", "TUIfly",         "TB5678", "09:00", "13:10", 250,  98, 0),
    ("CRL", "RAK", "Transavia",      "HV6100", "06:45", "11:00", 255,  79, 0),
    ("CRL", "RBA", "Ryanair",        "FR9902", "08:00", "12:00", 240,  62, 0),
    ("CRL", "AGA", "Ryanair",        "FR9903", "10:00", "14:30", 270,  55, 0),
    ("BRU", "RAK", "Royal Air Maroc","AT700",  "10:00", "13:45", 225, 135, 0),
    ("BRU", "RAK", "Ryanair",        "FR1100", "06:00", "10:15", 255,  69, 0),
    ("BRU", "RBA", "Royal Air Maroc","AT702",  "11:00", "14:30", 210, 148, 0),
    ("BRU", "RBA", "Lufthansa",      "LH1234", "07:00", "15:00", 480, 165, 1),
    ("BRU", "AGA", "Transavia",      "HV6200", "07:30", "12:00", 270,  95, 0),
    ("CDG", "RAK", "Royal Air Maroc","AT700",  "09:30", "13:10", 220, 119, 0),
    ("CDG", "RAK", "Air France",     "AF1400", "07:00", "10:45", 225, 142, 0),
    ("CDG", "RAK", "Transavia",      "TO1501", "06:30", "10:15", 225,  88, 0),
    ("CDG", "RBA", "Royal Air Maroc","AT710",  "10:00", "13:30", 210, 125, 0),
    ("CDG", "AGA", "Royal Air Maroc","AT720",  "08:00", "12:30", 270, 132, 0),
    ("CDG", "AGA", "Transavia",      "TO1601", "07:15", "11:45", 270,  92, 0),
    ("ORY", "RAK", "Transavia",      "TO3601", "08:00", "11:45", 225,  79, 0),
    ("ORY", "RAK", "Air Arabia",     "3O700",  "07:30", "11:15", 225,  68, 0),
    ("ORY", "RBA", "Transavia",      "TO3701", "09:00", "12:30", 210,  83, 0),
    ("ORY", "AGA", "Transavia",      "TO3801", "06:45", "11:15", 270,  86, 0),
]

# États conversation
STATE_ORIGIN, STATE_DEST, STATE_STOPOVER, STATE_DATE, STATE_NB_RESULTS = range(5)
DEFAULT_NB_RESULTS = 7
user_sessions: dict = {}

# ─── HELPERS ───────────────────────────────────────────────────────────────────

def build_keyboard(options: dict, cols: int = 2) -> InlineKeyboardMarkup:
    buttons = [InlineKeyboardButton(label, callback_data=key) for key, label in options.items()]
    rows    = [buttons[i:i+cols] for i in range(0, len(buttons), cols)]
    return InlineKeyboardMarkup(rows)


async def search_flights_api(origin: str, dest: str, date_str: str) -> list:
    """Tente Aviationstack si clé disponible."""
    if not AVIATIONSTACK_KEY:
        return []
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                "http://api.aviationstack.com/v1/flights",
                params={
                    "access_key":    AVIATIONSTACK_KEY,
                    "dep_iata":      origin,
                    "arr_iata":      dest,
                    "flight_date":   date_str,
                    "flight_status": "scheduled",
                    "limit":         10,
                }
            )
            data = r.json()
            flights = data.get("data") or []
            results = []
            for f in flights:
                dep     = f.get("departure", {})
                arr     = f.get("arrival", {})
                airline = f.get("airline", {}).get("name", "?")
                fnum    = f.get("flight", {}).get("iata", "")
                dep_t   = (dep.get("scheduled") or "")[:16].replace("T", " ")
                arr_t   = (arr.get("scheduled") or "")[:16].replace("T", " ")
                results.append({
                    "origin": origin, "destination": dest,
                    "airline": airline, "flight_num": fnum,
                    "depart": dep_t, "arrive": arr_t,
                    "duration": 0, "stops": 0, "price": 0,
                    "book_url": f"https://www.google.com/flights?q=flights+{origin}+to+{dest}+{date_str}",
                })
            return results
    except Exception as e:
        logger.error(f"API error: {e}")
        return []


def get_static_flights(origin: str, dest: str, date_str: str, stops: str) -> list:
    """Fallback : base de données avec prix simulés réalistes."""
    origins_list = list(ORIGINS.keys())[:-1] if origin == "ALL" else [origin]
    dests_list   = list(DESTINATIONS.keys())[:-1] if dest == "ALL" else [dest]

    try:
        dt = datetime.strptime(date_str, "%Y-%m-%d")
    except Exception:
        dt = datetime.today()

    random.seed(date_str)
    factor = random.uniform(0.85, 1.40)
    if dt.weekday() in (4, 5, 6):  # weekend = plus cher
        factor *= 1.10

    results = []
    for (orig, dst, airline, fnum, dep_t, arr_t, dur, price_base, n_stops) in KNOWN_FLIGHTS:
        if orig not in origins_list or dst not in dests_list:
            continue
        if stops == "direct" and n_stops > 0:
            continue
        price = round(price_base * factor / 5) * 5
        results.append({
            "origin": orig, "destination": dst,
            "airline": airline, "flight_num": fnum,
            "depart": f"{date_str} {dep_t}", "arrive": f"{date_str} {arr_t}",
            "duration": dur, "stops": n_stops, "price": price,
            "book_url": f"https://www.google.com/flights?q=flights+{orig}+to+{dst}+{date_str}",
        })
    return results


async def search_best(origin: str, dest: str, days: int, stops: str, nb_results: int = DEFAULT_NB_RESULTS) -> list:
    today  = datetime.today()
    all_f  = []
    seen   = set()

    origins_list = list(ORIGINS.keys())[:-1] if origin == "ALL" else [origin]
    dests_list   = list(DESTINATIONS.keys())[:-1] if dest == "ALL" else [dest]

    for delta in range(1, days + 1):
        date_str = (today + timedelta(days=delta)).strftime("%Y-%m-%d")

        api_results = []
        for orig in origins_list:
            for dst in dests_list:
                api_results += await search_flights_api(orig, dst, date_str)

        source = api_results if api_results else get_static_flights(origin, dest, date_str, stops)

        for f in source:
            key = (f["airline"], f["flight_num"], date_str)
            if key not in seen:
                seen.add(key)
                all_f.append(f)

    with_price    = sorted([f for f in all_f if f["price"] > 0], key=lambda x: x["price"])
    without_price = [f for f in all_f if f["price"] == 0]
    return (with_price + without_price)[:nb_results]


def format_flight(f: dict, rank: int) -> str:
    medals   = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    medal    = medals[rank] if rank < 5 else "▪️"
    s_label  = "✅ Direct" if f["stops"] == 0 else f"🔄 {f['stops']} escale(s)"
    dur_h, dur_m = divmod(f["duration"], 60)
    dur_str  = f"{dur_h}h{dur_m:02d}" if dur_h else ""
    orig_lbl = ORIGINS.get(f["origin"], f["origin"]).split("(")[0].strip()
    dest_lbl = DESTINATIONS.get(f["destination"], f["destination"]).split("(")[0].strip()
    price_s  = f"*{f['price']} €*" if f["price"] > 0 else "_voir lien_"
    dep_s    = f["depart"][11:16] if len(f["depart"]) > 10 else f["depart"]
    arr_s    = f["arrive"][11:16] if len(f["arrive"]) > 10 else f["arrive"]
    date_s   = f["depart"][:10]

    line = (
        f"{medal} *{f['airline']} {f['flight_num']}*\n"
        f"   {orig_lbl} → {dest_lbl}  |  📅 `{date_s}`\n"
        f"   🕐 `{dep_s}` → `{arr_s}`"
    )
    if dur_str:
        line += f"  ⏱️ {dur_str}"
    line += f"\n   {s_label}  |  💶 {price_s}\n"
    return line


# ─── CONVERSATION ──────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    user_sessions[update.effective_user.id] = {}
    await update.message.reply_text(
        "✈️ *FlightBot* — Vols pas chers vers le Maghreb\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
        "👇 *Aéroport de départ :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard(ORIGINS, cols=2)
    )
    return STATE_ORIGIN


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📖 *FlightBot — Aide*\n\n"
        "• /search — Nouvelle recherche\n"
        "• /cancel — Annuler\n\n"
        "Compagnies : Ryanair, Transavia, TUIfly,\n"
        "Royal Air Maroc, Air France, Air Arabia…\n\n"
        "💡 Choisissez `ALL` pour tout chercher d'un coup.",
        parse_mode="Markdown"
    )


async def cb_origin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user_sessions[q.from_user.id]["origin"] = q.data
    await q.edit_message_text(
        f"✅ Départ : *{ORIGINS[q.data]}*\n\n👇 *Destination :*",
        parse_mode="Markdown", reply_markup=build_keyboard(DESTINATIONS, cols=2)
    )
    return STATE_DEST


async def cb_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user_sessions[q.from_user.id]["destination"] = q.data
    await q.edit_message_text(
        f"✅ Destination : *{DESTINATIONS[q.data]}*\n\n👇 *Escales :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard({"direct":"🚀 Sans escale uniquement","any":"🔄 Peu importe","stopover":"⏱️ Avec escale OK"}, cols=1)
    )
    return STATE_STOPOVER


async def cb_stopover(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user_sessions[q.from_user.id]["stops"] = q.data
    await q.edit_message_text(
        "📅 *Période de recherche :*",
        parse_mode="Markdown",
        reply_markup=build_keyboard({"7":"📅 7 jours","14":"📅 14 jours","30":"📅 30 jours","90":"📅 3 mois"}, cols=2)
    )
    return STATE_DATE


async def cb_date(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    uid  = q.from_user.id
    days = int(q.data)
    user_sessions[uid]["days"] = days
    await q.edit_message_text(
        f"✅ Période : *{days} jours*\n\n"
        f"📊 *Nombre de résultats à afficher :*\n"
        f"_(défaut : {DEFAULT_NB_RESULTS})_",
        parse_mode="Markdown",
        reply_markup=build_keyboard({
            "r3":  "3️⃣  3 résultats",
            "r5":  "5️⃣  5 résultats",
            "r7":  f"7️⃣  7 résultats ✅ défaut",
            "r10": "🔟 10 résultats",
        }, cols=2)
    )
    return STATE_NB_RESULTS


async def cb_nb_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    uid = q.from_user.id
    nb  = int(q.data.replace("r", ""))
    s   = user_sessions[uid]
    s["nb_results"] = nb

    await q.edit_message_text(
        f"🔍 *Recherche en cours…*\n\n"
        f"   {ORIGINS[s['origin']]} → {DESTINATIONS[s['destination']]}\n"
        f"   {STOPS_LABEL[s['stops']]}  |  {s['days']} jours  |  top {nb}\n\n"
        f"_Patientez quelques secondes…_",
        parse_mode="Markdown"
    )

    flights = await search_best(s["origin"], s["destination"], s["days"], s["stops"], nb)

    if not flights:
        await q.message.reply_text(
            "😕 *Aucun vol trouvé.*\n\nEssayez avec `ALL` ou une période plus longue.\n/search pour recommencer.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    header = (
        f"✈️ *Top {len(flights)} vols les moins chers*\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"{ORIGINS[s['origin']]} → {DESTINATIONS[s['destination']]}\n"
        f"{STOPS_LABEL[s['stops']]}  |  {s['days']} jours\n"
        f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
    )
    body   = "\n".join(format_flight(f, i) for i, f in enumerate(flights))
    footer = (
        "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "⚠️ _Prix indicatifs — confirmez sur le site compagnie._\n"
        "/search pour une nouvelle recherche."
    )

    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton("🔗 Voir sur Google Flights", url=flights[0].get("book_url","https://www.google.com/flights"))
    ],[
        InlineKeyboardButton("🔄 Nouvelle recherche", callback_data="restart")
    ]])

    await q.message.reply_text(header + body + footer, parse_mode="Markdown",
                               reply_markup=keyboard, disable_web_page_preview=True)
    return ConversationHandler.END


async def cb_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    q = update.callback_query; await q.answer()
    user_sessions[q.from_user.id] = {}
    await q.message.reply_text(
        "✈️ *Nouvelle recherche*\n\n👇 *Aéroport de départ :*",
        parse_mode="Markdown", reply_markup=build_keyboard(ORIGINS, cols=2)
    )
    return STATE_ORIGIN


async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("❌ Annulé. /search pour recommencer.")
    return ConversationHandler.END


# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start), CommandHandler("search", start)],
        states={
            STATE_ORIGIN:     [CallbackQueryHandler(cb_origin,     pattern="^(LIL|CRL|BRU|CDG|ORY|ALL)$")],
            STATE_DEST:       [CallbackQueryHandler(cb_dest,       pattern="^(RAK|RBA|AGA|ALL)$")],
            STATE_STOPOVER:   [CallbackQueryHandler(cb_stopover,   pattern="^(direct|any|stopover)$")],
            STATE_DATE:       [CallbackQueryHandler(cb_date,       pattern="^(7|14|30|90)$")],
            STATE_NB_RESULTS: [CallbackQueryHandler(cb_nb_results, pattern="^r(3|5|7|10)$")],
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
    # drop_pending_updates=True corrige l'erreur Conflict
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
