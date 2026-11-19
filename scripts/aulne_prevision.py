"""
aulne_prevision.py  (version 2, calée sur l'historique 2010-2025)
=================================================================
Prévision de la hauteur d'eau de l'Aulne canalisée à Châteaulin par propagation
des débits mesurés aux stations amont.

    Poullaouen ──► Kergloff (Pont Triffen) ──► Châteauneuf-du-Faou ──► Gouézec ──► Châteaulin
      118 km²           518 km²                    1 225 km²              1 391 km²    1 483 km²
                                  (+ Hyères, non jaugée)

Formule (un tronçon amont U -> aval D, horizon h en heures) :

    Q_D(t+h) = Q_D(t) + r · [ Q_U(t+h-L) - Q_U(t-L) ]

    L : temps de propagation (h), plus long en forte crue (débordements) ;
    r : coefficient de transfert des variations de débit ;
    si h > L, Q_U(t+h-L) est la prévision de la station amont (cascade).

Hauteur prévue, « ancrée » sur la dernière hauteur mesurée (supprime le biais
de la courbe de tarage, qui est révisée d'une année sur l'autre) :

    H_D(t+h) = H_D(t) + C(Q_D(t+h)) - C(Q_D(t))      C : courbe Q -> H

Calage (sept. 2026) sur les séries horaires HydroPortail : calage 2010-2019,
validation sept. 2019-2025 sur 12 crues > 1,2 m à Châteaulin :
erreur absolue moyenne sur la hauteur du pic 0,01 m à 4 h, 0,02 m à 8 h,
0,04 m à 12 h, 0,06 m à 16 h (persistance : 0,03 / 0,09 / 0,15 / 0,21 m).
Fortes crues (pics de 1,9 à 2,4 m : 2013, 2014, 2023, 2024) : le modèle sous-estime
le pic de 0,1 à 0,2 m à 16 h (persistance : 0,4 à 0,9 m) -> prévoir une marge.

Ce qui a été écarté après analyse
- L'Ellez à Loqueffret : station à l'aval de la retenue Saint-Michel (35 km²),
  débits régulés, aucun pouvoir prédictif -> retirée de la chaîne.
- Correction de marée : en crue, aucun effet mesurable de la marée sur la hauteur
  à Châteaulin (< 2 cm). En basses eaux seulement, environ +0,10 m quand Guily
  Glaz dépasse 4 m -> sans intérêt pour la prévision de crue.

Horizon utile : environ 16 h depuis Poullaouen (au-delà, il faut la pluie prévue).
Domaine : moyennes et hautes eaux (Q Châteaulin > 50 m³/s). En basses eaux, les
hauteurs dépendent des manœuvres de barrages.

Dépendances : numpy, pandas (requests pour Hub'Eau).
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import product

import numpy as np
import pandas as pd

# ---------------------------------------------------------------------------
# 1. Réseau de mesure
# ---------------------------------------------------------------------------

STATIONS = {
    "poullaouen":  {"code": "J360181001", "bv_km2": 117.6, "libelle": "L'Aulne à Poullaouen - Le Goask"},
    "kergloff":    {"code": "J363181003", "bv_km2": 518.0, "libelle": "L'Aulne à Cléden-Poher - Pont Triffen"},
    "chateauneuf": {"code": "J381181001", "bv_km2": 1224.8, "libelle": "L'Aulne à Châteauneuf-du-Faou - Pont Pol Ty Glaz"},
    "gouezec":     {"code": "J382181001", "bv_km2": 1390.9, "libelle": "L'Aulne à Gouézec - Pont Coblant"},
    "chateaulin":  {"code": "J382182001", "bv_km2": 1483.4, "libelle": "L'Aulne à Châteaulin - Pont routier"},
}


@dataclass
class Troncon:
    aval: str
    amont: str
    lag_h: int              # temps de propagation en crue ordinaire
    r: float                # coefficient de transfert des variations
    lag_haut_h: int         # temps de propagation en forte crue
    seuil_q_amont: float    # débit amont (m³/s) au-delà duquel lag_haut_h s'applique


# Calage 2010-2019 (critère : Nash des variations de débit, Q aval > quantile 90 %)
TRONCONS = [
    Troncon("kergloff",    "poullaouen",  5, 1.80, 6, 13.9),
    Troncon("chateauneuf", "kergloff",    5, 1.12, 7, 56.3),
    Troncon("gouezec",     "chateauneuf", 2, 1.02, 3, 120.0),
    Troncon("chateaulin",  "gouezec",     4, 0.88, 4, 141.0),
]

# Temps de propagation des pics (médianes de 101 crues, Q Gouézec > 110 m³/s, 2000-2025) :
#   Poullaouen -> Châteaulin 14 h (21 h si Q Gouézec > 180), Kergloff -> Châteaulin 8 h (13 h),
#   Châteauneuf -> Châteaulin 5 h (6,5 h), Gouézec -> Châteaulin 3 h (4 h).
# Rapports des débits de pointe : Châteauneuf/Kergloff 1,87 (Hyères), Gouézec/Châteauneuf 1,16,
#   Châteaulin/Gouézec 1,05.


# ---------------------------------------------------------------------------
# 2. Courbes Q -> H
# ---------------------------------------------------------------------------

class CourbeHQ:
    """Relation monotone H (m) <-> Q (m³/s), interpolée linéairement."""

    def __init__(self, q, h):
        self.q = np.asarray(q, float)
        self.h = np.asarray(h, float)

    @classmethod
    def ajuster(cls, h: pd.Series, q: pd.Series, bornes_q=None) -> "CourbeHQ":
        """Médiane de H par classe de débit (couples mesurés simultanément)."""
        bornes_q = bornes_q or [0, 3, 6, 10, 15, 20, 30, 40, 50, 60, 80, 100, 125, 150,
                                175, 200, 250, 300, 350, 400, 500, 650]
        d = pd.concat({"h": h, "q": q}, axis=1, sort=True).dropna()
        d["c"] = pd.cut(d["q"], bornes_q)
        g = d.groupby("c", observed=True).agg(q=("q", "median"), h=("h", "median"), n=("h", "size"))
        g = g[g["n"] >= 5]
        hh = np.maximum.accumulate(g["h"].to_numpy())
        hh = hh + np.arange(len(hh)) * 1e-4  # strictement croissante
        return cls(g["q"].to_numpy(), hh)

    @staticmethod
    def _interp(x, xp, fp):
        x = np.asarray(x, float)
        y = np.interp(x, xp, fp)
        p0 = (fp[1] - fp[0]) / (xp[1] - xp[0])
        p1 = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
        y = np.where(x < xp[0], fp[0] + p0 * (x - xp[0]), y)
        return np.where(x > xp[-1], fp[-1] + p1 * (x - xp[-1]), y)

    def h_de_q(self, q):
        return self._interp(q, self.q, self.h)

    def q_de_h(self, h):
        return np.maximum(self._interp(h, self.h, self.q), 0.0)


# Courbes établies sur les couples H/Q horaires 2018-2025 (médianes par classe de débit).
COURBES = {
    "kergloff": CourbeHQ(
        [1.7, 4.0, 7.8, 12.4, 17.4, 24.2, 34.2, 44.2, 54.2, 68.5, 86.6, 109.0, 136.5],
        [0.84, 0.90, 0.96, 1.02, 1.07, 1.14, 1.22, 1.29, 1.36, 1.44, 1.54, 1.65, 1.83]),
    "chateauneuf": CourbeHQ(
        [2.2, 4.1, 7.7, 12.5, 17.0, 24.8, 34.8, 44.7, 54.9, 69.0, 88.6, 110.5, 137.9, 160.5, 186.0, 219.0, 280.5],
        [0.37, 0.42, 0.50, 0.58, 0.65, 0.76, 0.87, 0.98, 1.08, 1.21, 1.41, 1.59, 1.83, 2.04, 2.22, 2.43, 2.76]),
    "gouezec": CourbeHQ(
        [2.3, 4.0, 7.8, 12.3, 17.4, 24.7, 35.0, 44.9, 54.7, 69.7, 89.6, 110.0, 137.0, 162.0, 188.0, 217.4, 269.2, 327.9],
        [1.39, 1.43, 1.51, 1.58, 1.64, 1.72, 1.82, 1.92, 2.00, 2.10, 2.22, 2.31, 2.40, 2.61, 2.81, 3.03, 3.40, 3.74]),
    "chateaulin": CourbeHQ(
        [2.4, 4.0, 7.7, 12.3, 17.4, 24.3, 34.9, 44.9, 54.5, 69.9, 89.0, 110.5, 135.0, 161.2, 189.0, 214.4, 275.5, 322.5, 367.0],
        [0.03, 0.07, 0.14, 0.21, 0.27, 0.34, 0.44, 0.51, 0.58, 0.65, 0.76, 0.81, 0.92, 1.07, 1.20, 1.33, 1.57, 1.76, 1.96]),
}


# ---------------------------------------------------------------------------
# 3. Prévision
# ---------------------------------------------------------------------------

def _horaire(s: pd.Series) -> pd.Series:
    """Série horaire ; HydroPortail/Hub'Eau n'enregistrent un point que si la valeur change :
    on interpole dans le temps (trous de 48 h au plus)."""
    s = s.dropna().sort_index()
    s = s[~s.index.duplicated()]
    return s.resample("1h").mean().interpolate("time", limit=48, limit_area="inside")


def prevoir_chateaulin(debits: dict[str, pd.Series],
                       hauteurs: dict[str, pd.Series],
                       t0: pd.Timestamp | None = None,
                       horizon_max_h: int = 16,
                       troncons: list[Troncon] = TRONCONS,
                       courbes: dict[str, CourbeHQ] = COURBES) -> pd.DataFrame:
    """Hauteurs (m) et débits (m³/s) prévus aux stations aval, jusqu'à Châteaulin.

    debits   : {station: série Q (m³/s)} des 5 stations (Hub'Eau grandeur Q)
    hauteurs : {station: série H (m)} au moins des stations aval à prévoir
               (sert à ancrer la prévision sur la dernière hauteur mesurée)
    t0       : instant de prévision (défaut : dernière heure commune à toutes les séries)
    Retour   : DataFrame indexé de t0+1 h à t0+horizon_max_h, colonnes <station> (H) et Q_<station>.
    """
    q = {k: _horaire(s) for k, s in debits.items()}
    if t0 is None:
        t0 = min(s.dropna().index[-1] for s in q.values())
    t0 = pd.Timestamp(t0).floor("1h")
    q = {k: s.loc[:t0] for k, s in q.items()}
    horizons = range(1, horizon_max_h + 1)
    temps = [t0 + pd.Timedelta(hours=h) for h in horizons]

    def valeur(st, t):          # observé si t <= t0, sinon prévu (ou dernier observé)
        s = q[st]
        return s.iloc[-1] if t > s.index[-1] else s.asof(t)

    sortie = pd.DataFrame(index=pd.DatetimeIndex(temps, name="date"))
    for tr in troncons:
        qa, qu = q[tr.aval], q[tr.amont]
        L = tr.lag_haut_h if qu.iloc[-1] > tr.seuil_q_amont else tr.lag_h
        ref = qu.asof(t0 - pd.Timedelta(hours=L))
        q0 = qa.iloc[-1]
        prev = [max(q0 + tr.r * (valeur(tr.amont, t - pd.Timedelta(hours=L)) - ref), 0.0) for t in temps]
        sortie[f"Q_{tr.aval}"] = prev
        q[tr.aval] = pd.concat([qa, pd.Series(prev, index=temps)])   # cascade

        c = courbes[tr.aval]
        if tr.aval in hauteurs and len(hauteurs[tr.aval].dropna()):
            h0 = _horaire(hauteurs[tr.aval]).loc[:t0].iloc[-1]
            sortie[tr.aval] = h0 + c.h_de_q(np.array(prev)) - c.h_de_q(q0)
        else:
            sortie[tr.aval] = c.h_de_q(np.array(prev))
    return sortie.round(3)


# ---------------------------------------------------------------------------
# 4. Calage d'un tronçon
# ---------------------------------------------------------------------------

def caler_troncon(q_aval: pd.Series, q_amont: pd.Series, lags=range(1, 16),
                  quantile_crue: float = 0.90) -> dict:
    """(L, r) maximisant le Nash des variations Q_aval(t+L)-Q_aval(t) expliquées par
    r·[Q_amont(t)-Q_amont(t-L)], sur les heures où Q_aval dépasse son quantile 90 %.
    Le critère de Nash (et non l'écart quadratique) évite de favoriser les L courts."""
    qa, qu = _horaire(q_aval), _horaire(q_amont)
    crue = qa > qa.quantile(quantile_crue)
    meilleur = None
    for L in lags:
        y = qa.shift(-L) - qa
        x = qu - qu.shift(L)
        d = pd.concat({"y": y, "x": x}, axis=1)[crue].dropna()
        if len(d) < 48:
            continue
        r = float((d.x * d.y).sum() / (d.x ** 2).sum())
        nash = float(1 - (d.y - r * d.x).var() / d.y.var())
        if meilleur is None or nash > meilleur["nash"]:
            meilleur = {"lag_h": L, "r": r, "nash": nash, "n": len(d)}
    return meilleur


# ---------------------------------------------------------------------------
# 5. Temps réel (Hub'Eau, environ 1 mois d'historique)
# ---------------------------------------------------------------------------

HUBEAU = "https://hubeau.eaufrance.fr/api/v2/hydrometrie/observations_tr"


def charger_hubeau(code_station: str, grandeur: str = "H", jours: int = 5) -> pd.Series:
    """grandeur 'H' -> m ; 'Q' -> m³/s."""
    import requests
    debut = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=jours)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url, params, lignes = HUBEAU, {"code_entite": code_station, "grandeur_hydro": grandeur,
                                   "date_debut_obs": debut, "size": 20000,
                                   "fields": "date_obs,resultat_obs"}, []
    while url:
        rep = requests.get(url, params=params, timeout=30)
        rep.raise_for_status()
        js = rep.json()
        lignes += js.get("data", [])
        url, params = js.get("next"), None
    if not lignes:
        return pd.Series(dtype=float)
    df = pd.DataFrame(lignes)
    return pd.Series(df["resultat_obs"].astype(float).to_numpy() / 1000.0,
                     index=pd.to_datetime(df["date_obs"], utc=True)).sort_index()


def prevision_temps_reel(horizon_max_h: int = 16) -> pd.DataFrame:
    debits = {k: charger_hubeau(s["code"], "Q") for k, s in STATIONS.items()}
    hauteurs = {k: charger_hubeau(s["code"], "H") for k, s in STATIONS.items() if k != "poullaouen"}
    return prevoir_chateaulin(debits, hauteurs, horizon_max_h=horizon_max_h)


if __name__ == "__main__":
    print(prevision_temps_reel().to_string())
