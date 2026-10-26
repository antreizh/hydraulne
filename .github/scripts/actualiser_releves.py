#!/usr/bin/env python3
"""Actualise les relevés intégrés (« relevés de secours ») des pages de l'appli HYDRAULNE.

Lancé chaque jour par le workflow GitHub « Actualisation des relevés » (.github/workflows/).
Les pages chargent de toute façon les données récentes à l'ouverture ; ces relevés intégrés
servent à l'affichage immédiat et de secours si une source ne répond pas.

Sources (sans clé) :
  - Météo-France, fichier quotidien du Finistère (station 29263002 ST-SEGAL S A) : pluie RR, température TNTXM ;
  - Hub'Eau, piézomètre de Kergadalen 03103X0047/PZ : niveau de la nappe (m NGF).

Seules les constantes de données sont réécrites (SNAP, SNAP_RR, SNAP_TT et, si Météo-France
renomme son fichier en début d'année, MF_URL). Une série n'est jamais raccourcie : en cas de
doute ou de source injoignable, la page reste telle quelle.

Usage : python3 actualiser_releves.py <dossier de l'appli>
"""
import csv, datetime as dt, gzip, io, json, re, sys, time, urllib.request
from zoneinfo import ZoneInfo

STATION = "29263002"
BSS = "03103X0047/PZ"
S3 = "https://meteofrance.s3.sbg.io.cloud.ovh.net"
PREFIXE = "data/synchro_ftp/BASE/QUOT/Q_29_latest"
FICHIER_MF = re.compile(r"data/synchro_ftp/BASE/QUOT/Q_29_latest-(\d{4})-(\d{4})_RR-T-Vent\.csv\.gz")
HUBEAU = "https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/chroniques"
UA = {"User-Agent": "antreizh-hydraulne/1.0 (+https://appli.antreizh.fr)"}

journal = []
def log(msg):
    journal.append(msg); print(msg, flush=True)


def lire_url(url, essais=3):
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60) as r:
                return r.read()
        except Exception as e:
            if i == essais - 1:
                raise
            log(f"  nouvel essai ({e})"); time.sleep(10)


def jour(s):
    return dt.date.fromisoformat(s)


def fmt(v):
    """Comme String(+v) en JavaScript : 0.2, 12, -1.4."""
    v = float(v)
    if v == 0:
        return "0"
    return str(int(v)) if v.is_integer() else repr(round(v, 4))


# ---------------------------------------------------------------- Météo-France
def meteo_france():
    """→ (url du fichier, {date: (RR, TNTXM)}) pour la station, valeurs en texte ('' si absente)."""
    xml = lire_url(f"{S3}/?list-type=2&prefix={PREFIXE}").decode("utf-8", "replace")
    cles = sorted(set(m.group(0) for m in FICHIER_MF.finditer(xml)), key=lambda k: FICHIER_MF.search(k).group(2))
    if not cles:
        raise RuntimeError("fichier quotidien Météo-France introuvable dans la liste")
    url = f"{S3}/{cles[-1]}"
    texte = gzip.decompress(lire_url(url)).decode("utf-8", "replace")
    lignes = csv.reader(io.StringIO(texte), delimiter=";")
    tete = [h.strip() for h in next(lignes)]
    iP, iD, iR, iT = tete.index("NUM_POSTE"), tete.index("AAAAMMJJ"), tete.index("RR"), tete.index("TNTXM")
    vals = {}
    for c in lignes:
        if len(c) > iT and c[iP].strip() == STATION:
            d = c[iD].strip(); d = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
            vals[d] = (c[iR].strip(), c[iT].strip())
    if not vals:
        raise RuntimeError("station 29263002 absente du fichier Météo-France")
    log(f"Météo-France : {url.rsplit('/', 1)[1]}, {len(vals)} jours, dernier {max(vals)}")
    return url, vals


# ---------------------------------------------------------------- Hub'Eau
def hubeau(depuis):
    url = f"{HUBEAU}?code_bss={BSS}&date_debut_mesure={depuis}&fields=date_mesure,niveau_nappe_eau&size=20000"
    data = json.loads(lire_url(url)).get("data") or []
    vals = {d["date_mesure"][:10]: float(d["niveau_nappe_eau"]) for d in data
            if d.get("date_mesure") and d.get("niveau_nappe_eau") is not None}
    if not vals:
        raise RuntimeError("aucune mesure Hub'Eau")
    log(f"Hub'Eau depuis {depuis} : {len(vals)} jours, dernier {max(vals)}")
    return vals


# ---------------------------------------------------------------- outils sur les pages
def constante(t, nom):
    m = re.search(r"^const " + nom + r" = (.*);$", t, re.M)
    return m.group(1) if m else None


def remplacer(t, nom, valeur):
    return re.sub(r"^const " + nom + r" = .*;$", lambda m: f"const {nom} = {valeur};", t, count=1, flags=re.M)


def serie_texte(t, nom):
    """Constante '…' de valeurs quotidiennes séparées par des virgules → {date: texte}."""
    debut = jour(constante(t, "SNAP_START").strip("'\""))
    vals = constante(t, nom).strip("'").split(",")
    return {(debut + dt.timedelta(days=i)).isoformat(): v for i, v in enumerate(vals)}


def texte_serie(debut, d):
    fin = jour(max(k for k, v in d.items() if v != ""))
    n = (fin - debut).days + 1
    return "'" + ",".join(d.get((debut + dt.timedelta(days=i)).isoformat(), "") for i in range(n)) + "'"


def fusion(ancien, nouveau):
    """Les valeurs nouvelles remplacent les anciennes ; une valeur vide n'efface rien."""
    d = dict(ancien)
    for k, v in nouveau.items():
        if v != "":
            d[k] = v
    return d


def controle(ancien, nouveau, nom):
    """Refuse une série qui perdrait des jours renseignés."""
    perdus = [k for k, v in ancien.items() if v not in ("", None) and nouveau.get(k) in ("", None)]
    if perdus:
        raise RuntimeError(f"{nom} : {len(perdus)} jours perdus, page laissée telle quelle")


# ---------------------------------------------------------------- pages
def page_meteo(t, mf_url, mf, col_snap=None):
    """Pages Pluie et température / Pluviométrie / Température (relevés depuis le 1er janvier 2024).
    col_snap : colonne de la constante SNAP des pages à une seule série (0 = pluie, 1 = température)."""
    debut = jour(constante(t, "SNAP_START").strip("'"))
    for nom, col in (("SNAP_RR", 0), ("SNAP_TT", 1), ("SNAP", col_snap)):
        if constante(t, nom) is None:
            continue
        ancien = serie_texte(t, nom)
        neuf = fusion(ancien, {k: fmt(v[col]) if v[col] != "" else "" for k, v in mf.items()})
        controle(ancien, neuf, nom)
        t = remplacer(t, nom, texte_serie(debut, neuf))
    if constante(t, "MF_URL") is not None and constante(t, "MF_URL").strip("'") != mf_url:
        log(f"  nouvelle adresse Météo-France : {mf_url}")
        t = remplacer(t, "MF_URL", f"'{mf_url}'")
    return t


def page_nappe_30(t):
    debut = jour(constante(t, "SNAP_START").strip("'"))
    vals = json.loads(constante(t, "SNAP"))
    ancien = {(debut + dt.timedelta(days=i)).isoformat(): v for i, v in enumerate(vals)}
    dernier = max(k for k, v in ancien.items() if v is not None)
    neuf = dict(ancien); neuf.update(hubeau((jour(dernier) - dt.timedelta(days=30)).isoformat()))
    controle(ancien, neuf, "nappe 30 jours")
    fin = jour(max(neuf))
    liste = [neuf.get((debut + dt.timedelta(days=i)).isoformat()) for i in range((fin - debut).days + 1)]
    return remplacer(t, "SNAP", "[" + ",".join("null" if v is None else fmt(v) for v in liste) + "]")


def page_graphique(t, mf, annee):
    snap = json.loads(constante(t, "SNAP"))
    piezo = {d: v for d, v in snap["piezo"] if d.startswith(annee)}
    pluie = {d: v for d, v in snap["pluie"] if d.startswith(annee)}
    piezo2 = dict(piezo); piezo2.update(hubeau(f"{annee}-01-01"))
    pluie2 = dict(pluie); pluie2.update({k: float(v[0]) for k, v in mf.items() if k.startswith(annee) and v[0] != ""})
    controle(piezo, piezo2, "graphique, nappe"); controle(pluie, pluie2, "graphique, pluie")
    snap["piezo"] = [[d, round(piezo2[d], 2)] for d in sorted(piezo2)]
    snap["pluie"] = [[d, round(pluie2[d], 1)] for d in sorted(pluie2)]
    return remplacer(t, "SNAP", json.dumps(snap, ensure_ascii=False, separators=(",", ":")))


def main(base):
    import os
    annee = str(dt.datetime.now(ZoneInfo("Europe/Paris")).year)
    try:
        mf_url, mf = meteo_france()
    except Exception as e:
        log(f"Météo-France injoignable : {e}"); mf_url, mf = None, None
    pages = {
        "meteo-pluie-temperature.html": lambda t: page_meteo(t, mf_url, mf) if mf else None,
        "meteo-pluviometrie.html": lambda t: page_meteo(t, mf_url, mf, 0) if mf else None,
        "meteo-temperature.html": lambda t: page_meteo(t, mf_url, mf, 1) if mf else None,
        "meteo-nappe-30-jours.html": page_nappe_30,
        "meteo-graphique-nappe-pluie.html": lambda t: page_graphique(t, mf, annee) if mf else None,
    }
    modifiees, erreurs = [], 0
    for nom, maj in pages.items():
        p = os.path.join(base, nom)
        t = open(p, encoding="utf-8").read()
        try:
            n = maj(t)
        except Exception as e:
            log(f"{nom} : non actualisée ({e})"); erreurs += 1; continue
        if n is None:
            log(f"{nom} : non actualisée (source injoignable)"); erreurs += 1
        elif n != t:
            open(p, "w", encoding="utf-8").write(n); modifiees.append(nom); log(f"{nom} : actualisée")
        else:
            log(f"{nom} : déjà à jour")
    print(f"::notice::{len(modifiees)} page(s) actualisée(s), {erreurs} non actualisée(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
