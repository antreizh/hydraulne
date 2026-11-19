import pickle, pandas as pd, numpy as np
H,Q=pickle.load(open('/tmp/hq.pkl','rb'))
ref=Q['gouezec']
def evenements(q,seuil,sep=120):
    x=q[q>seuil]; pics=[]
    for t,v in x.items():
        if pics and (t-pics[-1][0]).total_seconds()/3600<sep:
            if v>pics[-1][1]: pics[-1]=(t,v)
        else: pics.append((t,v))
    return pics
def pic(s,t,a,b):
    if s is None: return None,None
    f=s.loc[t-pd.Timedelta(hours=a):t+pd.Timedelta(hours=b)].dropna()
    if len(f)<0.8*(a+b): return None,None
    tm=f.idxmax()
    if tm in (f.index[0],f.index[-1]): return None,None
    return tm,f.max()
ev=evenements(ref,110)
rows=[]
for t,v in ev:
    r={'t_gouezec':t,'Q_gouezec':v,'H_gouezec':H['gouezec'].asof(t)}
    for k,(a,b) in {'poullaouen':(72,6),'loqueffret':(72,6),'kergloff':(48,6),'penity':(48,6),'chateauneuf':(24,6),'chateaulin':(6,24)}.items():
        tm,qm=pic(Q.get(k),t,a,b)
        r['lag_'+k]=None if tm is None else (t-tm).total_seconds()/3600
        r['Q_'+k]=qm
        if k in H and tm is not None:
            th,hm=pic(H[k],t,a,b); r['H_'+k]=hm
    rows.append(r)
E=pd.DataFrame(rows); E.to_pickle('/tmp/ev.pkl')
print(len(E),'événements Q Gouézec >110 m3/s')
pd.set_option('display.width',250)
print(E[['t_gouezec','Q_gouezec','lag_poullaouen','lag_loqueffret','lag_penity','lag_kergloff','lag_chateauneuf','lag_chateaulin','Q_chateauneuf','Q_chateaulin','H_chateaulin']].round(1).to_string())
