#!/usr/bin/env python3
"""Archive de toutes les mesures de hauteur d'eau du marégraphe de Guily Glaz (Port-Launay).

Hub'Eau ne conserve les mesures en temps réel qu'environ un mois. Ce script, lancé par le workflow
GitHub « Actualisation des relevés » (3 fois par jour), ajoute les nouvelles mesures à un historique
permanent rangé dans le dépôt, un fichier par mois :

    data/maree-guily-glaz/AAAA-MM.csv      colonnes : date_utc;hauteur_mm
    (une ligne toutes les 5 minutes, heure UTC, hauteur en millimètres à l'échelle de la station)

Cet historique servira à affiner la courbe théorique du widget « Marée » (profil selon le coefficient,
décalage et hauteur par rapport à Brest, niveau plancher selon le débit de l'Aulne).

Une mesure déjà archivée n'est jamais effacée ; si Hub'Eau corrige une valeur, la nouvelle remplace l'ancienne.

Usage : python3 archiver_maregraphe.py <dossier du dépôt>
"""
import csv, datetime as dt, glob, json, os, sys, time, urllib.parse, urllib.request

STATION = "J382183001"      # Aulne à Guily Glaz (Port-Launay), marégraphe
API = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr"
UA = {"User-Agent": "antreizh-hydraulne/1.0 (+https://appli.antreizh.fr)"}
RETOUR_MAX_JOURS = 35       # au premier lancement (ou après une longue panne) : tout ce que Hub'Eau garde encore
CHEVAUCHEMENT_H = 6         # on redemande les 6 dernières heures archivées (valeurs provisoires parfois corrigées)


def lire_url(url, essais=3):
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=90) as r:
                return json.loads(r.read())
        except Exception as e:
            if i == essais - 1:
                raise
            print(f"  nouvel essai ({e})", flush=True); time.sleep(15)


def charger(dossier):
    """{date_utc 'AAAA-MM-JJTHH:MM:SSZ': hauteur_mm} de toutes les mesures déjà archivées."""
    d = {}
    for f in sorted(glob.glob(os.path.join(dossier, "*.csv"))):
        with open(f, encoding="utf-8") as fh:
            for row in csv.reader(fh, delimiter=";"):
                if len(row) == 2 and row[0] != "date_utc":
                    d[row[0]] = row[1]
    return d


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


def ecrire(dossier, mesures, mois):
    os.makedirs(dossier, exist_ok=True)
    for m in sorted(mois):
        lignes = sorted((k, v) for k, v in mesures.items() if k.startswith(m))
        with open(os.path.join(dossier, m + ".csv"), "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh, delimiter=";", lineterminator="\n")
            w.writerow(["date_utc", "hauteur_mm"])
            w.writerows(lignes)


def main(base):
    dossier = os.path.join(base, "data", "maree-guily-glaz")
    archive = charger(dossier)
    maintenant = dt.datetime.now(dt.timezone.utc)
    if archive:
        dernier = dt.datetime.fromisoformat(max(archive).replace("Z", "+00:00"))
        depuis = max(dernier - dt.timedelta(hours=CHEVAUCHEMENT_H), maintenant - dt.timedelta(days=RETOUR_MAX_JOURS))
    else:
        depuis = maintenant - dt.timedelta(days=RETOUR_MAX_JOURS)
    try:
        neuves = telecharger(depuis)
    except Exception as e:
        print(f"::warning::Hub'Eau injoignable, archive inchangée ({e})")
        return 0
    modifs = {k: v for k, v in neuves.items() if archive.get(k) != v}
    if not modifs:
        print(f"Guily Glaz : aucune nouvelle mesure ({len(archive)} archivées)")
        return 0
    archive.update(modifs)
    ecrire(dossier, archive, {k[:7] for k in modifs})
    print(f"::notice::Guily Glaz : {len(modifs)} mesure(s) ajoutée(s) ou corrigée(s), {len(archive)} au total, "
          f"du {min(archive)} au {max(archive)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
