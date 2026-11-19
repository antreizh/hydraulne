import pickle, sys, pandas as pd, numpy as np
sys.path.insert(0,'scripts'); from aulne_prevision import CourbeHQ
H,Q=pickle.load(open('/tmp/hq.pkl','rb'))
for k in ['chateaulin','gouezec','chateauneuf','kergloff']:
    d=pd.concat({'h':H[k],'q':Q[k]},axis=1).dropna()
    c=CourbeHQ.ajuster(d.h,d.q,60)
    d['res']=d.h-c.h_de_q(d.q.to_numpy())
    d['cl']=pd.cut(d.q,[0,20,50,100,150,200,300,600])
    print(k, d.groupby('cl',observed=True).res.agg(['count','std']).round(3).T.to_string())
    # stabilité : hauteur médiane pour Q 140-160 par année
    x=d[(d.q>130)&(d.q<170)]; print('   H à Q~150 par an :', x.groupby(x.index.year).h.median().round(2).to_dict())
# marée : Châteaulin résidu vs Guily Glaz
k='chateaulin'
d=pd.concat({'h':H[k],'q':Q[k],'gg':H['guilyglaz']},axis=1).dropna()
c=CourbeHQ.ajuster(d.h,d.q,60); d['res']=d.h-c.h_de_q(d.q.to_numpy())
for lo,hi in [(0,30),(30,80),(80,150),(150,600)]:
    x=d[(d.q>=lo)&(d.q<hi)]
    print(f'Q {lo}-{hi}: n={len(x)} corr(res, GG)={x.res.corr(x.gg):.2f}  pente={np.polyfit(x.gg,x.res,1)[0]:.3f} m/m   GG p99={x.gg.quantile(.99):.2f}')
