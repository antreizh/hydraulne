#!/usr/bin/env python3
"""Historique des marées réelles à Guily Glaz (Port-Launay) et à Brest, avec coefficient et météo de Brest.

Objectif : pouvoir analyser plus tard la corrélation entre hauteur d'eau, coefficient de marée,
surcote et vent (et pression atmosphérique, cause principale de la surcote avec le vent).
Lancé par le workflow GitHub « Actualisation des relevés » (3 fois par jour, sans ordinateur allumé).

Fichiers produits (un fichier par mois, heures en UTC, séparateur « ; ») :

  data/maree-guily-glaz/AAAA-MM.csv   date_utc;hauteur_mm;coef;vent_ms;vent_dir;pression_hpa
      marégraphe de Guily Glaz (Hub'Eau J382183001), une mesure toutes les 5 min,
      hauteur en mm à l'échelle de la station.
  data/maree-brest/AAAA-MM.csv        date_utc;hauteur_mm;coef;vent_ms;vent_dir;pression_hpa
      marégraphe de Brest (SHOM, REFMAR n° 3 ; secours : IOC « bres »), une valeur toutes les 5 min
      (moyenne des mesures minute de t-2 à t+2 min, qui lisse le clapot), hauteur en mm au-dessus du
      zéro hydrographique (même référence que les prédictions du SHOM).
  data/maree-brest/predictions-shom.csv      date_utc;type;hauteur_m;coef
      prédictions officielles du SHOM pour Brest (vignette publique : jour même + 6 jours), conservées
      au fil des jours ; la plus récente remplace l'ancienne pour une même marée.
  data/maree-brest/pleines-basses-mers.csv   une ligne par pleine ou basse mer prédite à Brest :
      date_utc_prevue;type;hauteur_prevue_m;coef;date_utc_mesuree;hauteur_mesuree_m;surcote_m;
      vent_ms;vent_dir;pression_hpa;gg_date_utc;gg_hauteur_mm
      surcote = hauteur extrême mesurée − hauteur prédite (« surcote de pleine/basse mer ») ;
      gg_… = extrême correspondant mesuré à Guily Glaz (de 1 h avant à 3 h après l'heure prévue à Brest).
  data/meteo-brest/AAAA-MM.csv        date_utc;vent_ms;vent_dir;rafale_ms;pression_hpa
      Météo-France, station Brest-Guipavas (29075001), fichier horaire public du Finistère (sans clé,
      publié chaque matin avec les observations jusqu'à la nuit) : FF vent moyen sur 10 min (m/s),
      DD direction (degrés, d'où vient le vent), FXI rafale max de l'heure, PMER pression au niveau de la mer.

Colonnes coef / vent / pression : coef = coefficient de la pleine mer de Brest la plus proche (à 6 h 13 près) ;
vent et pression = observation horaire la plus proche. Vides tant qu'ils ne sont pas connus : ils sont
complétés aux passages suivants (le vent arrive avec environ une demi-journée de retard). Le SHOM ne
publiant pas de prédictions passées, les mesures antérieures au 5 novembre 2026 n'ont pas de coefficient.

Rien n'est jamais effacé ; une valeur corrigée par la source remplace l'ancienne.
Usage : python3 archiver_maregraphe.py <dossier du dépôt>
"""
import csv, datetime as dt, glob, gzip, html as htmlmod, io, json, os, re, sys, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

UA = {"User-Agent": "antreizh-hydraulne/1.0 (+https://appli.antreizh.fr)"}
UTC = dt.timezone.utc
PARIS = ZoneInfo("Europe/Paris")
DEBUT = dt.datetime(2026, 9, 30, tzinfo=UTC)      # début de l'historique (premières mesures de Guily Glaz)
CHEVAUCHEMENT_H = 6                               # on redemande les 6 dernières heures (valeurs provisoires)
PAS = dt.timedelta(minutes=5)
DEMI_CYCLE = dt.timedelta(hours=6, minutes=13)

GG_STATION = "J382183001"                         # Aulne à Guily Glaz (Port-Launay), marégraphe
GG_API = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr"
GG_RETOUR_MAX_JOURS = 35                          # Hub'Eau ne garde le temps réel qu'environ un mois
REFMAR = "https://services.data.shom.fr/maregraphie/observation/json/3?sources=1&dtStart={a}&dtEnd={b}"
IOC = "https://www.ioc-sealevelmonitoring.org/service.php?query=data&code=bres&timestart={a}&timestop={b}&format=json"
VIGNETTE = "https://services.data.shom.fr/hdm/vignette/grande/BREST?locale=fr"
S3 = "https://meteofrance.s3.sbg.io.cloud.ovh.net"
MF_PREFIXE = "data/synchro_ftp/BASE/HOR/H_29_latest"
MF_STATION = "29075001"                           # BREST-GUIPAVAS

COLS_MAREE = ["date_utc", "hauteur_mm", "coef", "vent_ms", "vent_dir", "pression_hpa"]
COLS_METEO = ["date_utc", "vent_ms", "vent_dir", "rafale_ms", "pression_hpa"]
COLS_PRED = ["date_utc", "type", "hauteur_m", "coef"]
COLS_EXT = ["date_utc_prevue", "type", "hauteur_prevue_m", "coef", "date_utc_mesuree", "hauteur_mesuree_m",
            "surcote_m", "vent_ms", "vent_dir", "pression_hpa", "gg_date_utc", "gg_hauteur_mm"]


# ---------------------------------------------------------------- outils
def lire_url(url, essais=3, brut=False, binaire=False):
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                c = r.read()
                return c if binaire else c.decode("utf-8", "replace") if brut else json.loads(c)
        except Exception as e:
            if i == essais - 1:
                raise
            print(f"  nouvel essai ({e})", flush=True); time.sleep(15)


def cle(t):
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def date(k):
    return dt.datetime.fromisoformat(k.replace("Z", "+00:00"))


def charger_mensuel(dossier):
    """{date_utc: {colonne: valeur}} de tous les fichiers AAAA-MM.csv du dossier."""
    d = {}
    for f in sorted(glob.glob(os.path.join(dossier, "[0-9][0-9][0-9][0-9]-[0-9][0-9].csv"))):
        with open(f, encoding="utf-8") as fh:
            r = csv.reader(fh, delimiter=";")
            tete = next(r, None)
            for row in r:
                if row and row[0] != "date_utc":
                    d[row[0]] = {c: (row[i] if i < len(row) else "") for i, c in enumerate(tete)}
    return d


def ecrire_texte(f, texte):
    ancien = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
    if texte == ancien:
        return False
    os.makedirs(os.path.dirname(f), exist_ok=True)
    with open(f, "w", encoding="utf-8", newline="") as fh:
        fh.write(texte)
    return True


def table(cols, lignes):
    return ";".join(cols) + "\n" + "".join(";".join(str(l.get(c, "")) for c in cols) + "\n" for l in lignes)


def ecrire_mensuel(dossier, cols, d):
    """Réécrit les fichiers mensuels dont le contenu change ; renvoie les mois modifiés."""
    modifies = []
    for m in sorted({k[:7] for k in d}):
        lignes = [dict(d[k], date_utc=k) for k in sorted(d) if k.startswith(m)]
        if ecrire_texte(os.path.join(dossier, m + ".csv"), table(cols, lignes)):
            modifies.append(m)
    return modifies


def plus_proche(liste, t, ecart_max):
    """liste triée [(datetime, valeur)] → valeur la plus proche de t (à ecart_max près), sinon None."""
    import bisect
    i = bisect.bisect_left(liste, (t,))
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(liste) and abs(liste[j][0] - t) <= ecart_max:
            if best is None or abs(liste[j][0] - t) < abs(best[0] - t):
                best = liste[j]
    return best[1] if best else None


# ---------------------------------------------------------------- prédictions SHOM pour Brest
def lire_vignette():
    """Vignette SHOM (JavaScript qui écrit un tableau HTML) → [(datetime UTC, type, hauteur_m, coef ou '')]."""
    js = lire_url(VIGNETTE, brut=True)
    page = "".join(re.findall(r"ifrm\.document\.write\('((?:[^'\\]|\\.)*)'\);", js)).replace("\\'", "'").replace('\\"', '"')
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
    out, jour = [], None
    for m in re.finditer(r"<h2>\s*\w+\s+(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})\s*</h2>|<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        if m.group(1):
            nom = htmlmod.unescape(m.group(2)).lower()
            if nom in mois:
                jour = dt.date(int(m.group(3)), mois.index(nom) + 1, int(m.group(1)))
            continue
        ligne = m.group(4)
        d = re.search(r"<th[^>]*>\s*(\d{2})/(\d{2})/(\d{4})\s*</th>", ligne)
        if d:
            jour = dt.date(int(d.group(3)), int(d.group(2)), int(d.group(1)))
        elif jour:
            cases = [htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", ligne, flags=re.S)]
            if len(cases) >= 3 and cases[0] in ("PM", "BM") and re.fullmatch(r"\d{1,2}:\d{2}", cases[1]):
                hh, mm = map(int, cases[1].split(":"))
                loc = dt.datetime(jour.year, jour.month, jour.day, hh, mm, tzinfo=PARIS)
                coef = cases[3] if len(cases) > 3 and re.fullmatch(r"\d{2,3}", cases[3]) else ""
                out.append((loc.astimezone(UTC), cases[0], cases[2].replace(",", "."), coef))
    return out


def maj_predictions(f, ancien_f):
    """Ajoute les prédictions du jour à l'archive ; renvoie [(datetime UTC, type, h, coef)] triée."""
    archive = []
    for src in (f, ancien_f):                      # ancien_f : ancien emplacement (dossier Guily Glaz)
        if os.path.exists(src):
            with open(src, encoding="utf-8") as fh:
                for row in csv.reader(fh, delimiter=";"):
                    if len(row) == 4 and row[0] != "date_utc":
                        archive.append((date(row[0]), row[1], row[2], row[3]))
    archive = sorted(set(archive))
    try:
        neuves = lire_vignette()
    except Exception as e:
        print(f"::warning::Vignette SHOM injoignable, prédictions non actualisées ({e})")
        neuves = []
    # une même marée (même type à moins de 2 h) : la prédiction la plus récente remplace l'ancienne
    archive = [a for a in archive if not any(a[1] == n[1] and abs(a[0] - n[0]) < dt.timedelta(hours=2) for n in neuves)] + neuves
    archive.sort()
    if archive:
        ecrire_texte(f, table(COLS_PRED, [dict(zip(COLS_PRED, (cle(a[0]),) + a[1:])) for a in archive]))
        if os.path.exists(ancien_f):
            os.remove(ancien_f)
        print(f"SHOM Brest : {len(neuves)} marées lues, {len(archive)} archivées "
              f"(du {archive[0][0]:%d/%m/%Y} au {archive[-1][0]:%d/%m/%Y})")
    return archive


# ---------------------------------------------------------------- Guily Glaz (Hub'Eau)
def guily_glaz(depuis):
    out, page = {}, 1
    while True:
        q = urllib.parse.urlencode({"code_entite": GG_STATION, "grandeur_hydro": "H", "size": 10000, "page": page,
                                    "date_debut_obs": cle(depuis), "sort": "asc", "format": "json"})
        j = lire_url(f"{GG_API}?{q}")
        data = j.get("data") or []
        for r in data:
            if r.get("date_obs") and r.get("resultat_obs") is not None:
                out[cle(date(r["date_obs"]))] = str(int(round(float(r["resultat_obs"]))))
        if len(data) < 10000 or not j.get("next"):
            return out
        page += 1


# ---------------------------------------------------------------- Brest (SHOM REFMAR, secours IOC)
def brest_minutes(a, b):
    """Mesures minute entre a et b → {datetime: hauteur m}."""
    try:
        data = lire_url(REFMAR.format(a=cle(a), b=cle(b)))["data"]
        return {dt.datetime.strptime(x["timestamp"], "%Y/%m/%d %H:%M:%S").replace(tzinfo=UTC): float(x["value"])
                for x in data if x.get("value") is not None}
    except Exception as e:
        print(f"  REFMAR injoignable ({e}), secours IOC")
        data = lire_url(IOC.format(a=a.strftime("%Y-%m-%dT%H:%M"), b=b.strftime("%Y-%m-%dT%H:%M")))
        return {dt.datetime.strptime(x["stime"].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=UTC): float(x["slevel"])
                for x in data if x.get("slevel") is not None}


def brest(depuis, jusque):
    """Valeurs toutes les 5 min (moyenne de t-2 à t+2 min, au moins 3 mesures) → {date_utc: hauteur_mm}."""
    out, a = {}, depuis.replace(minute=depuis.minute - depuis.minute % 5, second=0, microsecond=0)
    while a < jusque:
        b = min(a + dt.timedelta(days=1), jusque)
        mn = brest_minutes(a - dt.timedelta(minutes=2), b + dt.timedelta(minutes=2))
        t = a
        while t <= b:
            v = [mn[t + dt.timedelta(minutes=i)] for i in range(-2, 3) if t + dt.timedelta(minutes=i) in mn]
            if len(v) >= 3:
                out[cle(t)] = str(int(round(sum(v) / len(v) * 1000)))
            t += PAS
        a = b
    return out


# ---------------------------------------------------------------- météo Brest-Guipavas (Météo-France)
def meteo_brest(depuis):
    xml = lire_url(f"{S3}/?list-type=2&prefix={MF_PREFIXE}", brut=True)
    cles = sorted(set(re.findall(r"<Key>(" + re.escape(MF_PREFIXE) + r"-\d{4}-(\d{4})\.csv\.gz)</Key>", xml)), key=lambda c: c[1])
    if not cles:
        raise RuntimeError("fichier horaire Météo-France introuvable")
    url = f"{S3}/{cles[-1][0]}"
    lignes = csv.reader(io.StringIO(gzip.decompress(lire_url(url, binaire=True)).decode("utf-8", "replace")), delimiter=";")
    tete = [h.strip() for h in next(lignes)]
    ix = {c: tete.index(c) for c in ("NUM_POSTE", "AAAAMMJJHH", "FF", "DD", "FXI", "PMER")}
    out, mini = {}, depuis.strftime("%Y%m%d%H")
    for l in lignes:
        if len(l) > max(ix.values()) and l[ix["NUM_POSTE"]].strip() == MF_STATION and l[ix["AAAAMMJJHH"]].strip() >= mini:
            h = l[ix["AAAAMMJJHH"]].strip()
            t = dt.datetime(int(h[:4]), int(h[4:6]), int(h[6:8]), int(h[8:10]), tzinfo=UTC)
            out[cle(t)] = {"vent_ms": l[ix["FF"]].strip(), "vent_dir": l[ix["DD"]].strip(),
                           "rafale_ms": l[ix["FXI"]].strip(), "pression_hpa": l[ix["PMER"]].strip()}
    print(f"Météo-France {url.rsplit('/', 1)[1]} : {len(out)} heures pour Brest-Guipavas, dernière {max(out) if out else '—'}")
    return out


# ---------------------------------------------------------------- assemblage
def completer(mesures, pm, meteo_l):
    """Ajoute coef, vent et pression à chaque mesure {date_utc: {hauteur_mm: …}}."""
    for k, l in mesures.items():
        t = date(k)
        l["coef"] = plus_proche(pm, t, DEMI_CYCLE) or l.get("coef", "")
        m = plus_proche(meteo_l, t, dt.timedelta(minutes=30))
        for c in ("vent_ms", "vent_dir", "pression_hpa"):
            l[c] = (m or {}).get(c, "") or l.get(c, "")


def extremes(pred, brest_m, gg_m, pm, meteo_l):
    """Une ligne par pleine/basse mer prédite à Brest, avec l'extrême mesuré, la surcote et la météo."""
    br = sorted((date(k), int(v["hauteur_mm"])) for k, v in brest_m.items() if v.get("hauteur_mm"))
    gg = sorted((date(k), int(v["hauteur_mm"])) for k, v in gg_m.items() if v.get("hauteur_mm"))
    import bisect

    def fenetre(serie, a, b):
        return serie[bisect.bisect_left(serie, (a,)):bisect.bisect_right(serie, (b, 10 ** 9))]

    out = []
    for t, ty, h, coef in pred:
        l = {"date_utc_prevue": cle(t), "type": ty, "hauteur_prevue_m": h, "coef": coef or (plus_proche(pm, t, DEMI_CYCLE) or "")}
        f = fenetre(br, t - dt.timedelta(minutes=90), t + dt.timedelta(minutes=90))
        if len(f) >= 30:                           # fenêtre de 3 h presque complète (37 valeurs attendues)
            tx, hx = (max if ty == "PM" else min)(f, key=lambda x: x[1])
            l.update(date_utc_mesuree=cle(tx), hauteur_mesuree_m=f"{hx / 1000:.3f}", surcote_m=f"{hx / 1000 - float(h):.3f}")
        m = plus_proche(meteo_l, t, dt.timedelta(minutes=30)) or {}
        l.update(vent_ms=m.get("vent_ms", ""), vent_dir=m.get("vent_dir", ""), pression_hpa=m.get("pression_hpa", ""))
        g = fenetre(gg, t - dt.timedelta(hours=1), t + dt.timedelta(hours=3))
        if len(g) >= 40:
            tg, hg = (max if ty == "PM" else min)(g, key=lambda x: x[1])
            l.update(gg_date_utc=cle(tg), gg_hauteur_mm=hg)
        if t <= dt.datetime.now(UTC):
            out.append(l)
    return out


def depuis_archive(d, maintenant, retour_max=None):
    plancher = DEBUT if retour_max is None else max(DEBUT, maintenant - dt.timedelta(days=retour_max))
    if d:
        return max(date(max(d)) - dt.timedelta(hours=CHEVAUCHEMENT_H), plancher)
    return plancher


def main(base):
    maintenant = dt.datetime.now(UTC)
    d_gg = os.path.join(base, "data", "maree-guily-glaz")
    d_br = os.path.join(base, "data", "maree-brest")
    d_mt = os.path.join(base, "data", "meteo-brest")

    pred = maj_predictions(os.path.join(d_br, "predictions-shom.csv"), os.path.join(d_gg, "brest-shom.csv"))
    pm = [(t, c) for t, ty, h, c in pred if ty == "PM" and c]

    # météo horaire de Brest-Guipavas
    meteo = charger_mensuel(d_mt)
    try:
        neuves = meteo_brest(depuis_archive(meteo, maintenant) - dt.timedelta(days=2))
        for k, v in neuves.items():
            if any(v.values()):
                meteo[k] = v
    except Exception as e:
        print(f"::warning::Météo-France injoignable, vent non actualisé ({e})")
    mois_mt = ecrire_mensuel(d_mt, COLS_METEO, meteo)
    meteo_l = sorted((date(k), v) for k, v in meteo.items())

    # marégraphes
    resume = []
    for nom, dossier, lire, retour in (("Guily Glaz", d_gg, lambda a: guily_glaz(a), GG_RETOUR_MAX_JOURS),
                                       ("Brest", d_br, lambda a: brest(a, maintenant), None)):
        archive = charger_mensuel(dossier)
        try:
            neuves = lire(depuis_archive(archive, maintenant, retour))
        except Exception as e:
            print(f"::warning::{nom} : mesures injoignables, archive inchangée ({e})")
            neuves = {}
        n = 0
        for k, v in neuves.items():
            if archive.get(k, {}).get("hauteur_mm") != v:
                archive.setdefault(k, {})["hauteur_mm"] = v; n += 1
        completer(archive, pm, meteo_l)
        mois = ecrire_mensuel(dossier, COLS_MAREE, archive)
        avec = lambda c: sum(1 for l in archive.values() if l.get(c))
        resume.append(f"{nom} : {n} mesure(s) ajoutée(s) ou corrigée(s), {len(archive)} au total"
                      + (f" (du {min(archive)} au {max(archive)})" if archive else "")
                      + f", {avec('coef')} avec coefficient, {avec('vent_ms')} avec vent ; fichiers réécrits : {', '.join(mois) or 'aucun'}")
        if nom == "Guily Glaz":
            gg = archive
        else:
            br = archive

    ext = extremes(pred, br, gg, pm, meteo_l)
    ecrire_texte(os.path.join(d_br, "pleines-basses-mers.csv"), table(COLS_EXT, ext))
    resume.append(f"Brest : {sum(1 for l in ext if l.get('surcote_m'))} surcote(s) de pleine/basse mer calculée(s) ; "
                  f"météo : {len(meteo)} heures, fichiers réécrits : {', '.join(mois_mt) or 'aucun'}")
    for r in resume:
        print("::notice::" + r)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
