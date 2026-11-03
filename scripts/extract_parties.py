"""Rule-based extraction of transaction details from Grandchamp's act summaries.

Grandchamp's analyses follow a small set of formulas, e.g.

    "Francisco Vita, calabrais, reconnaît devoir pour son rachat 140 écus d'or
     d'Espagne à Nardo Ferrer, sicilien."

extract(act) turns one summary into the transaction columns (parties with
origins and roles, matter, price, currency, keywords, decision, notes). The
result is a first draft: build_db.py then applies the manually reviewed
corrections in annotations/vol<N>.json, which take precedence.
"""
import re

# ---------------------------------------------------------------- vocabulary

ORIGIN_ADJ = (
    r"sicilien|corse|napolitain|g[ée]nois|grec|marseillais|proven[çc]al|majorquin|sarde|"
    r"calabrais|romain|v[ée]nitien|ragusais|espagnol|fran[çc]ais|maltais|cypriote|chypriote|"
    r"esclavon|lombard|palermitain|portugais|florentin|toscan|savoyard|languedocien|anglais|"
    r"flamand|allemand|turc|maure|juif|arabe|africain|albanais|candiote|pi[ée]montais|"
    r"milanais|catalan|valencien|rhodien|trapanais|messinois|livournais|ni[çc]ois|toulonnais|"
    r"ciotaden|sicilienne|bourguignon|lyonnais|italien|levantin|andalou|castillan|sardes|"
    r"tunisien|alg[ée]rien|tripolitain|ottoman|candiot|slave|dalmate|hongrois|polonais|"
    r"bolonais|lucquois|pisan|siennois|mantouan|ferrarais|parmesan|vénitienne"
)
ORIGIN_ADJ_RE = re.compile(rf"^(?:(?:{ORIGIN_ADJ})(?:ne|e|s|es|nes)?)(?:\s+(?:reni[ée]e?|ren[ée]gate?|de\s+[\w'-]+|d'\w+)){{0,2}}$", re.I)

ROLE_WORDS = [
    # (regex, English label)
    (r"vice[- ]consul", "vice-consul"), (r"(?<!vice-)(?<!vice )\bconsul", "consul"), (r"chancelier", "chancellor"),
    (r"patrons?\b", "ship master (patron)"), (r"cap(?:itai)?(?:ne|e|iie|n|\^)\w*", "captain"),
    (r"marchands?", "merchant"), (r"mariniers?|marins?\b", "sailor"), (r"[ée]crivain", "ship's clerk (écrivain)"),
    (r"ma[iî]tre ma[çc]on", "master mason"), (r"esclaves?", "slave/captive"),
    (r"ren[ée]gat|reni[ée]", "renegade"), (r"janissaire", "janissary"), (r"oldach|odabass?i", "janissary officer (oldach)"),
    (r"ra[iï]s\b", "corsair captain (raïs)"), (r"ca[iï]d", "caïd"), (r"pacha|bassa|bacha|baissa", "pasha"),
    (r"\bbey\b", "bey"), (r"\baga\b", "agha"), (r"religieu(?:x|se)|pr[êe]tre|\bp[èe]re\b(?! et fils)", "cleric"),
    (r"chevalier", "knight"), (r"gouverneur", "governor"), (r"douanier", "customs officer"),
    (r"interpr[èe]te|truchement", "interpreter"), (r"noble|gentilhomme|seigneur", "nobleman"),
    (r"\bveuve\b", "widow"), (r"\bfemme\b", "wife"), (r"fr[èe]re", "brother"), (r"\bfils\b", "son"),
    (r"notaire", "notary"), (r"repr[ée]sentant|mandataire|procureur", "representative"),
    (r"commis|facteur|agent", "agent"), (r"administrateur", "administrator"), (r"associ[ée]", "partner"),
    (r"soldats?", "soldier"), (r"[ée]cuyer", "squire (écuyer)"), (r"bourgeois|citoyen", "citizen"), (r"ma[iî]tre", "master"), (r"chirurgien|barbier", "surgeon/barber"),
]

TYPE_EN = {
    "reconnaissance_dette": "Debt", "quittance": "Payment / receipt", "procuration": "Power of attorney",
    "affretement": "Charter (freight)", "protestation": "Protest / summons", "caution": "Surety",
    "societe": "Company / partnership", "accord": "Agreement", "decision": "Consular decision",
    "declaration": "Declaration / attestation", "enregistrement": "Registration / copy",
    "testament": "Will", "mariage": "Marriage contract", "engagement": "Undertaking / promise",
    "vente_cession": "Sale / transfer", "deliberation": "Deliberation of the Nation", "autre": "Other",
}

KEYWORDS = [
    (r"rach[ae]t|rachep|racheter|rançon|lib[ée]rt[ée]|d[ée]livr", "ransom"),
    (r"esclave", "slavery"), (r"ren[ée]gat|reni[ée]", "renegade"),
    (r"pr[êe]t[ée]?s?\b|pr[êe]t est consenti|avanc[ée]s", "loan"),
    (r"\bvins?\b", "wine"), (r"laines?", "wool"), (r"cuirs?", "hides"), (r"corail|corailleur", "coral"),
    (r"savon", "soap"), (r"bl[ée]\b|froment", "grain"), (r"drap|[ée]toffe|soie|fil d'or", "textiles"),
    (r"\bhuile", "oil"), (r"cire", "wax"), (r"sel\b", "salt"),
    (r"fret|nolis|nolise|affr[èe]te|chargement|charge(?:r|ment)", "freight / shipping"),
    (r"navire|vaisseau|barque|galion|saetia|sa[ée]tie|polacre|nef\b|tartane|fr[ée]gate|galère|bateau", "ship"),
    (r"droits? de consulat|droit de \d|droit temporaire|% ", "consular dues"),
    (r"avanie|avarie", "avania (extortion)"), (r"douane|douanier", "customs"),
    (r"compagnie du corail|emprise du corail|compagnie", "company"),
    (r"r[ée]demption", "Redemption (charitable order)"), (r"nation fran[çc]aise|la nation", "French Nation"),
    (r"janissaire|divan|diuan", "janissaries / divan"), (r"pacha|bassa|roi de tunis|bey\b", "Pasha / Bey of Tunis"),
    (r"tabarca|tabarque", "Tabarca"), (r"bastion", "Bastion de France"), (r"galères", "galleys"),
    (r"successi?on|h[ée]ritier|d[ée]c[èe]s|feu\b", "inheritance"), (r"mariage|femme|veuve", "family"),
    (r"maure", "Moors"), (r"\bjuif", "Jews"), (r"caution|garant", "guarantee"),
    (r"livre de comptes?|comptes?\b", "accounts"), (r"marchandises?", "goods"),
    (r"contestation|diff[ée]rend|litige|proc[èe]s", "dispute"), (r"amende", "fine"),
]

PURPOSES = [
    (r"pour (?:son|leur|sa) (?:propre )?rachat|payés? pour (?:son|leur) rachat|pour se racheter|lors de (?:son|leur) rachat", "ransom of party 1"),
    (r"(?:pour|du|au) (?:le )?(?:solde|compl[ée]ment|reste) (?:de|du) (?:son|leur) rachat", "balance of party 1's ransom"),
    (r"pour le rachat d[eu']", "ransom of a third party"),
    (r"vente de vin", "sale of wine"), (r"vente d'[ée]toffe|drap", "sale of cloth"),
    (r"\bfret\b|nolis", "freight"), (r"chargement", "cargo / loading"),
    (r"prêt[ée]s? pour ses propres besoins", "personal loan"),
    (r"droits? de consulat", "consular dues"), (r"salaires?|gages", "wages"),
    (r"compagnie du corail", "Compagnie du Corail business"),
]

AMT_RE = re.compile(
    r"(?<![\w/])(?P<num>\d{1,3}(?:\.\d{3})+|\d+)(?P<frac>\s*(?:et\s+(?:demi[e]?|un\s+tiers|\d/\d|[\\½¼¾])|[½¼¾]|\d/\d))?\s+"
    r"(?P<cur>[ée]cus?|onces?|ducats?|aspres?|taris?|piastres?|livres?|sols?|sous|r[ée]aux|carlins?|"
    r"sequins?|doubles?|doublons?|sultanins?|florins?|thalers?|deniers?|pi[èe]ces? de huit)\b"
    r"(?P<qual>(?:\s*,?\s*(?:d'or|d'argent|de\s+Sicile|de\s+Naples|d'Espagne|coin\s+d'Espagne|sol\b|pistol\w*|"
    r"de\s+France|de\s+Marseille|de\s+Malte|de\s+Venise|de\s+\d+\s+(?:aspres|taris|r[ée]aux|carlins|soubz|sous)(?:\s+chaque)?))*)"
    r"(?P<frac2>\s+et\s+demie?\b)?",
    re.I,
)
RATE_RE = re.compile(
    r"(?:à|de)\s+\d+\s*(?:et\s+demi\s+)?(?:taris|aspres|carlins|r[ée]aux|soubz|sous)\s+(?:par|à l'|l')\s*[ée]cus?|"
    r"de\s+\d+\s+(?:taris|aspres|r[ée]aux|carlins|soubz|sous)(?:\s+chaque)?|"
    r"\d+\s+(?:taris|aspres|carlins)\s+(?:et\s+demi\s+)?par\s+[ée]cus?", re.I)

CUR_NORM = [
    (r"[ée]cus?", "écus"), (r"onces?", "onces"), (r"ducats?", "ducats"), (r"aspres?", "aspres"),
    (r"taris?", "taris"), (r"piastres?", "piastres"), (r"livres?", "livres"), (r"sols?|sous", "sous"),
    (r"r[ée]aux", "réaux"), (r"carlins?", "carlins"), (r"sequins?", "sequins"),
    (r"doubles?|doublons?", "doublons"), (r"sultanins?", "sultanins"), (r"florins?", "florins"),
    (r"thalers?", "thalers"), (r"deniers?", "deniers"), (r"pi[èe]ces? de huit", "pièces de huit"),
]

NAME_START = re.compile(r"^(?:[A-ZÀ-ÝÉ]|d'|de\s+[A-Z]|di\s+[A-Z])")


# ---------------------------------------------------------------- helpers

def clean_summary(s):
    s = re.sub(r"^[^\wÀ-ÿ«]+", "", s.strip())
    s = re.sub(r"^[\W_]*(?:E[nt]i?)\s+(?:italien|latin|espagnol|provençal|français)\s*[.:,]?\s*[—–-]+\s*", "", s.strip())
    s = re.sub(r"^\(\s*\d+\s*\)\s*(?:\d+\)\s*|\(\w\)\s*)?", "", s)   # register folio "(269)"
    s = re.sub(r"\bà(?=[A-ZÉ])", "à ", s)            # OCR: "àBastiano"
    s = re.sub(r"\bde'\s", "de ", s)
    s = re.sub(r"\.\s+(?=d[e']\s?[A-Z])", ", ", s)          # OCR: "Rabastan. de Marseille"
    s = re.sub(r"\s+", " ", s)
    return s.strip(" '\\*")


def split_sentences(s):
    # Split on ". " followed by a capital, keeping parenthesised remarks whole.
    out, depth, cur = [], 0, ""
    for i, ch in enumerate(s):
        cur += ch
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == "." and depth == 0 and s[i + 1:i + 3].strip()[:1].isupper() and s[i + 1:i + 2] == " ":
            # avoid splitting after initials / abbreviations ("J. B.", "S.", "cap.")
            prev = cur[:-1].split()[-1] if cur[:-1].split() else ""
            if len(prev) <= 2 or prev.lower() in ("st", "ste", "sr", "cap", "capne", "mgr", "ant", "bapte"):
                continue
            out.append(cur.strip())
            cur = ""
    if cur.strip():
        out.append(cur.strip())
    return out


def role_labels(text):
    labels = []
    for pat, lab in ROLE_WORDS:
        if re.search(pat, text, re.I) and lab not in labels:
            labels.append(lab)
    return labels


def parse_people(chunk):
    """'Le patron Jehan Arnaud, de la Ciotat, et Pierre Burle, marseillais' ->
    [{'name':..., 'origins':[...], 'roles':[...]}, ...]"""
    chunk = chunk.strip(" ,.;:")
    chunk = re.sub(r"^(?:à|au|aux|à la|de|du|des|par|en faveur de)\s+", "", chunk)
    people = []
    if not chunk:
        return people
    # leading role words: "Le patron X", "les patrons A, B et C", "le capne X", "le noble X"
    lead_roles = []
    m = re.match(r"^(?:le|la|les|l')\s*((?:(?:patrons?|cap\S*|capitaines?|noble|chevalier|père|p\.|sieur|s\^?r|seigneur|magnifique|marchands?|nommée?|consul|chancelier|caïd|cayt|cait|raïs)\s+)+)", chunk, re.I)
    if m:
        lead_roles = role_labels(m.group(1))
        chunk = chunk[m.end():]
    elif re.match(r"^(?:patrons?|capitaines?|cap\S{1,4}|ra[iï]s|ca[iï]d|noble|chevalier|sieur)\s+(?=[A-Z])", chunk):
        m = re.match(r"^(\S+)\s+", chunk)
        lead_roles = role_labels(m.group(1))
        chunk = chunk[m.end():]
    # Tokenise on commas and " et " (not inside parentheses or quotes)
    parts, depth, cur = [], 0, ""
    i = 0
    while i < len(chunk):
        ch = chunk[i]
        if ch in "(«":
            depth += 1
        elif ch in ")»":
            depth = max(0, depth - 1)
        if depth == 0 and ch == ",":
            parts.append(cur)
            cur = ""
            i += 1
            continue
        if depth == 0 and chunk[i:i + 4] == " et " and not re.match(r"\s*(?:demi|un tiers|fils)", chunk[i + 4:]):
            parts.append(cur)
            cur = ""
            i += 4
            continue
        if depth == 0 and chunk[i:i + 6] == " ou à ":
            parts.append(cur)
            cur = ""
            i += 6
            continue
        cur += ch
        i += 1
    parts.append(cur)
    parts = [re.sub(r"^(?:à|au|aux)\s+", "", p.strip()) for p in parts if p.strip()]
    KIN = {"femme": "wife", "frère": "brother", "frere": "brother", "fils": "son", "père": "father",
           "mère": "mother", "neveu": "nephew", "cousin": "cousin", "associé": "partner",
           "beau-frère": "brother-in-law", "fille": "daughter", "oncle": "uncle", "maître": "master"}
    for p in parts:
        km = re.match(r"^(?:sa|son|ses|leur|leurs)\s+(\S+)\s*(.*)$", p)
        if km and km.group(1).lower() in KIN:
            label = KIN[km.group(1).lower()] + " (of party)"
            nm = km.group(2).strip()
            people.append({"name": nm if nm and nm[0].isupper() else f"(his/her {KIN[km.group(1).lower()]})",
                           "origins": [], "roles": list(lead_roles) + [label]})
            continue
        is_name = bool(NAME_START.match(p)) and not (people and re.match(
            r"^(?:Pacha|Roi|Bey|Aga|Ca[iï]d|Consul|Gouverneur|Seigneur|Vice-roi|Ambassadeur|Chancelier)\b", p)) and not ORIGIN_ADJ_RE.match(p) and not re.match(
            r"^(?:de la même|du même|de\s+[A-Z][\w'-]+(?:\s+\(.*\))?$)", p)
        # "de Marseille" is an origin; "Jehan de Gregorio" is a name
        if re.match(r"^(?:de|du)\s+[A-ZÀ-Ý][\w'-]+\s+(?:de\s+|di\s+)?[A-ZÀ-Ý]", p):
            p = re.sub(r"^(?:de|du)\s+", "", p)        # "... et de Louis Gicalino"
            is_name = True
        if re.match(r"^(?:de\s|d'|du\s)", p) and people:
            is_name = False
        if is_name:
            name = re.sub(r"\s+", " ", p).strip(" .")
            people.append({"name": name, "origins": [], "roles": list(lead_roles)})
            continue
        if not people:
            continue
        target = people[-1]
        desc = p.strip(" .")
        if target.get("_kin"):
            # descriptors after "fille de Memi-Raïs" describe that relative, not the party
            target["roles"][-1] += ", " + desc
            continue
        km = re.match(r"^(fils|fille|fr[èe]re|s(?:œ|oe)ur|femme|veuve|neveu|ni[èe]ce|p[èe]re|m[èe]re|oncle|gendre|beau-fr[èe]re|esclave|serviteur|domestique|associ[ée]|facteur|commis|repr[ée]sentant|mandataire)\s+(?:de|du|d'|des)\s*(.+)$", desc, re.I)
        if km:
            labels = {"fils": "son", "fille": "daughter", "femme": "wife", "veuve": "widow", "neveu": "nephew",
                      "esclave": "slave", "père": "father", "pere": "father", "frère": "brother", "frere": "brother"}
            target["roles"].append(f"{labels.get(km.group(1).lower(), km.group(1).lower())} of {km.group(2)}")
            target["_kin"] = True
            continue
        if re.match(r"^(?:dit|alias)\s", desc):
            target["name"] += f" ({desc})"
        elif re.match(r"^(?:de la même ville|du même lieu|de la même île)", desc):
            prev = people[-2]["origins"] if len(people) > 1 else []
            target["origins"] += prev or ["same place as previous party"]
        elif ORIGIN_ADJ_RE.match(desc):
            adj = desc
            plural = bool(re.match(rf"^(?:{ORIGIN_ADJ})(?:s|es|nes)\b", adj, re.I)) and len(people) > 1
            tgt = people if plural else [target]
            for t in tgt:
                t["origins"].append(adj)
                if re.search(r"reni[ée]|ren[ée]gat", adj):
                    t["roles"].append("renegade")
        elif re.match(r"^(?:de|d'|du|des)\s*[A-ZÀ-Ý]", desc) or re.match(r"^(?:habitant|citoyen|originaire|natif|demeurant)", desc):
            target["origins"].append(desc)
        else:
            labs = role_labels(desc)
            target["roles"] += [l for l in labs if l not in target["roles"]] or [desc]
            om = re.search(r"\b(?:de|d'|du)\s*((?:la\s+)?[A-ZÀ-Ý][\w'-]+(?:\s+[A-ZÀ-Ý][\w'-]+)?)\s*$", desc)
            if labs and om:
                target["origins"].append("de " + om.group(1))
    for pp in people:
        pp.pop("_kin", None)
    return people


def join_people(people, field):
    vals = []
    for p in people:
        if field == "name":
            vals.append(p["name"])
        else:
            vals.append(", ".join(p[field]) if p[field] else "")
    if field != "name" and not any(vals):
        return None
    return "; ".join(vals) if vals else None


def first_amount_split(text):
    """Return (before, amount_match, after) around the first sum of money."""
    m = AMT_RE.search(text)
    if not m:
        return text, None, ""
    return text[:m.start()], m, text[m.end():]


def skip_amounts(text):
    """Drop further amounts directly after the price: '... onces et 15 taris à X'."""
    while True:
        m = re.match(r"^\s*(?:et|,)?\s*", text)
        rest = text[m.end():]
        am = AMT_RE.match(rest)
        if not am:
            return text
        text = rest[am.end():]


def cut_p2(text):
    return re.split(r",?\s+(?:tout|ce qu|pour|payés?|prêtés?|la somme|une?\s+somme|le prix|les?\s+droits?|"
                    r"\d|une?|deux|trois|quatre|cinq|six|sept|huit|neuf|dix|douze|vingt|trente|quarante|"
                    r"cinquante|soixante|cent|diverses?|certaine?s?|des|ses|sa|son|leurs?|"
                    r"le\s+montant|qu'|dont|remboursables?)\b", text, maxsplit=1)[0]


def norm_currency(m):
    cur = m.group("cur").lower()
    base = next(v for k, v in CUR_NORM if re.fullmatch(k, cur))
    qual = re.sub(r"\s+", " ", (m.group("qual") or "")).strip(" ,")
    qual = re.sub(r"^,\s*", "", qual)
    return f"{base} {qual}".strip()


def amount_value(m):
    num = int(m.group("num").replace(".", ""))
    frac = (m.group("frac") or m.group("frac2") or "").strip()
    if "demi" in frac or "½" in frac or "\\" in frac:
        num += 0.5
    elif "tiers" in frac:
        num += 1 / 3
    elif "¼" in frac:
        num += 0.25
    elif "¾" in frac:
        num += 0.75
    else:
        fm = re.search(r"(\d)/(\d)", frac)
        if fm and int(fm.group(2)):
            num += int(fm.group(1)) / int(fm.group(2))
    return round(num, 3)


# ---------------------------------------------------------------- patterns

VERB_SPLIT = (r",?\s+(?:reconna\w*|r[ée]conna\w*|a\s|ont\s|fait\b|font\b|est\b|sont\b|demande|donne|promet|s'|se\s|"
              r"prend|paie|paye|remet|accorde|c[èe]de|charge|annonce|[ée]crit|rend|vend|ach[èe]te|consent|"
              r"renonce|d[ée]clare|dit\s+que|refuse|r[ée]clame|assure|re[çc]oit|nolise|loue|accepte|expose|"
              r"r[ée]pond|proteste|requiert|prie|supplie|affirme|atteste|certifie|reste|doit|devra|lui|leur)\b")

V_DEBT = r"(?:(?:reconna\w*|r[ée]conna\w*|reconnaissent)\s+(?:de\s*)?devoir|(?:\w\s+)?doi(?:t|vent)(?=\s+(?:à|au|aux)\s))"
V_RECV = r"(?:(?:reconna\w*|r[ée]conna\w*|reconnaissent)\s+avoir|\ba|\bont)\s+re[çc]u"
P2_STOP = r"(?=\s+\d|,\s*\d|\s+(?:pour|la somme|les?\s+\d|une?\s|tout|ce qui|ses|son|sa|leurs?)\b|[.;]|$)"


def _purpose(text):
    for pat, lab in PURPOSES:
        if re.search(pat, text, re.I):
            return lab
    return None


def extract(act):
    s = clean_summary(act["summary"])
    sents = split_sentences(s)
    main = sents[0] if sents else s
    rest_sents = sents[1:]
    res = {"p1": [], "p2": [], "others": [], "type": None, "matter": None,
           "decision": None, "price": None, "currency": None, "equiv": []}
    p1_role = p2_role = None
    m = None

    def others_from(text):
        """'... payés pour son rachat à Mamet de Fessi, maure de Tunis' -> payee of ransom."""
        out = []
        pm = re.search(r"pay[ée]s?\s+(?:pour\s+(?:son|leur|sa)\s+rachat\s+)?(?:au|à|aux)\s+(?!(?:son|leur)\b)(.+?)(?=\.|;|\s+pour\b|\s+montant\b|$)", text)
        if pm and NAME_START.match(pm.group(1).strip()) or (pm and re.match(r"(?:caïd|cayt|cait|caito|roi|pacha)\b", pm.group(1).strip(), re.I)):
            for p in parse_people(pm.group(1)):
                p["roles"] = ["recipient of ransom payment (captor/owner)"] + [r for r in p["roles"] if r]
                out.append(p)
        rm = re.search(r"(?:le\s+)?(?:solde\s+du\s+|reste\s+du\s+|compl[ée]ment\s+du\s+)?rach[ae]t\s+(?:de|du|d')\s*(?:la nommée\s+|le nommé\s+)?([A-ZÀ-Ý][^.,;()]+?)(?=[.,;(]|\s+(?:esclave|pour|à|que|qui)\b|$)", text)
        if rm and not re.match(r"(?:son|leur|sa)\b", rm.group(1)):
            for p in parse_people(rm.group(1)):
                p["roles"] = ["captive (ransomed)"]
                out.append(p)
        rm2 = re.search(r"racheter\s+(?:le nommé\s+|la nommée\s+)?([A-ZÀ-Ý][^.;()]+?)(?=[.;(]|\s+(?:esclave|pour|à)\b|$)", text)
        if rm2:
            for p in parse_people(rm2.group(1)):
                p["roles"] = ["captive (to be ransomed)"] + [r for r in p["roles"] if r not in ("captive (to be ransomed)",)]
                out.append(p)
        em = re.search(r"esclave\s+(?:de|du)\s+([A-ZÀ-Ý][^.,;()]+)", text)
        if em and not any(em.group(1).strip() in o["name"] for o in out):
            for p in parse_people(em.group(1)):
                p["roles"] = ["slave-owner"]
                out.append(p)
        return out

    # ---- debt: "X reconnaît devoir [pour son rachat] à Y N écus ..." / "... N écus à Y"
    m = re.match(rf"^(?P<p1>.+?)\s+{V_DEBT}\s*(?P<rest>.*)$", main)
    if m:
        res["type"] = "reconnaissance_dette"
        p1_txt, rest = m.group("p1"), m.group("rest")
        rest = re.sub(r"^pour (?:son|leur) rachat\s+", "", rest)
        before, am, after = first_amount_split(rest)
        after = skip_amounts(after)
        if re.match(r"^\s*(?:à|au|aux|envers)\s", before) and before.strip():
            p2_txt = cut_p2(before)
        else:
            after2 = re.sub(r"^\s*,?\s*(?:payés?\s+)?pour\s+(?:son|leur|sa)\s+rachat", "", after)
            mm = re.match(r"^\s*,?\s*(?:à|au|aux)\s+(.+?)(?=,?\s+(?:pour|payés?|prêtés?|avancés?|qu'|dont|remboursables?|montant)\b|[.;]|$)", after2)
            p2_txt = mm.group(1) if mm else before
        res["p1"], res["p2"] = parse_people(p1_txt), parse_people(p2_txt)
        p1_role, p2_role = "debtor", "creditor"
        res["others"] = others_from(after)
        if am:
            res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- receipt
    if not res["type"]:
        m = re.match(rf"^(?P<p1>.+?)\s+{V_RECV}\s*(?P<rest>.*)$", main)
        if m:
            res["type"] = "quittance"
            rest = m.group("rest")
            before, am, after = first_amount_split(rest)
            after = skip_amounts(after)
            if re.match(r"^\s*(?:de|du|des|d')", before) and before.strip():
                p2_txt = cut_p2(before)
            else:
                mm = re.match(r"^\s*,?\s*(?:de|du|des|d')\s*(.+?)(?=,?\s+(?:pour|qui|payés?|dont|montant|en\s)\b|[.;]|$)", after)
                p2_txt = mm.group(1) if mm else (before if not am else "")
            res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(p2_txt)
            p1_role, p2_role = "recipient (acknowledges receipt)", "payer"
            res["others"] = others_from(after)
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- redeemed by
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+reconna[iî]t\s+avoir\s+été\s+rachet[ée]e?s?\s+(?:par\s+)?(?P<p2>.*?)(?=[.;]|$)", main)
        if m:
            res["type"] = "rachat"
            res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(m.group("p2"))
            p1_role, p2_role = "ransomed captive", "redeemer (paid the ransom)"
    # ---- "X a racheté (à Y) Z": redemption by purchase
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+(?:a|ont)\s+rachet[ée]e?s?\s+(?P<rest>.*)$", main)
        if m and NAME_START.match(m.group("p1")):
            res["type"] = "rachat"
            rest = m.group("rest")
            mm = re.match(r"^(?:à|au|aux)\s+(?P<p2>.+?),\s+(?P<o>.*)$", rest)
            res["p1"] = parse_people(m.group("p1"))
            if mm:
                res["p2"] = parse_people(mm.group("p2"))
                rest = mm.group("o")
            om = re.search(r"(?:nommée?|appelée?)\s+(?P<o>[A-ZÀ-Ý][^.;]+?)(?=[.;]|$)", rest) or \
                 re.match(r"^(?P<o>[A-ZÀ-Ý][^.;]+?)(?=[.;]|$)", rest)
            if om:
                res["others"] = parse_people(om.group("o"))
                for p in res["others"]:
                    p["roles"] = ["captive (ransomed)"] + p["roles"]
            p1_role, p2_role = "redeemer (paid the ransom)", "seller of the captive (ransom recipient)"
            am = AMT_RE.search(rest)
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- quittance / décharge given
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+donnen?t?\s+(?:une\s+)?(?:quittance|d[ée]charge)(?:\s+g[ée]n[ée]rale)?\s*(?P<rest>.*)$", main)
        if m:
            res["type"] = "quittance"
            rest = m.group("rest")
            mm = re.search(r"(?:^|\s)(?:à|au|aux)\s+(.+?)(?=,?\s+(?:pour|d'une|de\s+\d|qui|dont)\b|[.;]|$)", rest)
            res["p1"] = parse_people(m.group("p1"))
            res["p2"] = parse_people(mm.group(1)) if mm else []
            p1_role, p2_role = "creditor (gives receipt/discharge)", "debtor (discharged)"
            am = AMT_RE.search(rest)
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- power of attorney
    if not res["type"]:
        for pat in (r"^(?P<p1>.+?)\s+(?:donnen?t?|passen?t?)\s+(?:une\s+)?procuration\s+(?:générale\s+)?(?:à|au|aux|en faveur de)\s+(?P<p2>.+?)(?P<rest>(?:,?\s+(?:pour|afin|de\s+(?=s'|[a-zé]+(?:er|ir|re)\b)|d'(?=[a-zé]+(?:er|ir|re)\b)|qui|au sujet|relative|concernant)\b.*)?)$",
                    r"^Procuration\s+donn[ée]e\s+par\s+(?P<p1>.+?)\s+(?:à|au|aux)\s+(?P<p2>.+?)(?P<rest>(?:,?\s+(?:pour|afin|de\s+(?=s'|[a-zé]+(?:er|ir|re)\b)|d'(?=[a-zé]+(?:er|ir|re)\b)|qui|au sujet|relative|concernant)\b.*)?)$",
                    r"^Procuration\s+(?:de|du)\s+(?P<p1>.+?)\s+en\s+faveur\s+(?:de|du)\s+(?P<p2>.+?)(?P<rest>(?:,?\s+(?:pour|afin|de\s+(?=s'|[a-zé]+(?:er|ir|re)\b)|d'(?=[a-zé]+(?:er|ir|re)\b)|qui|au sujet|relative|concernant)\b.*)?)$"):
            m = re.match(pat, main.rstrip(". "))
            if m:
                res["type"] = "procuration"
                res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(m.group("p2"))
                p1_role, p2_role = "principal (grants power of attorney)", "attorney (agent)"
                res["others"] = others_from(m.group("rest") or "")
                res["poa_purpose"] = (m.group("rest") or "").strip(" ,.")
                break
    # ---- charter
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+(?:loue et nolise|nolise|fr[èe]te|affr[èe]te)\s+(?P<rest>.*)$", main)
        if m:
            res["type"] = "affretement"
            mm = re.search(r"\s(?:à|au|aux)\s+(.+?)(?=,?\s+(?:pour|qui|de\s+\d|moyennant)\b|[.;]|$)", m.group("rest"))
            res["p1"] = parse_people(m.group("p1"))
            res["p2"] = parse_people(mm.group(1)) if mm else []
            p1_role, p2_role = "shipowner / master (lets the ship)", "charterer"
            am = AMT_RE.search(m.group("rest"))
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
        m2 = re.match(r"^Affr[èe]tement\s+(?:pour\s+\S+\s+)?(?:d'une?\s+)?(?P<ship>.+?)\s+par\s+(?P<p2>.+?)(?=[.;]|$)", main)
        if not res["type"] and m2:
            res["type"] = "affretement"
            res["p2"] = parse_people(m2.group("p2"))
            mm = re.search(r"patron\s+([A-Z][^,]+?)(?:,\s*(de\s+[A-Z][^,]+))?(?:,|\s+par\b)", main)
            if mm:
                res["p1"] = [{"name": mm.group(1).strip(), "origins": [mm.group(2)] if mm.group(2) else [], "roles": ["ship master (patron)"]}]
            p1_role, p2_role = "shipowner / master (lets the ship)", "charterer"
            am = AMT_RE.search(main)
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- summons / protest
    if not res["type"]:
        m = (re.match(r"^(?P<p1>.+?)\s+(?-i:somme(?:nt)?)\s+(?P<p2>(?:le |les |M\W*\s*|au |aux )?.+?)\s+(?:de|d')\s", main)
             or re.match(r"^Sommation\s+(?:adress[ée]e|faite)\s+par\s+(?P<p1>.+?)\s+(?:à|au|aux)\s+(?P<p2>.+?)(?=,?\s+(?:au sujet|pour|de)\b|[.;]|$)", main))
        if m:
            res["type"] = "protestation"
            res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(m.group("p2"))
            p1_role, p2_role = "summoning party", "party summoned"
    if not res["type"]:
        m = (re.match(r"^(?P<p1>.+?)\s+protesten?t?\s+(?:contre|au sujet)\s*(?P<p2>.*?)(?=,?\s+(?:au sujet|pour|qui)\b|[.;]|$)", main)
             or re.match(r"^Protestation\s+(?:de|du)\s+(?P<p1>.+?)\s+contre\s+(?P<p2>.+?)(?=,?\s+(?:au sujet|pour)\b|[.;]|$)", main))
        if m:
            res["type"] = "protestation"
            p2 = m.group("p2") or ""
            res["p1"] = parse_people(m.group("p1"))
            res["p2"] = parse_people(re.sub(r"^(?:le|la|les)\s+", "", p2)) if NAME_START.match(p2.strip() or "x") else []
            p1_role, p2_role = "protesting party", "party protested against"
    # ---- surety
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+se\s+porten?t?\s+(?:caution|garants?)\s+(?:pour|de)\s+(?P<p2>.+?)(?=,?\s+(?:pour|envers|vis-à-vis|qui|de\s+\d)\b|[.;]|$)", main)
        if m:
            res["type"] = "caution"
            res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(m.group("p2"))
            p1_role, p2_role = "guarantor", "principal debtor (guaranteed)"
            am = AMT_RE.search(main)
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- agreement / convention / marriage between
    if not res["type"]:
        m = re.match(r"^(?P<kind>Accord|Convention|Transaction|Promesse de mariage|Contrat)\s+(?:intervenu\s+)?entre\s+(?P<p1>.+?)\s+et\s+(?P<p2>[A-ZÀ-Ý].+?)(?=,?\s+(?:au sujet|pour|relati|concernant)\b|[.;]|$)", main)
        if m:
            res["type"] = "mariage" if "mariage" in m.group("kind").lower() else "accord"
            res["p1"], res["p2"] = parse_people(m.group("p1")), parse_people(m.group("p2"))
            p1_role = p2_role = "party to the agreement" if res["type"] == "accord" else "spouse-to-be"
    # ---- consular decision
    if not res["type"]:
        m = re.match(r"^D[ée]cision\s+(?:de|du|d')\s*(?P<p1>.+?)\s+(?:au sujet|concernant|sur)\b(?P<rest>.*)$", main)
        if m:
            res["type"] = "decision"
            res["p1"] = parse_people(m.group("p1"))
            p1_role = "judge (consular authority)"
            mm = re.search(r"entre\s+(?P<a>[A-ZÀ-Ý].+?)\s+et\s+(?P<b>[A-ZÀ-Ý].+?)(?=[.;]|$)", m.group("rest"))
            if mm:
                res["p2"] = parse_people(mm.group("a")) + parse_people(mm.group("b"))
                p2_role = "litigant"
            res["decision"] = main
    # ---- sale / cession
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+(?:a\s+|ont\s+)?(?:vend(?:ent)?|vendu|c[èe]de(?:nt)?|c[ée]d[ée])\s+(?P<rest>.*)$", main)
        if m and NAME_START.match(m.group("p1")):
            res["type"] = "vente_cession"
            mm = re.search(r"\s(?:à|au|aux)\s+(?P<p2>[A-ZÀ-Ý].+?)(?=,?\s+(?:pour|moyennant|qui|de\s+\d)\b|[.;]|$)", m.group("rest"))
            res["p1"] = parse_people(m.group("p1"))
            res["p2"] = parse_people(mm.group("p2")) if mm else []
            p1_role, p2_role = "seller / transferor", "buyer / transferee"
            am = AMT_RE.search(m.group("rest"))
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- undertaking: "X s'engage/promet/s'oblige ... à/envers Y"
    if not res["type"]:
        m = re.match(r"^(?P<p1>.+?)\s+(?:s'engagen?t?|s'obligen?t?|prometten?t?|promet)\s+(?P<rest>.*)$", main)
        if m and NAME_START.match(m.group("p1")):
            res["type"] = "engagement"
            res["p1"] = parse_people(m.group("p1"))
            mm = (re.search(r"(?:vis-à-vis|envers)\s+(?:de\s+|du\s+)?(?P<p2>[A-ZÀ-Ý].+?)(?=,?\s+(?:à|de|pour|qui)\b|[.;]|$)", m.group("rest"))
                  or re.search(r"^(?:à|au)\s+(?P<p2>[A-ZÀ-Ý][^,]+?(?:,\s*[a-zé][^,]+?)?)(?=,?\s+(?:de|d'|à|qu)\b)", m.group("rest"))
                  or re.search(r".*\s(?:à|au|aux)\s+(?P<p2>(?:patron\s+)?[A-ZÀ-Ý][^.;()]+?)(?=\s*[.;(]|$)", m.group("rest")))
            res["p2"] = parse_people(mm.group("p2")) if mm else []
            p1_role, p2_role = "promisor (undertakes)", "promisee"
            res["others"] = others_from(m.group("rest"))
            am = AMT_RE.search(m.group("rest"))
            if am:
                res["price"], res["currency"] = amount_value(am), norm_currency(am)
    # ---- declaration / attestation / certification
    if not res["type"]:
        m = re.match(r"^(?P<p1>[A-ZÀ-Ý].+?)\s+(?:d[ée]clare(?:nt)?|fait une d[ée]claration|font une d[ée]claration|atteste(?:nt)?|certifie(?:nt)?|expose(?:nt)?|r[ée]pond(?:ent)?|d[ée]signe(?:nt)?|relève|relèvent|nomme(?:nt)?|charge(?:nt)?|consent|consentent|remet|remettent|approuve(?:nt)?|ratifie(?:nt)?)\b(?P<rest>.*)$", main)
        if m and len(m.group("p1")) < 160:
            res["type"] = "declaration"
            res["p1"] = parse_people(m.group("p1"))
            p1_role = "declarant"
            mm = re.match(r"^\s*(?:que\s+)?(?P<p2>[A-ZÀ-Ý][^,.;]+?(?:,\s*(?:de\s+[A-Z][^,]+|[a-zé]+))?)\s+(?:a|ont|est|sont|doit|devra|lui)\b", m.group("rest"))
            if mm:
                res["p2"] = parse_people(mm.group("p2"))
                p2_role = "party concerned"
    # ---- fallback: "Copie / Enregistrement / Délibération ..." or a leading name
    if not res["type"]:
        res["type"] = act.get("act_type") or "autre"
        mm = re.search(r"\bpar\s+(?P<p1>[A-ZÀ-Ý][^.;]+?)(?=,?\s+(?:au sujet|à|pour|concernant|aux|le|du|de\s+\d)\b|[.;]|$)", main)
        lead_txt = re.split(VERB_SPLIT, main, maxsplit=1)[0]
        lead = re.match(r"^(?P<p1>.+)$", lead_txt) if (lead_txt != main and len(lead_txt) < 150
                                                         and re.match(r"^(?:(?:Le|La|Les)\s+\S+\s+)?[A-ZÀ-Ý]", lead_txt)) else None
        nm = re.match(r"^(?:R[ée]ponse|Protest(?:ation)?|D[ée]claration|Lettre|Requ[êe]te|Plainte|Testament|Codicille|Compte|Quittance(?:\s+g[ée]n[ée]rale)?|Inventaire|Signification|Attestation)\s+(?:de|du|d'|des)\s*(?P<p1>[A-ZÀ-Ý][^.;]+?)(?=,?\s+(?:au sujet|à|pour|concernant|aux|contre|par|en)\b|[.;]|$)", main)
        if nm:
            res["p1"] = parse_people(nm.group("p1"))
            p1_role = "author / party"
        elif mm and re.match(r"^(?:Copie|Enregistrement|Déclaration|Délibération|Réunion|Assemblée|Lettre|Rapport|Signification|Inventaire|Attestation|Acte|Compte)", main):
            res["p1"] = parse_people(mm.group("p1"))
            p1_role = "author / party"
        elif lead and not re.match(r"^(?:Copie|Enregistrement|Acte|Même|Suit|Le même|La même|Les mêmes|Même|Compte|R[ée]ponse|R[ée]plique|Suite|Fin|Rapport|Accord|Au sujet|Il |Elle |Ils |Ce |Cet|Cette|On |D[ée]lib|Assembl|R[ée]union|Note|Promesse|Signification|Inventaire|Ordre|Liste|Table|Requ)", main):
            res["p1"] = parse_people(lead.group("p1"))
            p1_role = "party"

    # "Le même / Les mêmes" = same party as the previous act: flagged in notes
    same_as_prev = bool(re.match(r"^(?:Le même|La même|Les mêmes|Même acte|Acte sem\w*blable)\b", main))
    if same_as_prev and not res["p1"]:
        res["p1"] = [{"name": "(same as previous act)", "origins": [], "roles": []}]

    # "de la même ville": same origin as party 1
    p1_orig = res["p1"][0]["origins"] if res["p1"] else []
    for grp in ("p2", "others"):
        for p in res[grp]:
            if "same place as previous party" in p["origins"]:
                p["origins"] = [o for o in p["origins"] if o != "same place as previous party"] + (p1_orig or ["same place as party 1"])

    # transaction roles first, then status/occupation found in the text
    for key, role in (("p1", p1_role), ("p2", p2_role)):
        for p in res[key]:
            if role:
                p["roles"] = [role] + [r for r in p["roles"] if r != role]
            if key == "p1" and re.search(r"pour (?:son|leur|sa) (?:propre )?rachat|lors de (?:son|leur) rachat|pour se racheter", s) \
                    and "ransomed captive" not in p["roles"]:
                p["roles"].append("ransomed captive")

    # ---- price & equivalents (from the whole summary)
    all_amts = list(AMT_RE.finditer(s))
    if res["price"] is None and all_amts:
        res["price"], res["currency"] = amount_value(all_amts[0]), norm_currency(all_amts[0])
        used = all_amts[0].group(0)
    else:
        used = None
        for a in all_amts:
            if res["price"] is not None and amount_value(a) == res["price"] and norm_currency(a) == res["currency"]:
                used = a.group(0)
                break
    equiv = []
    for a in all_amts:
        if a.group(0) == used:
            used = None   # skip the price itself once
            continue
        equiv.append(re.sub(r"\s+", " ", a.group(0)).strip())
    for r in RATE_RE.finditer(s):
        txt = r.group(0)
        if not any(txt in e for e in equiv) and not (res["currency"] and txt in res["currency"]):
            equiv.append(txt)
    res["equiv"] = equiv

    # ---- transaction type in English, with redemption taking precedence for ransom money
    ttype = res["type"]
    ransom = bool(re.search(r"rach[ae]t|rachep|racheter|se racheter", s, re.I))
    if ttype == "rachat":
        en = "Redemption"
    elif ransom and ttype in ("reconnaissance_dette", "quittance", "engagement", "caution", "accord"):
        en = "Redemption"
    else:
        en = TYPE_EN.get(ttype, "Other")
    res["type_en"] = en

    # ---- matter
    purpose = _purpose(s)
    matter_bits = []
    base = {
        "reconnaissance_dette": "Acknowledgement of debt", "quittance": "Receipt / discharge",
        "rachat": "Redemption of a captive", "procuration": "Power of attorney",
        "affretement": "Charter of a ship", "protestation": "Protest / summons",
        "caution": "Surety", "accord": "Agreement", "mariage": "Promise of marriage",
        "decision": "Consular decision", "vente_cession": "Sale / transfer",
        "engagement": "Undertaking", "declaration": "Declaration",
    }.get(ttype)
    if base:
        matter_bits.append(base)
    if purpose:
        matter_bits.append(f"for {purpose}")
    if ttype == "procuration" and res.get("poa_purpose"):
        matter_bits.append(f"({res['poa_purpose'][:150]})")
    ship = re.findall(r"«\s*([^»]{2,60}?)\s*»", s)
    if ship and re.search(r"navire|vaisseau|barque|galion|saetia|nef|polacre|bateau|tartane|patron", s, re.I):
        matter_bits.append("ship « " + ship[0] + " »")
    if not base:
        matter_bits.insert(0, re.sub(r"\s+", " ", main)[:200])
    res["matter"] = "; ".join(matter_bits)

    # ---- keywords
    kws = []
    for pat, kw in KEYWORDS:
        if re.search(pat, s, re.I) and kw not in kws:
            kws.append(kw)
    if res["type_en"] == "Redemption" and "ransom" not in kws:
        kws.insert(0, "ransom")
    if res["type"] == "reconnaissance_dette" and "debt" not in kws:
        kws.insert(0, "debt")
    res["keywords"] = "; ".join(kws) or None

    # ---- decision
    if ttype in ("decision", "deliberation") and not res["decision"]:
        res["decision"] = s
    if not res["decision"]:
        dm = re.search(r"((?:Les experts|Il est (?:décidé|convenu)|La nation|L'assemblée|Le consul)[^.]*(?:décide|décident|arrêt|convien|fix|condamn|ordonn)[^.]*\.)", s)
        if dm:
            res["decision"] = dm.group(1)

    # ---- notes: remaining sentences and remarks, condition, language, same-as-previous
    notes = []
    fm = re.match(r"^\W*(?:E[nt]i?\s*italien\W*)?\(\s*(\d+)\s*\)", act["summary"].strip())
    if fm:
        notes.append(f"Folio {fm.group(1)} of the register.")
    if act.get("language"):
        notes.append(f"Act written in {act['language']}.")
    if same_as_prev:
        notes.append("Refers to the parties of the previous act.")
    notes += [x for x in rest_sents]
    paren = re.findall(r"\(([^()]{4,})\)", main)
    notes += [f"({p})" for p in paren if not re.match(r"^\?$", p)]
    flags = [lab for k, lab in (("struck_out", "struck out (biffé)"), ("illegible", "partly illegible"),
                                ("unfinished", "unfinished"), ("damaged", "damaged/effaced")) if act.get(k)]
    if flags:
        notes.append("Condition: " + ", ".join(flags) + ".")
    res["notes"] = " ".join(dict.fromkeys(n.strip() for n in notes if n.strip())) or None
    return res


def to_columns(act, res):
    """Flatten an extraction into the transaction columns."""
    MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
              "septembre", "octobre", "novembre", "décembre"]
    md = None
    if act.get("month"):
        md = (f"{act['day']} " if act.get("day") else "") + MONTHS[act["month"] - 1]
    elif act.get("undated"):
        md = "s. d."
    return {
        "month_day": md,
        "transaction_type": res["type_en"],
        "party1_name": join_people(res["p1"], "name"),
        "party1_origins": join_people(res["p1"], "origins"),
        "party1_role": join_people(res["p1"], "roles"),
        "party2_name": join_people(res["p2"], "name"),
        "party2_origins": join_people(res["p2"], "origins"),
        "party2_role": join_people(res["p2"], "roles"),
        "other_parties_names": join_people(res["others"], "name"),
        "other_parties_origins": join_people(res["others"], "origins"),
        "other_parties_role": join_people(res["others"], "roles"),
        "matter": res["matter"],
        "price": res["price"],
        "currency": res["currency"],
        "other_equivalent": "; ".join(res["equiv"]) or None,
        "keywords": res["keywords"],
        "decision": res["decision"],
        "notes": res["notes"],
    }
