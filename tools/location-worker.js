// 警衛路線工作台：對方位置回傳（Cloudflare Worker＋D1）
// 設定：D1 資料庫綁定名稱 DB；密鑰 ADMIN_KEY（工作台用來登記分享、讀位置）；變數 ALLOW_ORIGIN（例如 https://comfort527.github.io）
// 定時清除：在 Worker 的 Triggers 加一個 Cron，例如每天一次（0 19 * * *）
//
// API
//   POST   /share   （管理密鑰）{name, exp, grp, mutual} → {id, w}  登記一個要回傳位置的分享連結（grp＋mutual：同一趟的人互相看得到）
//   GET    /locs    （管理密鑰）                        → {shares:[{id,name,exp,devs:[…]}], now}
//   DELETE /share/ID（管理密鑰）                        → {ok}        不再接收這個連結的位置
//   POST   /loc     {id, w, dev, lat, lng, acc, hd, sp} → {ok, exp}   對方回傳位置（連結到期就回 410）
//   POST   /loc/stop {id, w, dev}                       → {ok}        對方按「停止」
//   POST   /peers   {id, w, dev}                        → {peers:[…]} 同一趟、也同意互相看到的其他人（自己要正在回傳才看得到）
//   POST   /blob    （管理密鑰）{data, exp}             → {sid}       短連結：存加密後的分享資料（鑰匙只在連結裡，Worker 解不開）
//   GET    /blob/SID                                    → {data, exp} 分享頁用短連結代碼讀回加密資料（到期就沒了）
//   PUT    /blob/SID（管理密鑰）{data}                 → {ok}        用目前的路線更新已分享的連結（期限不變）
//   DELETE /blob/SID（管理密鑰）                        → {ok}        停止分享：連結立刻失效

const MAX_SHARES=50, MAX_DEVS=5, MIN_GAP=3000, PEER_FRESH=5*60e3, MAX_BLOB=60000, MAX_BLOBS=500;

const SCHEMA=[
  'CREATE TABLE IF NOT EXISTS shares (id TEXT PRIMARY KEY, w TEXT NOT NULL, name TEXT NOT NULL, exp INTEGER NOT NULL, made INTEGER NOT NULL, grp TEXT, mutual INTEGER NOT NULL DEFAULT 0)',
  'CREATE TABLE IF NOT EXISTS blobs (sid TEXT PRIMARY KEY, data TEXT NOT NULL, exp INTEGER NOT NULL, made INTEGER NOT NULL)',
  'CREATE TABLE IF NOT EXISTS locs (id TEXT NOT NULL, dev TEXT NOT NULL, n INTEGER NOT NULL, lat REAL, lng REAL, acc REAL, hd REAL, sp REAL, t INTEGER NOT NULL, stopped INTEGER NOT NULL DEFAULT 0, PRIMARY KEY (id, dev))'
];
let ready=false;
async function init(db){
  if(ready) return;for(const s of SCHEMA) await db.prepare(s).run();
  for(const c of ['grp TEXT','mutual INTEGER NOT NULL DEFAULT 0']) try{await db.prepare('ALTER TABLE shares ADD COLUMN '+c).run();}catch{}   // 舊版資料表補欄位（已經有就略過）
  ready=true;
}
async function sweep(db,now){
  await db.prepare('DELETE FROM locs WHERE id IN (SELECT id FROM shares WHERE exp < ?)').bind(now).run();
  await db.prepare('DELETE FROM shares WHERE exp < ?').bind(now).run();
  await db.prepare('DELETE FROM blobs WHERE exp < ?').bind(now).run();
}
const rnd=n=>{const a=new Uint8Array(n);crypto.getRandomValues(a);return Array.from(a,b=>'abcdefghijkmnpqrstuvwxyz23456789'[b&31]).join('');};
function eq(a,b){   // 比對密鑰：花的時間不因內容不同而改變
  a=String(a||'');b=String(b||'');if(!a||!b) return false;
  let d=a.length^b.length;for(let i=0;i<a.length;i++) d|=a.charCodeAt(i)^b.charCodeAt(i%b.length);
  return d===0;
}
const num=(v,lo,hi)=>{v=Number(v);return Number.isFinite(v)&&v>=lo&&v<=hi?v:null;};

export default {
  async fetch(req,env){
    const origin=req.headers.get('Origin')||'',allow=String(env.ALLOW_ORIGIN||'').split(',').map(s=>s.trim()).filter(Boolean);
    const cors={'Access-Control-Allow-Origin':allow.includes(origin)?origin:(allow[0]||''),'Access-Control-Allow-Methods':'GET,POST,PUT,DELETE,OPTIONS','Access-Control-Allow-Headers':'Content-Type,Authorization','Access-Control-Max-Age':'86400','Vary':'Origin'};
    const json=(o,s=200)=>new Response(JSON.stringify(o),{status:s,headers:{...cors,'Content-Type':'application/json','Cache-Control':'no-store'}});
    if(req.method==='OPTIONS') return new Response(null,{status:204,headers:cors});
    if(!env.DB||!env.ADMIN_KEY) return json({error:'Worker 還沒設定好：要綁定 D1 資料庫（DB）並設定 ADMIN_KEY'},500);
    const url=new URL(req.url),path=url.pathname.replace(/\/+$/,''),now=Date.now(),db=env.DB;
    await init(db);
    const admin=eq((req.headers.get('Authorization')||'').replace(/^Bearer\s+/i,''),env.ADMIN_KEY);
    let body={};
    if(req.method==='POST'||req.method==='PUT'){
      const lim=path.startsWith('/blob')?MAX_BLOB+500:2000;   // 短連結的加密資料比較大
      if((+req.headers.get('Content-Length')||0)>lim) return json({error:'too big'},413);
      const txt=await req.text();if(txt.length>lim) return json({error:'too big'},413);
      try{body=JSON.parse(txt||'{}');}catch{return json({error:'bad json'},400);}
    }
    try{
      if(path==='/share'&&req.method==='POST'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        await sweep(db,now);
        const exp=num(body.exp,now+60e3,now+8*864e5),name=String(body.name||'').trim().slice(0,20)||'對方';
        if(!exp) return json({error:'到期時間不對（最長 8 天）'},400);
        const n=(await db.prepare('SELECT COUNT(*) AS c FROM shares').first())?.c||0;
        if(n>=MAX_SHARES) return json({error:`同時最多 ${MAX_SHARES} 個回傳位置的連結`},429);
        const id=rnd(12),w=rnd(20),mutual=body.mutual?1:0,grp=mutual?String(body.grp||'').slice(0,64)||null:null;
        await db.prepare('INSERT INTO shares (id,w,name,exp,made,grp,mutual) VALUES (?,?,?,?,?,?,?)').bind(id,w,name,exp,now,grp,grp?1:0).run();
        return json({id,w,name,exp,mutual:grp?1:0});
      }
      if(path==='/locs'&&req.method==='GET'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        await sweep(db,now);
        const sh=(await db.prepare('SELECT id,name,exp,made,mutual FROM shares ORDER BY made').all()).results||[];
        const ls=(await db.prepare('SELECT id,dev,n,lat,lng,acc,hd,sp,t,stopped FROM locs ORDER BY id,n').all()).results||[];
        return json({now,shares:sh.map(s=>({...s,devs:ls.filter(l=>l.id===s.id).map(({id,...l})=>l)}))});
      }
      if(path==='/blob'&&req.method==='POST'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        await sweep(db,now);
        const exp=num(body.exp,now+60e3,now+8*864e5),data=String(body.data||'');
        if(!exp) return json({error:'到期時間不對（最長 8 天）'},400);
        if(!data||data.length>MAX_BLOB||!/^[A-Za-z0-9_\-.]+$/.test(data)) return json({error:'資料格式不對或太大'},400);
        const n=(await db.prepare('SELECT COUNT(*) AS c FROM blobs').first())?.c||0;
        if(n>=MAX_BLOBS) return json({error:`同時最多 ${MAX_BLOBS} 個短連結`},429);
        const sid=rnd(10);
        await db.prepare('INSERT INTO blobs (sid,data,exp,made) VALUES (?,?,?,?)').bind(sid,data,exp,now).run();
        return json({sid,exp});
      }
      let b=path.match(/^\/blob\/([a-z0-9]{6,24})$/);
      if(b&&req.method==='GET'){
        const r=await db.prepare('SELECT data,exp FROM blobs WHERE sid=?').bind(b[1]).first();
        if(!r) return json({error:'找不到這個連結'},404);
        if(r.exp<now) return json({error:'連結已經到期'},410);
        return json({data:r.data,exp:r.exp});
      }
      if(b&&req.method==='PUT'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        const data=String(body.data||'');
        if(!data||data.length>MAX_BLOB||!/^[A-Za-z0-9_\-.]+$/.test(data)) return json({error:'資料格式不對或太大'},400);
        const r=await db.prepare('SELECT exp FROM blobs WHERE sid=?').bind(b[1]).first();
        if(!r) return json({error:'找不到這個連結（可能已經停止或到期）'},404);
        if(r.exp<now) return json({error:'連結已經到期'},410);
        await db.prepare('UPDATE blobs SET data=? WHERE sid=?').bind(data,b[1]).run();
        return json({ok:true,exp:r.exp});
      }
      if(b&&req.method==='DELETE'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        await db.prepare('DELETE FROM blobs WHERE sid=?').bind(b[1]).run();
        return json({ok:true});
      }
      let m=path.match(/^\/share\/([a-z0-9]{6,24})$/);
      if(m&&req.method==='DELETE'){
        if(!admin) return json({error:'管理密鑰不對'},401);
        await db.prepare('DELETE FROM locs WHERE id=?').bind(m[1]).run();
        await db.prepare('DELETE FROM shares WHERE id=?').bind(m[1]).run();
        return json({ok:true});
      }
      if(path==='/peers'&&req.method==='POST'){
        const id=String(body.id||''),dev=String(body.dev||'');
        if(!/^[a-z0-9]{6,24}$/.test(id)||!/^[a-z0-9]{6,24}$/.test(dev)) return json({error:'bad id'},400);
        const s=await db.prepare('SELECT w,exp,grp,mutual FROM shares WHERE id=?').bind(id).first();
        if(!s||!eq(body.w,s.w)) return json({error:'這個連結不能回傳位置'},403);
        if(s.exp<now) return json({error:'連結已經到期'},410);
        if(!s.mutual||!s.grp) return json({peers:[]});
        const me=await db.prepare('SELECT t,stopped FROM locs WHERE id=? AND dev=?').bind(id,dev).first();
        if(!me||me.stopped||now-me.t>PEER_FRESH) return json({peers:[],need:'share'});   // 自己也要正在回傳，才看得到別人
        const rows=(await db.prepare('SELECT s.name,l.n,l.lat,l.lng,l.acc,l.t,l.stopped,l.id,l.dev FROM locs l JOIN shares s ON s.id=l.id WHERE s.grp=? AND s.mutual=1 AND s.exp>=? AND l.t>=?').bind(s.grp,now,now-6*3600e3).all()).results||[];
        return json({now,peers:rows.filter(r=>!(r.id===id&&r.dev===dev)).map(r=>({name:r.name+(r.n>1?' #'+r.n:''),lat:r.lat,lng:r.lng,acc:r.acc,t:r.t,stopped:r.stopped}))});
      }
      if((path==='/loc'||path==='/loc/stop')&&req.method==='POST'){
        const id=String(body.id||''),dev=String(body.dev||'');
        if(!/^[a-z0-9]{6,24}$/.test(id)||!/^[a-z0-9]{6,24}$/.test(dev)) return json({error:'bad id'},400);
        const s=await db.prepare('SELECT w,exp FROM shares WHERE id=?').bind(id).first();
        if(!s||!eq(body.w,s.w)) return json({error:'這個連結不能回傳位置'},403);
        if(s.exp<now) return json({error:'連結已經到期'},410);
        const cur=await db.prepare('SELECT n,t FROM locs WHERE id=? AND dev=?').bind(id,dev).first();
        if(path==='/loc/stop'){if(cur) await db.prepare('UPDATE locs SET stopped=1,t=? WHERE id=? AND dev=?').bind(now,id,dev).run();return json({ok:true,exp:s.exp});}
        const lat=num(body.lat,-90,90),lng=num(body.lng,-180,180);
        if(lat===null||lng===null) return json({error:'bad position'},400);
        const acc=num(body.acc,0,100000),hd=num(body.hd,0,360),sp=num(body.sp,0,200);
        if(cur&&now-cur.t<MIN_GAP) return json({ok:true,exp:s.exp,skip:true});   // 太頻繁就略過
        let n=cur?.n;
        if(!n){const c=(await db.prepare('SELECT COUNT(*) AS c, MAX(n) AS m FROM locs WHERE id=?').bind(id).first())||{};if((c.c||0)>=MAX_DEVS) return json({error:'這個連結回傳位置的裝置太多'},429);n=(c.m||0)+1;}
        await db.prepare('INSERT INTO locs (id,dev,n,lat,lng,acc,hd,sp,t,stopped) VALUES (?,?,?,?,?,?,?,?,?,0) ON CONFLICT(id,dev) DO UPDATE SET lat=excluded.lat,lng=excluded.lng,acc=excluded.acc,hd=excluded.hd,sp=excluded.sp,t=excluded.t,stopped=0')
          .bind(id,dev,n,lat,lng,acc,hd,sp,now).run();
        return json({ok:true,exp:s.exp});
      }
      return json({error:'not found'},404);
    }catch(e){return json({error:'伺服器錯誤'},500);}
  },
  async scheduled(ev,env){if(env.DB){await init(env.DB);await sweep(env.DB,Date.now());}}
};
