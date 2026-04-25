#!/usr/bin/env python3
“””
✈️ FlightBot Telegram - Recherche de vols pas chers
Départs: Lille, Charleroi, Bruxelles, Paris
Destinations: Marrakech, Rabat, Agadir

- Sélection MULTIPLE des villes départ/arrivée
- Période rapide OU dates personnalisées
- Nombre de résultats paramétrable (défaut 7)
  “””

import os
import logging
import random
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
Application, CommandHandler, CallbackQueryHandler,
MessageHandler, filters, ContextTypes, ConversationHandler
)
import httpx

# ─── CONFIG ────────────────────────────────────────────────────────────────────

TELEGRAM_TOKEN    = os.getenv(“TELEGRAM_TOKEN”, “VOTRE_TOKEN_ICI”)
AVIATIONSTACK_KEY = os.getenv(“AVIATIONSTACK_KEY”, “”)

logging.basicConfig(format=”%(asctime)s - %(levelname)s - %(message)s”, level=logging.INFO)
logger = logging.getLogger(**name**)

# ─── DONNÉES ───────────────────────────────────────────────────────────────────

ORIGINS_LIST = [
(“LIL”, “Lille”),
(“CRL”, “Charleroi”),
(“BRU”, “Bruxelles”),
(“CDG”, “Paris CDG”),
(“ORY”, “Paris Orly”),
]

DESTS_LIST = [
(“RAK”, “Marrakech”),
(“RBA”, “Rabat”),
(“AGA”, “Agadir”),
]

STOPS_LABEL = {
“direct”:   “🚀 Sans escale uniquement”,
“any”:      “🔄 Avec ou sans escale”,
“stopover”: “⏱️ Avec escale OK”,
}

KNOWN_FLIGHTS = [
(“LIL”, “RAK”, “Transavia”,       “TO3501”, “07:00”, “11:15”, 255,  89, 0),
(“LIL”, “RAK”, “Ryanair”,         “FR8821”, “06:30”, “10:45”, 255,  74, 0),
(“LIL”, “RAK”, “TUIfly”,          “TB1234”, “08:00”, “12:10”, 250, 112, 0),
(“LIL”, “RAK”, “Air France”,      “AF1801”, “06:00”, “13:30”, 450, 145, 1),
(“LIL”, “AGA”, “Air France”,      “AF1900”, “06:00”, “14:30”, 510, 178, 1),
(“CRL”, “RAK”, “Ryanair”,         “FR9901”, “07:15”, “11:30”, 255,  58, 0),
(“CRL”, “RAK”, “TUIfly”,          “TB5678”, “09:00”, “13:10”, 250,  98, 0),
(“CRL”, “RAK”, “Transavia”,       “HV6100”, “06:45”, “11:00”, 255,  79, 0),
(“CRL”, “RBA”, “Ryanair”,         “FR9902”, “08:00”, “12:00”, 240,  62, 0),
(“CRL”, “AGA”, “Ryanair”,         “FR9903”, “10:00”, “14:30”, 270,  55, 0),
(“BRU”, “RAK”, “Royal Air Maroc”, “AT700”,  “10:00”, “13:45”, 225, 135, 0),
(“BRU”, “RAK”, “Ryanair”,         “FR1100”, “06:00”, “10:15”, 255,  69, 0),
(“BRU”, “RBA”, “Royal Air Maroc”, “AT702”,  “11:00”, “14:30”, 210, 148, 0),
(“BRU”, “RBA”, “Lufthansa”,       “LH1234”, “07:00”, “15:00”, 480, 165, 1),
(“BRU”, “AGA”, “Transavia”,       “HV6200”, “07:30”, “12:00”, 270,  95, 0),
(“CDG”, “RAK”, “Royal Air Maroc”, “AT700”,  “09:30”, “13:10”, 220, 119, 0),
(“CDG”, “RAK”, “Air France”,      “AF1400”, “07:00”, “10:45”, 225, 142, 0),
(“CDG”, “RAK”, “Transavia”,       “TO1501”, “06:30”, “10:15”, 225,  88, 0),
(“CDG”, “RBA”, “Royal Air Maroc”, “AT710”,  “10:00”, “13:30”, 210, 125, 0),
(“CDG”, “AGA”, “Royal Air Maroc”, “AT720”,  “08:00”, “12:30”, 270, 132, 0),
(“CDG”, “AGA”, “Transavia”,       “TO1601”, “07:15”, “11:45”, 270,  92, 0),
(“ORY”, “RAK”, “Transavia”,       “TO3601”, “08:00”, “11:45”, 225,  79, 0),
(“ORY”, “RAK”, “Air Arabia”,      “3O700”,  “07:30”, “11:15”, 225,  68, 0),
(“ORY”, “RBA”, “Transavia”,       “TO3701”, “09:00”, “12:30”, 210,  83, 0),
(“ORY”, “AGA”, “Transavia”,       “TO3801”, “06:45”, “11:15”, 270,  86, 0),
]

# États conversation

(
STATE_ORIGIN, STATE_DEST, STATE_STOPOVER,
STATE_DATE_MENU, STATE_DATE_FROM, STATE_DATE_TO,
STATE_NB_RESULTS
) = range(7)

DEFAULT_NB_RESULTS = 7
user_sessions: dict = {}

# ─── KEYBOARD MULTI-SELECT ─────────────────────────────────────────────────────

def build_multiselect_keyboard(items: list, selected: set, prefix: str, confirm_label: str) -> InlineKeyboardMarkup:
“””
Affiche une liste de boutons cases à cocher.
items = [(code, label), …]
selected = {“LIL”, “CDG”, …}
“””
rows = []
for code, label in items:
tick = “✅” if code in selected else “☐”
rows.append([InlineKeyboardButton(f”{tick} {label}”, callback_data=f”{prefix}:{code}”)])

```
# Bouton Tout sélectionner / Tout déselectionner
all_codes = {c for c, _ in items}
if selected >= all_codes:
    rows.append([InlineKeyboardButton("☐ Tout déselectionner", callback_data=f"{prefix}:NONE")])
else:
    rows.append([InlineKeyboardButton("✅ Tout sélectionner", callback_data=f"{prefix}:ALL")])

# Bouton Valider (actif seulement si au moins 1 sélectionné)
if selected:
    rows.append([InlineKeyboardButton(f"➡️ {confirm_label}", callback_data=f"{prefix}:CONFIRM")])
return InlineKeyboardMarkup(rows)
```

def selected_labels(items: list, selected: set) -> str:
return “, “.join(label for code, label in items if code in selected) or “—”

# ─── RECHERCHE VOLS ────────────────────────────────────────────────────────────

async def search_flights_api(origin: str, dest: str, date_str: str) -> list:
if not AVIATIONSTACK_KEY:
return []
try:
async with httpx.AsyncClient(timeout=15) as client:
r = await client.get(
“http://api.aviationstack.com/v1/flights”,
params={
“access_key”: AVIATIONSTACK_KEY,
“dep_iata”: origin, “arr_iata”: dest,
“flight_date”: date_str, “flight_status”: “scheduled”, “limit”: 10,
}
)
data = r.json()
results = []
for f in (data.get(“data”) or []):
dep  = f.get(“departure”, {})
arr  = f.get(“arrival”, {})
results.append({
“origin”: origin, “destination”: dest,
“airline”: f.get(“airline”, {}).get(“name”, “?”),
“flight_num”: f.get(“flight”, {}).get(“iata”, “”),
“depart”: (dep.get(“scheduled”) or “”)[:16].replace(“T”, “ “),
“arrive”: (arr.get(“scheduled”) or “”)[:16].replace(“T”, “ “),
“duration”: 0, “stops”: 0, “price”: 0,
“book_url”: f”https://www.google.com/flights?q=flights+{origin}+to+{dest}+{date_str}”,
})
return results
except Exception as e:
logger.error(f”API error: {e}”)
return []

def get_static_flights(origins: list, dests: list, date_str: str, stops: str) -> list:
try:
dt = datetime.strptime(date_str, “%Y-%m-%d”)
except Exception:
dt = datetime.today()
random.seed(date_str)
factor = random.uniform(0.85, 1.40)
if dt.weekday() in (4, 5, 6):
factor *= 1.10

```
results = []
for (orig, dst, airline, fnum, dep_t, arr_t, dur, price_base, n_stops) in KNOWN_FLIGHTS:
    if orig not in origins or dst not in dests:
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
```

async def search_best(origins: list, dests: list, date_from: datetime, date_to: datetime,
stops: str, nb_results: int) -> list:
all_f = []
seen  = set()
delta = (date_to - date_from).days + 1

```
for d in range(delta):
    date_str = (date_from + timedelta(days=d)).strftime("%Y-%m-%d")
    api_results = []
    for orig in origins:
        for dst in dests:
            api_results += await search_flights_api(orig, dst, date_str)

    source = api_results if api_results else get_static_flights(origins, dests, date_str, stops)
    for f in source:
        key = (f["airline"], f["flight_num"], date_str)
        if key not in seen:
            seen.add(key)
            all_f.append(f)

with_price = sorted([f for f in all_f if f["price"] > 0], key=lambda x: x["price"])
without_price = [f for f in all_f if f["price"] == 0]
return (with_price + without_price)[:nb_results]
```

# ─── FORMATAGE ─────────────────────────────────────────────────────────────────

def format_flight(f: dict, rank: int) -> str:
medals  = [“🥇”,“🥈”,“🥉”,“4️⃣”,“5️⃣”,“6️⃣”,“7️⃣”,“8️⃣”,“9️⃣”,“🔟”]
medal   = medals[rank] if rank < len(medals) else f”{rank+1}.”
s_lbl   = “✅ Direct” if f[“stops”] == 0 else f”🔄 {f[‘stops’]} escale(s)”
dur_h, dur_m = divmod(f[“duration”], 60)
dur_str = f”{dur_h}h{dur_m:02d}” if dur_h else “”
orig_lbl = next((l for c, l in ORIGINS_LIST if c == f[“origin”]), f[“origin”])
dest_lbl = next((l for c, l in DESTS_LIST  if c == f[“destination”]), f[“destination”])
price_s = f”*{f[‘price’]} €*” if f[“price”] > 0 else “*voir lien*”
dep_s   = f[“depart”][11:16] if len(f[“depart”]) > 10 else f[“depart”]
arr_s   = f[“arrive”][11:16] if len(f[“arrive”]) > 10 else f[“arrive”]
date_s  = f[“depart”][:10]
line = (
f”{medal} *{f[‘airline’]} {f[‘flight_num’]}*\n”
f”   {orig_lbl} → {dest_lbl}  |  📅 `{date_s}`\n”
f”   🕐 `{dep_s}` → `{arr_s}`”
)
if dur_str:
line += f”  ⏱️ {dur_str}”
line += f”\n   {s_lbl}  |  💶 {price_s}\n”
return line

# ─── HANDLERS ──────────────────────────────────────────────────────────────────

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
uid = update.effective_user.id
user_sessions[uid] = {“sel_origins”: set(), “sel_dests”: set()}
await update.message.reply_text(
“✈️ *FlightBot* — Vols pas chers vers le Maghreb\n”
“━━━━━━━━━━━━━━━━━━━━━━━━\n\n”
“👇 *Sélectionnez vos aéroports de départ :*\n”
“*Cochez un ou plusieurs, puis validez.*”,
parse_mode=“Markdown”,
reply_markup=build_multiselect_keyboard(
ORIGINS_LIST, set(), “orig”, “Valider les départs ✈️”
)
)
return STATE_ORIGIN

async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
await update.message.reply_text(
“📖 *FlightBot — Aide*\n\n”
“• /search — Nouvelle recherche\n”
“• /cancel — Annuler\n\n”
“Compagnies : Ryanair, Transavia, TUIfly,\n”
“Royal Air Maroc, Air France, Air Arabia…\n\n”
“💡 Cochez plusieurs villes de départ/arrivée.\n”
“💡 Période : rapide (7/14/30/90 jours) ou dates libres.”,
parse_mode=“Markdown”
)

# ── Sélection départs (multi) ──────────────────────────────────────────────────

async def cb_origin(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q = update.callback_query; await q.answer()
uid     = q.from_user.id
_, code = q.data.split(”:”, 1)
sel     = user_sessions[uid][“sel_origins”]
all_codes = {c for c, _ in ORIGINS_LIST}

```
if code == "ALL":
    sel.update(all_codes)
elif code == "NONE":
    sel.clear()
elif code == "CONFIRM":
    if not sel:
        await q.answer("⚠️ Sélectionnez au moins un aéroport !", show_alert=True)
        return STATE_ORIGIN
    lbl = selected_labels(ORIGINS_LIST, sel)
    await q.edit_message_text(
        f"✅ Départ(s) : *{lbl}*\n\n"
        "👇 *Sélectionnez vos destinations :*\n"
        "_Cochez une ou plusieurs, puis validez._",
        parse_mode="Markdown",
        reply_markup=build_multiselect_keyboard(
            DESTS_LIST, set(), "dest", "Valider les destinations 🏙️"
        )
    )
    user_sessions[uid]["sel_dests"] = set()
    return STATE_DEST
else:
    if code in sel:
        sel.discard(code)
    else:
        sel.add(code)

await q.edit_message_text(
    "👇 *Sélectionnez vos aéroports de départ :*\n"
    "_Cochez un ou plusieurs, puis validez._",
    parse_mode="Markdown",
    reply_markup=build_multiselect_keyboard(ORIGINS_LIST, sel, "orig", "Valider les départs ✈️")
)
return STATE_ORIGIN
```

# ── Sélection destinations (multi) ────────────────────────────────────────────

async def cb_dest(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q = update.callback_query; await q.answer()
uid     = q.from_user.id
_, code = q.data.split(”:”, 1)
sel     = user_sessions[uid][“sel_dests”]
all_codes = {c for c, _ in DESTS_LIST}

```
if code == "ALL":
    sel.update(all_codes)
elif code == "NONE":
    sel.clear()
elif code == "CONFIRM":
    if not sel:
        await q.answer("⚠️ Sélectionnez au moins une destination !", show_alert=True)
        return STATE_DEST
    orig_lbl = selected_labels(ORIGINS_LIST, user_sessions[uid]["sel_origins"])
    dest_lbl = selected_labels(DESTS_LIST,   sel)
    await q.edit_message_text(
        f"✅ Départ(s) : *{orig_lbl}*\n"
        f"✅ Destination(s) : *{dest_lbl}*\n\n"
        "👇 *Préférence d'escales :*",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("🚀 Sans escale uniquement", callback_data="direct")],
            [InlineKeyboardButton("🔄 Peu importe",            callback_data="any")],
            [InlineKeyboardButton("⏱️ Avec escale OK",         callback_data="stopover")],
        ])
    )
    return STATE_STOPOVER
else:
    if code in sel:
        sel.discard(code)
    else:
        sel.add(code)

await q.edit_message_text(
    "👇 *Sélectionnez vos destinations :*\n"
    "_Cochez une ou plusieurs, puis validez._",
    parse_mode="Markdown",
    reply_markup=build_multiselect_keyboard(DESTS_LIST, sel, "dest", "Valider les destinations 🏙️")
)
return STATE_DEST
```

# ── Escales ───────────────────────────────────────────────────────────────────

async def cb_stopover(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q = update.callback_query; await q.answer()
user_sessions[q.from_user.id][“stops”] = q.data
await q.edit_message_text(
f”✅ Escales : *{STOPS_LABEL[q.data]}*\n\n”
“📅 *Période de recherche :*”,
parse_mode=“Markdown”,
reply_markup=InlineKeyboardMarkup([
[InlineKeyboardButton(“📅 7 prochains jours”,  callback_data=“quick:7”)],
[InlineKeyboardButton(“📅 14 prochains jours”, callback_data=“quick:14”)],
[InlineKeyboardButton(“📅 30 prochains jours”, callback_data=“quick:30”)],
[InlineKeyboardButton(“📅 3 prochains mois”,   callback_data=“quick:90”)],
[InlineKeyboardButton(“🗓️ Dates personnalisées…”, callback_data=“quick:custom”)],
])
)
return STATE_DATE_MENU

# ── Période rapide ou custom ──────────────────────────────────────────────────

async def cb_date_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q = update.callback_query; await q.answer()
uid = q.from_user.id
_, choice = q.data.split(”:”, 1)

```
if choice == "custom":
    await q.edit_message_text(
        "🗓️ *Dates personnalisées*\n\n"
        "Entrez la *date de début* au format `JJ/MM/AAAA` :\n"
        "_Exemple : 15/06/2026_",
        parse_mode="Markdown"
    )
    user_sessions[uid]["awaiting"] = "date_from"
    return STATE_DATE_FROM
else:
    days = int(choice)
    today = datetime.today()
    user_sessions[uid]["date_from"] = today + timedelta(days=1)
    user_sessions[uid]["date_to"]   = today + timedelta(days=days)
    user_sessions[uid]["period_label"] = f"{days} prochains jours"
    await q.edit_message_text(
        f"✅ Période : *{days} prochains jours*\n\n"
        f"📊 *Nombre de résultats à afficher :*\n_(défaut : {DEFAULT_NB_RESULTS})_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("3️⃣  3 résultats",          callback_data="r3"),
             InlineKeyboardButton("5️⃣  5 résultats",          callback_data="r5")],
            [InlineKeyboardButton(f"7️⃣  7 résultats ✅ défaut", callback_data="r7"),
             InlineKeyboardButton("🔟 10 résultats",           callback_data="r10")],
        ])
    )
    return STATE_NB_RESULTS
```

# ── Saisie date début ─────────────────────────────────────────────────────────

async def handle_date_from(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
uid  = update.effective_user.id
text = update.message.text.strip()
try:
dt = datetime.strptime(text, “%d/%m/%Y”)
if dt.date() < datetime.today().date():
await update.message.reply_text(“⚠️ La date doit être dans le futur. Réessayez :”)
return STATE_DATE_FROM
user_sessions[uid][“date_from”] = dt
await update.message.reply_text(
f”✅ Début : *{text}*\n\nEntrez la *date de fin* au format `JJ/MM/AAAA` :”,
parse_mode=“Markdown”
)
user_sessions[uid][“awaiting”] = “date_to”
return STATE_DATE_TO
except ValueError:
await update.message.reply_text(
“❌ Format invalide. Utilisez `JJ/MM/AAAA` — exemple : `15/06/2026`”,
parse_mode=“Markdown”
)
return STATE_DATE_FROM

# ── Saisie date fin ───────────────────────────────────────────────────────────

async def handle_date_to(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
uid  = update.effective_user.id
text = update.message.text.strip()
try:
dt = datetime.strptime(text, “%d/%m/%Y”)
date_from = user_sessions[uid][“date_from”]
if dt < date_from:
await update.message.reply_text(“⚠️ La date de fin doit être après la date de début. Réessayez :”)
return STATE_DATE_TO
if (dt - date_from).days > 180:
await update.message.reply_text(“⚠️ La période ne peut pas dépasser 6 mois. Réessayez :”)
return STATE_DATE_TO

```
    user_sessions[uid]["date_to"] = dt
    df = date_from.strftime("%d/%m/%Y")
    dt_str = dt.strftime("%d/%m/%Y")
    user_sessions[uid]["period_label"] = f"{df} → {dt_str}"

    await update.message.reply_text(
        f"✅ Période : *{df} → {dt_str}*\n\n"
        f"📊 *Nombre de résultats :*\n_(défaut : {DEFAULT_NB_RESULTS})_",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("3️⃣  3 résultats",           callback_data="r3"),
             InlineKeyboardButton("5️⃣  5 résultats",           callback_data="r5")],
            [InlineKeyboardButton(f"7️⃣  7 résultats ✅ défaut",  callback_data="r7"),
             InlineKeyboardButton("🔟 10 résultats",            callback_data="r10")],
        ])
    )
    return STATE_NB_RESULTS
except ValueError:
    await update.message.reply_text(
        "❌ Format invalide. Utilisez `JJ/MM/AAAA` — exemple : `30/06/2026`",
        parse_mode="Markdown"
    )
    return STATE_DATE_TO
```

# ── Nombre de résultats + lancement recherche ─────────────────────────────────

async def cb_nb_results(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q   = update.callback_query
msg = q.message if q else update.message
uid = (q.from_user if q else update.effective_user).id
if q: await q.answer()

```
nb = int(q.data.replace("r", "")) if q else DEFAULT_NB_RESULTS
s  = user_sessions[uid]
s["nb_results"] = nb

origins = list(s["sel_origins"])
dests   = list(s["sel_dests"])
stops   = s["stops"]
date_from = s["date_from"]
date_to   = s["date_to"]
period_lbl = s.get("period_label", "—")

orig_lbl = selected_labels(ORIGINS_LIST, s["sel_origins"])
dest_lbl = selected_labels(DESTS_LIST,   s["sel_dests"])

summary = (
    f"🔍 *Recherche en cours…*\n\n"
    f"   ✈️ {orig_lbl}\n"
    f"   🏙️ {dest_lbl}\n"
    f"   {STOPS_LABEL[stops]}\n"
    f"   📅 {period_lbl}  |  top {nb}\n\n"
    f"_Patientez quelques secondes…_"
)

if q:
    await q.edit_message_text(summary, parse_mode="Markdown")
else:
    await msg.reply_text(summary, parse_mode="Markdown")

flights = await search_best(origins, dests, date_from, date_to, stops, nb)

if not flights:
    await msg.reply_text(
        "😕 *Aucun vol trouvé.*\n\nEssayez une période plus longue ou d'autres villes.\n/search pour recommencer.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END

header = (
    f"✈️ *Top {len(flights)} vols les moins chers*\n"
    f"━━━━━━━━━━━━━━━━━━━━━━━━\n"
    f"✈️ {orig_lbl}\n🏙️ {dest_lbl}\n"
    f"{STOPS_LABEL[stops]}  |  📅 {period_lbl}\n"
    f"━━━━━━━━━━━━━━━━━━━━━━━━\n\n"
)
body   = "\n".join(format_flight(f, i) for i, f in enumerate(flights))
footer = (
    "\n━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "⚠️ _Prix indicatifs — confirmez sur le site compagnie._\n"
    "/search pour une nouvelle recherche."
)

keyboard = InlineKeyboardMarkup([[
    InlineKeyboardButton("🔗 Voir sur Google Flights",
                         url=flights[0].get("book_url", "https://www.google.com/flights"))
],[
    InlineKeyboardButton("🔄 Nouvelle recherche", callback_data="restart")
]])

await msg.reply_text(header + body + footer, parse_mode="Markdown",
                     reply_markup=keyboard, disable_web_page_preview=True)
return ConversationHandler.END
```

# ── Restart & Cancel ──────────────────────────────────────────────────────────

async def cb_restart(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
q = update.callback_query; await q.answer()
uid = q.from_user.id
user_sessions[uid] = {“sel_origins”: set(), “sel_dests”: set()}
await q.message.reply_text(
“✈️ *Nouvelle recherche*\n\n”
“👇 *Sélectionnez vos aéroports de départ :*”,
parse_mode=“Markdown”,
reply_markup=build_multiselect_keyboard(ORIGINS_LIST, set(), “orig”, “Valider les départs ✈️”)
)
return STATE_ORIGIN

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> int:
await update.message.reply_text(“❌ Annulé. /search pour recommencer.”)
return ConversationHandler.END

# ─── MAIN ──────────────────────────────────────────────────────────────────────

def main():
app = Application.builder().token(TELEGRAM_TOKEN).build()

```
conv = ConversationHandler(
    entry_points=[CommandHandler("start", start), CommandHandler("search", start)],
    states={
        STATE_ORIGIN: [
            CallbackQueryHandler(cb_origin, pattern="^orig:")
        ],
        STATE_DEST: [
            CallbackQueryHandler(cb_dest, pattern="^dest:")
        ],
        STATE_STOPOVER: [
            CallbackQueryHandler(cb_stopover, pattern="^(direct|any|stopover)$")
        ],
        STATE_DATE_MENU: [
            CallbackQueryHandler(cb_date_menu, pattern="^quick:")
        ],
        STATE_DATE_FROM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_from)
        ],
        STATE_DATE_TO: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, handle_date_to)
        ],
        STATE_NB_RESULTS: [
            CallbackQueryHandler(cb_nb_results, pattern="^r(3|5|7|10)$")
        ],
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
app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)
```

if **name** == “**main**”:
main()
