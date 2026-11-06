#!/usr/bin/env python3
"""Archive de toutes les mesures de hauteur d'eau du marégraphe de Guily Glaz (Port-Launay).

Hub'Eau ne conserve les mesures en temps réel qu'environ un mois. Ce script, lancé par le workflow
GitHub « Actualisation des relevés » (3 fois par jour), ajoute les nouvelles mesures à un historique
permanent rangé dans le dépôt, un fichier par mois :

    data/maree-guily-glaz/AAAA-MM.csv      colonnes : date_utc;hauteur_mm;coef
    (une ligne toutes les 5 minutes, heure UTC, hauteur en millimètres à l'échelle de la station,
     coefficient de la marée en cours : celui de la pleine mer de Brest la plus proche, à 6 h 13 près ;
     vide tant que le coefficient n'est pas connu, notamment avant le 6 novembre 2026)

Il archive aussi les prédictions officielles du SHOM pour Brest (port de référence), lues sur la vignette
publique du SHOM ; elles portent sur le jour même et les 6 jours suivants et sont conservées au fil des jours :

    data/maree-guily-glaz/brest-shom.csv   colonnes : date_utc;type;hauteur_m;coef
    (type PM ou BM, heure UTC, hauteur à Brest en mètres, coefficient pour les pleines mers)

Cet historique servira à affiner la courbe théorique du widget « Marée » (profil selon le coefficient,
décalage et hauteur par rapport à Brest, niveau plancher selon le débit de l'Aulne).

Une mesure déjà archivée n'est jamais effacée ; si Hub'Eau corrige une valeur, la nouvelle remplace l'ancienne.

Usage : python3 archiver_maregraphe.py <dossier du dépôt>
"""
import csv, datetime as dt, glob, html as htmlmod, json, os, re, sys, time, urllib.parse, urllib.request
from zoneinfo import ZoneInfo

STATION = "J382183001"      # Aulne à Guily Glaz (Port-Launay), marégraphe
API = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr"
UA = {"User-Agent": "antreizh-hydraulne/1.0 (+https://appli.antreizh.fr)"}
RETOUR_MAX_JOURS = 35       # au premier lancement (ou après une longue panne) : tout ce que Hub'Eau garde encore
CHEVAUCHEMENT_H = 6         # on redemande les 6 dernières heures archivées (valeurs provisoires parfois corrigées)
VIGNETTE = "https://services.data.shom.fr/hdm/vignette/grande/BREST?locale=fr"
PARIS = ZoneInfo("Europe/Paris")
FICHIER_BREST = "brest-shom.csv"
DEMI_CYCLE = dt.timedelta(hours=6, minutes=13)


def lire_url(url, essais=3, brut=False):
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                contenu = r.read()
                return contenu.decode("utf-8", "replace") if brut else json.loads(contenu)
        except Exception as e:
            if i == essais - 1:
                raise
            print(f"  nouvel essai ({e})", flush=True); time.sleep(15)


def charger(dossier):
    """{date_utc 'AAAA-MM-JJTHH:MM:SSZ': hauteur_mm} de toutes les mesures déjà archivées."""
    d = {}
    for f in sorted(glob.glob(os.path.join(dossier, "[0-9][0-9][0-9][0-9]-[0-9][0-9].csv"))):
        with open(f, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter=";"):
                if len(row) >= 2 and row[0] != "date_utc":
                    d[row[0]] = row[1]
    return d


# ---------------------------------------------------------------- prédictions SHOM pour Brest
def lire_vignette():
    """Vignette SHOM (JavaScript qui écrit un tableau HTML) → [(datetime UTC, type, hauteur_m, coef ou '')]."""
    js = lire_url(VIGNETTE, brut=True)
    page = "".join(re.findall(r"ifrm\.document\.write\('((?:[^'\\]|\\.)*)'\);", js)).replace("\\'", "'").replace('\\"', '"')
    page = re.sub(r"<!--.*?-->", "", page, flags=re.S)
    out, jour = [], None
    for m in re.finditer(r"<th[^>]*>\s*(\d{2})/(\d{2})/(\d{4})\s*</th>|<h2>\s*\w+\s+(\d{1,2})(?:er)?\s+(\w+)\s+(\d{4})\s*</h2>|<tr[^>]*>(.*?)</tr>", page, flags=re.S):
        if m.group(1):
            jour = dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        elif m.group(4):
            mois = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août", "septembre", "octobre", "novembre", "décembre"]
            nom = htmlmod.unescape(m.group(5)).lower()
            if nom in mois:
                jour = dt.date(int(m.group(6)), mois.index(nom) + 1, int(m.group(4)))
        elif re.search(r"<th[^>]*>\s*\d{2}/\d{2}/\d{4}\s*</th>", m.group(7)):
            j, mo, an = re.search(r"(\d{2})/(\d{2})/(\d{4})", m.group(7)).groups()
            jour = dt.date(int(an), int(mo), int(j))
        elif jour:
            cases = [htmlmod.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", m.group(7), flags=re.S)]
            if len(cases) >= 3 and cases[0] in ("PM", "BM") and re.fullmatch(r"\d{1,2}:\d{2}", cases[1]):
                hh, mm = map(int, cases[1].split(":"))
                loc = dt.datetime(jour.year, jour.month, jour.day, hh, mm, tzinfo=PARIS)
                h = cases[2].replace(",", ".")
                coef = cases[3] if len(cases) > 3 and re.fullmatch(r"\d{2,3}", cases[3]) else ""
                out.append((loc.astimezone(dt.timezone.utc), cases[0], h, coef))
    return out


def maj_brest(dossier):
    """Ajoute les prédictions du jour à l'archive Brest ; renvoie la liste complète [(datetime UTC, type, h, coef)]."""
    f = os.path.join(dossier, FICHIER_BREST)
    archive = []
    if os.path.exists(f):
        with open(f, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter=";"):
                if len(row) == 4 and row[0] != "date_utc":
                    archive.append((dt.datetime.fromisoformat(row[0].replace("Z", "+00:00")), row[1], row[2], row[3]))
    try:
        neuves = lire_vignette()
    except Exception as e:
        print(f"::warning::Vignette SHOM injoignable, coefficients non actualisés ({e})")
        neuves = []
    if neuves:
        # une même marée (même type à moins de 2 h) : la prédiction la plus récente remplace l'ancienne
        archive = [a for a in archive if not any(a[1] == n[1] and abs(a[0] - n[0]) < dt.timedelta(hours=2) for n in neuves)] + neuves
        archive.sort()
        os.makedirs(dossier, exist_ok=True)
        with open(f, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";", lineterminator="\n")
            w.writerow(["date_utc", "type", "hauteur_m", "coef"])
            for a in archive:
                w.writerow([a[0].strftime("%Y-%m-%dT%H:%M:%SZ"), a[1], a[2], a[3]])
        print(f"SHOM Brest : {len(neuves)} marées lues, {len(archive)} archivées (du {archive[0][0]:%d/%m/%Y} au {archive[-1][0]:%d/%m/%Y})")
    return archive


def coefficients(mesures, brest):
    """{date_utc: coef} : coefficient de la pleine mer de Brest la plus proche (à 6 h 13 près)."""
    pm = [(t, c) for t, ty, h, c in brest if ty == "PM" and c]
    out, i = {}, 0
    for k in sorted(mesures):
        t = dt.datetime.fromisoformat(k.replace("Z", "+00:00"))
        while i + 1 < len(pm) and abs(pm[i + 1][0] - t) <= abs(pm[i][0] - t):
            i += 1
        out[k] = pm[i][1] if pm and abs(pm[i][0] - t) <= DEMI_CYCLE else ""
    return out


def telecharger(depuis):
    """Mesures depuis la date donnée (UTC), page par page."""
    out, page = {}, 1
    while True:
        q = urllib.parse.urlencode({"code_entite": STATION, "grandeur_hydro": "H", "size": 10000, "page": page,
                                    "date_debut_obs": depuis.strftime("%Y-%m-%dT%H:%M:%SZ"), "sort": "asc", "format": "json"})
        j = lire_url(f"{API}?{q}")
        data = j.get("data") or []
        for r in data:
            if r.get("date_obs") and r.get("resultat_obs") is not None:
                t = dt.datetime.fromisoformat(r["date_obs"].replace("Z", "+00:00")).astimezone(dt.timezone.utc)
                out[t.strftime("%Y-%m-%dT%H:%M:%SZ")] = str(int(round(float(r["resultat_obs"]))))
        if len(data) < 10000 or not j.get("next"):
            return out
        page += 1


def ecrire(dossier, mesures, coefs):
    """Réécrit les fichiers mensuels dont le contenu change ; renvoie les mois modifiés."""
    os.makedirs(dossier, exist_ok=True)
    modifies = []
    for m in sorted({k[:7] for k in mesures}):
        f = os.path.join(dossier, m + ".csv")
        texte = "date_utc;hauteur_mm;coef\n" + "".join("%s;%s;%s\n" % (k, mesures[k], coefs.get(k, ""))
                                                     for k in sorted(mesures) if k.startswith(m))
        ancien = open(f, encoding="utf-8").read() if os.path.exists(f) else ""
        if texte != ancien:
            with open(f, "w", encoding="utf-8", newline="") as fh:
                fh.write(texte)
            modifies.append(m)
    return modifies


def main(base):
    dossier = os.path.join(base, "data", "maree-guily-glaz")
    archive = charger(dossier)
    maintenant = dt.datetime.now(dt.timezone.utc)
    if archive:
        dernier = dt.datetime.fromisoformat(max(archive).replace("Z", "+00:00"))
        depuis = max(dernier - dt.timedelta(hours=CHEVAUCHEMENT_H), maintenant - dt.timedelta(days=RETOUR_MAX_JOURS))
    else:
        depuis = maintenant - dt.timedelta(days=RETOUR_MAX_JOURS)
    brest = maj_brest(dossier)
    try:
        neuves = telecharger(depuis)
    except Exception as e:
        print(f"::warning::Hub'Eau injoignable, mesures inchangées ({e})")
        neuves = {}
    modifs = {k: v for k, v in neuves.items() if archive.get(k) != v}
    archive.update(modifs)
    if not archive:
        return 0
    coefs = coefficients(archive, brest)
    mois = ecrire(dossier, archive, coefs)
    avec = sum(1 for c in coefs.values() if c)
    print(f"::notice::Guily Glaz : {len(modifs)} mesure(s) ajoutée(s) ou corrigée(s), {len(archive)} au total "
          f"(du {min(archive)} au {max(archive)}), {avec} avec coefficient ; fichiers réécrits : {', '.join(mois) or 'aucun'}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
