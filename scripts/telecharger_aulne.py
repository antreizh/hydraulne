"""Téléchargement de l'historique hydrométrique du bassin de l'Aulne (2000-2025).

Lancé une seule fois par le workflow « Historique Aulne » de la branche analyse-aulne.
Sorties (dossier donnees/) :
  stations.csv                         référentiel Hub'Eau des stations Aulne / Ellez / Hyères
  elab/<code>_<grandeur>.csv.gz        Hub'Eau obs_elab : QmnJ, HIXnJ, QIXnJ (journalier)
  horaire/<code>_<H|Q>.csv.gz          HydroPortail : moyennes horaires (si l'accès fonctionne)
  sondes/                              réponses brutes des essais HydroPortail (diagnostic)
  rapport.json                         ce qui a marché, nombre de valeurs, erreurs
"""
import gzip
import io
import json
import re
import sys
import time
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

SORTIE = Path(sys.argv[1] if len(sys.argv) > 1 else "donnees")
DEBUT, FIN = date(2000, 1, 1), date(2025, 12, 31)
HUBEAU = "https://hubeau.eaufrance.fr/api/v2/hydrometrie"
UA = {"User-Agent": "antreizh-hydraulne/1.0 (+https://appli.antreizh.fr)"}
UA_NAV = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0 Safari/537.36",
          "Accept": "application/json, text/javascript, */*; q=0.01",
          "X-Requested-With": "XMLHttpRequest"}
MOTIF = re.compile(r"aulne|ellez|elez|hy[eè]res", re.I)
rapport = {"debut": datetime.now(timezone.utc).isoformat(), "stations": {}, "erreurs": []}


def lire(url, entetes=UA, essais=3, delai=60):
    for i in range(essais):
        try:
            req = urllib.request.Request(url, headers=entetes)
            with urllib.request.urlopen(req, timeout=delai) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                return e.code, e.read()
            time.sleep(3 * (i + 1))
        except Exception as e:  # réseau
            if i == essais - 1:
                return None, str(e).encode()
            time.sleep(3 * (i + 1))
    return None, b""


def ecrire_gz(df, chemin):
    chemin.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(chemin, "wt", encoding="utf-8") as f:
        df.to_csv(f, sep=";", index=False)


# ---------------------------------------------------------------- 1. référentiel
def referentiel():
    lignes = []
    for dep in ("29", "22"):
        url = (f"{HUBEAU}/referentiel/stations?code_departement={dep}&format=json&size=2000")
        st, corps = lire(url)
        if st != 200 and st != 206:
            rapport["erreurs"].append(f"référentiel {dep} : HTTP {st}")
            continue
        for s in json.loads(corps).get("data", []):
            texte = f"{s.get('libelle_station','')} {s.get('libelle_cours_eau','')}"
            if MOTIF.search(texte):
                lignes.append({k: s.get(k) for k in (
                    "code_station", "code_site", "libelle_station", "libelle_cours_eau",
                    "en_service", "date_ouverture_station", "date_fermeture_station",
                    "longitude_station", "latitude_station", "altitude_ref_alti_station",
                    "type_station", "code_commune_station", "libelle_commune")})
    df = pd.DataFrame(lignes).drop_duplicates("code_station").sort_values("code_station")
    SORTIE.mkdir(parents=True, exist_ok=True)
    df.to_csv(SORTIE / "stations.csv", sep=";", index=False)
    # sites (surface du bassin versant)
    sites = []
    for code in sorted(set(df["code_site"].dropna())):
        st, corps = lire(f"{HUBEAU}/referentiel/sites?code_site={code}&format=json")
        if st in (200, 206):
            for s in json.loads(corps).get("data", []):
                sites.append({k: s.get(k) for k in ("code_site", "libelle_site",
                              "surface_bv", "date_premiere_donnee_dispo_site")})
    pd.DataFrame(sites).to_csv(SORTIE / "sites.csv", sep=";", index=False)
    print(f"{len(df)} stations retenues")
    return df


# ---------------------------------------------------------------- 2. obs_elab
def obs_elab(code, grandeur):
    url = (f"{HUBEAU}/obs_elab?code_entite={code}&grandeur_hydro_elab={grandeur}"
           f"&date_debut_obs_elab={DEBUT}&date_fin_obs_elab={FIN}&size=20000"
           "&fields=date_obs_elab,resultat_obs_elab,libelle_qualification")
    lignes = []
    while url:
        st, corps = lire(url)
        if st not in (200, 206):
            if st != 404:
                rapport["erreurs"].append(f"obs_elab {code} {grandeur} : HTTP {st}")
            break
        js = json.loads(corps)
        lignes += js.get("data", [])
        url = js.get("next")
    if lignes:
        ecrire_gz(pd.DataFrame(lignes), SORTIE / "elab" / f"{code}_{grandeur}.csv.gz")
    return len(lignes)


# ---------------------------------------------------------------- 3. HydroPortail horaire
HP = "https://www.hydro.eaufrance.fr/stationhydro/ajax/{code}/series"
VARIANTES = [  # plusieurs formes d'URL essayées ; la première qui rend des valeurs est gardée
    {"hydro_series[variableType]": "simple_and_interpolated_and_hourly_variable",
     "hydro_series[simpleAndInterpolatedAndHourlyVariable]": "{g}",
     "hydro_series[statusData]": "most_valid"},
    {"hydro_series[variableType]": "simple_and_interpolated_and_hourly_variable",
     "hydro_series[simpleAndInterpolatedAndHourlyVariable]": "{g}",
     "hydro_series[statusData]": "raw"},
    {"hydro_series[variableType]": "simple_variable",
     "hydro_series[simpleVariable]": "{g}",
     "hydro_series[statusData]": "most_valid"},
]


def points(js):
    """Cherche dans la réponse une liste de points {t, v}."""
    if isinstance(js, list) and js and isinstance(js[0], dict) and "t" in js[0] and "v" in js[0]:
        return js
    if isinstance(js, dict):
        for v in js.values():
            p = points(v)
            if p:
                return p
    if isinstance(js, list):
        for v in js:
            p = points(v)
            if p:
                return p
    return None


def url_hp(code, g, d1, d2, variante):
    params = {"hydro_series[startAt]": d1.strftime("%d/%m/%Y"),
              "hydro_series[endAt]": d2.strftime("%d/%m/%Y")}
    params.update({k: v.format(g=g) for k, v in variante.items()})
    return HP.format(code=code) + "?" + urllib.parse.urlencode(params)


def sonder(code):
    """Trouve la variante et l'entête qui marchent, et la durée maximale d'une requête."""
    sondes = SORTIE / "sondes"
    sondes.mkdir(parents=True, exist_ok=True)
    for i, var in enumerate(VARIANTES):
        for nom_e, ent in (("nav", UA_NAV), ("ua", UA)):
            st, corps = lire(url_hp(code, "H", date(2014, 2, 1), date(2014, 2, 28), var), ent)
            (sondes / f"{code}_v{i}_{nom_e}.txt").write_bytes((corps or b"")[:4000])
            try:
                p = points(json.loads(corps))
            except Exception:
                p = None
            if p:
                # durée max : essai sur 1 an, sinon 3 mois, sinon 1 mois
                for jours in (366, 92, 31):
                    st2, c2 = lire(url_hp(code, "H", date(2014, 1, 1),
                                          date(2014, 1, 1) + timedelta(days=jours - 1), var), ent)
                    try:
                        p2 = points(json.loads(c2))
                    except Exception:
                        p2 = None
                    if p2 and len(p2) > 20 * (jours / 31):
                        return var, ent, jours
                return var, ent, 31
    return None


def horaire(code, g, var, ent, jours):
    morceaux, d = [], DEBUT
    while d <= FIN:
        d2 = min(d + timedelta(days=jours - 1), FIN)
        st, corps = lire(url_hp(code, g, d, d2, var), ent, delai=120)
        try:
            p = points(json.loads(corps)) or []
        except Exception:
            p = []
            if st not in (200, 404):
                rapport["erreurs"].append(f"HP {code} {g} {d} : HTTP {st}")
        if p:
            df = pd.DataFrame(p)[["t", "v"]]
            df["t"] = pd.to_datetime(df["t"], utc=True, errors="coerce")
            df = df.dropna().set_index("t")["v"].astype(float)
            morceaux.append(df.resample("1h").mean().dropna())
        d = d2 + timedelta(days=1)
        time.sleep(0.4)
    if not morceaux:
        return 0
    s = pd.concat(morceaux)
    s = s[~s.index.duplicated()].sort_index()
    ecrire_gz(s.rename("v").reset_index(), SORTIE / "horaire" / f"{code}_{g}.csv.gz")
    return len(s)


def main():
    stations = referentiel()
    codes = list(stations["code_station"])
    t0 = time.time()
    for code in codes:
        r = rapport["stations"].setdefault(code, {})
        for g in ("QmnJ", "HIXnJ", "QIXnJ"):
            r[g] = obs_elab(code, g)
        print(code, r, flush=True)
    sites = sorted(set(stations["code_site"].dropna()))
    for code in sites:  # débits journaliers souvent rattachés au site
        r = rapport["stations"].setdefault(code, {})
        r["QmnJ"] = obs_elab(code, "QmnJ")
        r["QIXnJ"] = obs_elab(code, "QIXnJ")

    # HydroPortail : on sonde sur Châteaulin, puis on applique à toutes les stations
    trouve = None
    for code_test in ("J382182001", "J381181001", codes[0] if codes else "J382182001"):
        trouve = sonder(code_test)
        if trouve:
            break
    rapport["hydroportail"] = (
        {"variante": trouve[0], "entete": "navigateur" if trouve[1] is UA_NAV else "ua",
         "jours_par_requete": trouve[2]} if trouve else "inaccessible")
    print("HydroPortail :", rapport["hydroportail"], flush=True)
    if trouve:
        var, ent, jours = trouve
        for code in codes:
            if time.time() - t0 > 80 * 60:
                rapport["erreurs"].append("arrêt : limite de temps atteinte")
                break
            r = rapport["stations"][code]
            for g in ("H", "Q"):
                r[f"horaire_{g}"] = horaire(code, g, var, ent, jours)
            print(code, r, flush=True)

    rapport["fin"] = datetime.now(timezone.utc).isoformat()
    (SORTIE / "rapport.json").write_text(json.dumps(rapport, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
