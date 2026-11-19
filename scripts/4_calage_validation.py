import pickle, sys, pandas as pd, numpy as np
sys.path.insert(0,'scripts'); from aulne_prevision import CourbeHQ, caler_troncon
H,Q=pickle.load(open('/tmp/hq.pkl','rb'))
idx=pd.date_range('2010-06-01','2025-12-31 23:00',freq='1h',tz='UTC')
Q={k:v.reindex(idx) for k,v in Q.items()}; H={k:v.reindex(idx) for k,v in H.items()}
CAL=slice('2010-06-01','2019-08-31'); VAL=slice('2019-09-01','2025-12-31')

def cascade(troncons, Hmax=24):
    """F[st][h] : Série (index origine t0) du débit prévu à t0+h. troncons : [(aval, [(amont, L, r, Lhaut, seuil)])]"""
    F={}
    def G(st,k):  # valeur connue à t0+k : obs si k<=0, sinon prévision
        if k<=0: return Q[st].shift(-k)
        return F[st][k] if st in F else Q[st]   # amont extrême : persistance
    for aval,amonts in troncons:
        F[aval]={}
        for h in range(1,Hmax+1):
            x=Q[aval].copy()
            for (u,L,r,Lh,seuil) in amonts:
                inc_bas=G(u,h-L)-Q[u].shift(L)
                if Lh is None: x=x+r*inc_bas
                else:
                    inc_haut=G(u,h-Lh)-Q[u].shift(Lh)
                    x=x+r*np.where(Q[u]>seuil,inc_haut,inc_bas)
            F[aval][h]=x.clip(lower=0)
    return F

courbe=CourbeHQ.ajuster(H['chateaulin']['2023':'2025'],Q['chateaulin']['2023':'2025'],60)
def scores(F, periode, label, horizons=(3,6,9,12,15,18)):
    hobs=H['chateaulin']; qref=Q['gouezec']
    out={}
    for h in horizons:
        prev=pd.Series(courbe.h_de_q(F['chateaulin'][h].to_numpy()),index=idx)
        obs=hobs.shift(-h)
        # origines en crue : Q Gouézec à t0+h > 120 (on juge la prévision des hautes eaux)
        m=(qref.shift(-h)>120)&prev.notna()&obs.notna()
        m=m&(pd.Series(idx,index=idx).between(pd.Timestamp(periode.start,tz='UTC'),pd.Timestamp(periode.stop,tz='UTC')))
        e=(prev-obs)[m]; p=(hobs-obs)[m]
        out[h]=(round(float(np.sqrt((e**2).mean())),3), round(float(np.sqrt((p**2).mean())),3), int(m.sum()))
    print(label, ' '.join(f'h{h}:{a}/{b}' for h,(a,b,n) in out.items()), ' n=',out[horizons[0]][2])
    return out

def h_prevue(F,h,ancre=True):
    q=F['chateaulin'][h].to_numpy()
    if not ancre: return pd.Series(courbe.h_de_q(q),index=idx)
    return H['chateaulin'] + pd.Series(courbe.h_de_q(q)-courbe.h_de_q(Q['chateaulin'].to_numpy()),index=idx)

def scores2(F, periode, label, horizons=(3,6,9,12,15,18), ancre=True):
    hobs=H['chateaulin']; qref=Q['gouezec']; per=pd.Series(idx,index=idx).between(pd.Timestamp(periode.start,tz='UTC'),pd.Timestamp(periode.stop,tz='UTC'))
    s=[]
    for h in horizons:
        prev=h_prevue(F,h,ancre); obs=hobs.shift(-h)
        m=(qref.shift(-h)>120)&prev.notna()&obs.notna()&per
        s.append(f'h{h}:{np.sqrt(((prev-obs)[m]**2).mean()):.3f}/{np.sqrt(((hobs-obs)[m]**2).mean()):.3f}')
    print(label,' '.join(s))

def pics(F, periode, label, horizons=(4,8,12,16), ancre=True, seuil=1.2):
    hobs=H['chateaulin']
    x=hobs[periode]; x=x[x>seuil]; P=[]
    for t,v in x.items():
        if P and (t-P[-1][0]).total_seconds()<5*86400:
            if v>P[-1][1]: P[-1]=(t,v)
        else: P.append((t,v))
    lignes=[]
    for t,v in P:
        r={'pic':t.strftime('%Y-%m-%d %Hh'),'H_obs':round(v,2)}
        for h in horizons:
            t0=t-pd.Timedelta(hours=h)
            hp=h_prevue(F,h,ancre).get(t0,np.nan)
            r[f'err_{h}h']=round(hp-v,2); r[f'pers_{h}h']=round(hobs.get(t0,np.nan)-v,2)
        lignes.append(r)
    D=pd.DataFrame(lignes); print(label); print(D.to_string(index=False))
    print('  erreur absolue moyenne :',{c:round(D[c].abs().mean(),2) for c in D.columns if c.startswith(('err','pers'))})
    return D
