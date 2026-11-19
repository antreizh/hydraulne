import gzip, pandas as pd, numpy as np
CODES = {"poullaouen":"J360181001","loqueffret":"J362401001","penity":"J363181001","kergloff":"J363181003",
         "chateauneuf":"J381181001","gouezec":"J382181001","chateaulin":"J382182001","guilyglaz":"J382183001"}
def h(code,g,lim=48):
    try: df=pd.read_csv(gzip.open(f'donnees/horaire/{code}_{g}.csv.gz','rt'),sep=';')
    except FileNotFoundError: return None
    s=pd.Series(df.v.to_numpy(float)/1000,index=pd.to_datetime(df.t,utc=True)).sort_index()
    s=s[~s.index.duplicated()]
    full=s.resample('1h').mean()
    return full.interpolate(method='time',limit=lim,limit_area='inside')
def charger():
    H={k:h(c,'H') for k,c in CODES.items()}; Q={k:h(c,'Q') for k,c in CODES.items()}
    return {k:v for k,v in H.items() if v is not None},{k:v for k,v in Q.items() if v is not None}
