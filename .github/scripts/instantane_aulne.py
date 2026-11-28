#!/usr/bin/env python3
"""Instantané de l'Aulne à Châteaulin (station J382182001) pour le mini-widget « Situation hydrologique ».

Le mini-widget a besoin de 4 séries Hub'Eau, dont 2 très lourdes (valeurs journalières depuis 2010) qui
répondent lentement, surtout le matin. Ce script les récupère toutes les 3 heures (workflow
« Instantané Aulne ») et les publie sous une forme compacte dans data/aulne/chateaulin.json :
le widget l'affiche aussitôt, puis le complète par les dernières mesures lues en direct.

Format (heures en UTC) :
  maj    : heure du passage (ISO)
  q, h   : débit (l/s) et hauteur (mm) toutes les 5 min sur 8,5 jours
           { "t0": premier instant en ms epoch, "p": [[minutes depuis t0, valeur], ...] } (ordre chronologique)
  qmnj   : débit moyen journalier (l/s)      { "d0": "AAAA-MM-JJ", "v": [valeur ou null, un par jour] }
  hixnj  : hauteur maximale journalière (mm) { "d0": "AAAA-MM-JJ", "v": [...] }
Une série que Hub'Eau ne rend pas est reprise du fichier précédent (l'instantané ne se dégrade jamais).

Depuis le 28/11/2026, même principe pour la nappe de Saint-Ségal (piézomètre de Kergadalen 03103X0047/PZ),
rubrique « Nappe » du mini-widget, dans data/nappe/saint-segal.json :
  maj    : heure du passage (ISO)
  niveau : niveau journalier NGF (m)          { "d0": "AAAA-MM-JJ", "v": [valeur ou null, un par jour], "prof": profondeur du dernier jour }
  tr     : dernière mesure télétransmise     { "date_mesure", "niveau_eau_ngf", "profondeur_nappe" }
"""
import datetime as dt
import json
import os
import sys
import time
import urllib.parse
import urllib.request

STATION = "J382182001"
BASE = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/"
RACINE = sys.argv[1] if len(sys.argv) > 1 else "."
SORTIE = os.path.join(RACINE, "data", "aulne", "chateaulin.json")
BSS = "03103X0047/PZ"
SORTIE_NAPPE = os.path.join(RACINE, "data", "nappe", "saint-segal.json")


def lire(url, essais=3):
    for i in range(essais):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"Accept": "application/json"}), timeout=90) as r:
                return json.load(r).get("data") or []
        except Exception as e:  # Hub'Eau répond parfois par une page vide ou trop lentement
            print(f"  essai {i + 1} : {e}")
            time.sleep(10 * (i + 1))
    return None


def obs(grandeur):
    depuis = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=8.5)).strftime("%Y-%m-%dT%H:%M:%SZ")
    d = lire(f"{BASE}observations_tr?code_entite={STATION}&grandeur_hydro={grandeur}&date_debut_obs={depuis}"
             "&size=5000&sort=asc&format=json&fields=date_obs,resultat_obs")
    if not d:
        return None
    pts = sorted((int(dt.datetime.fromisoformat(r["date_obs"].replace("Z", "+00:00")).timestamp() * 1000), r["resultat_obs"])
                 for r in d if r.get("resultat_obs") is not None and r.get("date_obs"))
    if not pts:
        return None
    t0 = pts[0][0]
    return {"t0": t0, "p": [[round((t - t0) / 60000), v] for t, v in pts]}


def elab(grandeur):
    d = lire(f"{BASE}obs_elab?code_entite={STATION}&grandeur_hydro_elab={grandeur}"
             "&fields=date_obs_elab,resultat_obs_elab&size=20000&format=json")
    if not d:
        return None
    vals = {r["date_obs_elab"][:10]: r["resultat_obs_elab"] for r in d if r.get("resultat_obs_elab") is not None}
    if not vals:
        return None
    jours = sorted(vals)
    d0 = dt.date.fromisoformat(jours[0])
    n = (dt.date.fromisoformat(jours[-1]) - d0).days + 1
    v = [vals.get((d0 + dt.timedelta(days=i)).isoformat()) for i in range(n)]
    return {"d0": d0.isoformat(), "v": [round(x) if isinstance(x, float) and x.is_integer() else x for x in v]}


def nappe():
    """Instantané de la nappe de Saint-Ségal (mêmes requêtes que le mini-widget)."""
    ancien = {}
    if os.path.exists(SORTIE_NAPPE):
        with open(SORTIE_NAPPE, encoding="utf-8") as f:
            ancien = json.load(f)
    bss = urllib.parse.quote(BSS, safe="")
    neuf = {"maj": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "bss": BSS}
    print("nappe …")
    d = lire(f"https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/chroniques?code_bss={bss}"
             "&fields=date_mesure,niveau_nappe_eau,profondeur_nappe&size=20000&sort=asc")
    niveau = None
    if d:
        vals = {r["date_mesure"][:10]: r for r in d if r.get("niveau_nappe_eau") is not None and r.get("date_mesure")}
        if vals:
            jours = sorted(vals)
            d0 = dt.date.fromisoformat(jours[0])
            n = (dt.date.fromisoformat(jours[-1]) - d0).days + 1
            v = []
            for i in range(n):
                r = vals.get((d0 + dt.timedelta(days=i)).isoformat())
                v.append(r["niveau_nappe_eau"] if r else None)
            niveau = {"d0": d0.isoformat(), "v": v, "prof": vals[jours[-1]].get("profondeur_nappe")}
    if niveau is None:
        print("::warning::Nappe : chronique indisponible sur Hub'Eau, reprise de l'instantané précédent")
        niveau = ancien.get("niveau")
    neuf["niveau"] = niveau
    print("nappe (temps réel) …")
    t = lire(f"https://hubeau.eaufrance.fr/api/v1/niveaux_nappes/chroniques_tr?code_bss={bss}&size=1&sort=desc")
    neuf["tr"] = ({k: t[0].get(k) for k in ("date_mesure", "niveau_eau_ngf", "profondeur_nappe")} if t else ancien.get("tr"))
    if not niveau:
        print("::warning::Nappe : aucune donnée, instantané inchangé")
        return
    if ancien and {k: v for k, v in neuf.items() if k != "maj"} == {k: v for k, v in ancien.items() if k != "maj"}:
        print("Nappe : rien de nouveau")
        return
    os.makedirs(os.path.dirname(SORTIE_NAPPE), exist_ok=True)
    with open(SORTIE_NAPPE, "w", encoding="utf-8") as f:
        json.dump(neuf, f, separators=(",", ":"))
    dern = dt.date.fromisoformat(niveau["d0"]) + dt.timedelta(days=len(niveau["v"]) - 1)
    print(f"Nappe : niveau journalier jusqu'au {dern} · temps réel {(neuf.get('tr') or {}).get('date_mesure')} · "
          f"{SORTIE_NAPPE} écrit ({os.path.getsize(SORTIE_NAPPE) // 1024} Ko)")


def main():
    ancien = {}
    if os.path.exists(SORTIE):
        with open(SORTIE, encoding="utf-8") as f:
            ancien = json.load(f)
    neuf = {"maj": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"), "station": STATION}
    for cle, fn, arg in (("q", obs, "Q"), ("h", obs, "H"), ("qmnj", elab, "QmnJ"), ("hixnj", elab, "HIXnJ")):
        print(f"{cle} …")
        s = fn(arg)
        if s is None:
            print(f"::warning::Aulne : série {cle} indisponible sur Hub'Eau, reprise de l'instantané précédent")
            s = ancien.get(cle)
        neuf[cle] = s
    if not (neuf.get("q") or neuf.get("h")):
        print("::warning::Aulne : aucune mesure récente, instantané inchangé")
        return
    comp = {k: v for k, v in neuf.items() if k != "maj"}
    if ancien and comp == {k: v for k, v in ancien.items() if k != "maj"}:
        print("Aulne : rien de nouveau")
        return
    os.makedirs(os.path.dirname(SORTIE), exist_ok=True)
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(neuf, f, separators=(",", ":"))
    for cle in ("q", "h"):
        s = neuf.get(cle)
        if s and s["p"]:
            dern = dt.datetime.fromtimestamp((s["t0"] + s["p"][-1][0] * 60000) / 1000, dt.timezone.utc)
            print(f"Aulne : {cle} dernière mesure {dern:%Y-%m-%d %H:%M} UTC ({len(s['p'])} valeurs)")
    print(f"Aulne : {SORTIE} écrit ({os.path.getsize(SORTIE) // 1024} Ko)")


if __name__ == "__main__":
    main()
    nappe()
