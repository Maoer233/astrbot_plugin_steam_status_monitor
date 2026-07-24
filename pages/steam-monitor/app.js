// Steam Monitor Admin
const bridge = window.AstrBotPluginPage;

function parsePluginEndpoint(url) {
  const [rawPath, rawQuery = ""] = String(url).split("?", 2);
  const endpoint = rawPath
    .replace(/^\/?api\/?/, "")
    .replace(/^\/+|\/+$/g, "");
  const params = {};
  for (const [key, value] of new URLSearchParams(rawQuery)) {
    params[key] = value;
  }
  return {endpoint, params};
}

const API = {
  async get(url) {
    const {endpoint, params} = parsePluginEndpoint(url);
    return bridge.apiGet(endpoint, Object.keys(params).length ? params : undefined);
  },
  async post(url, data) {
    const {endpoint} = parsePluginEndpoint(url);
    return bridge.apiPost(endpoint, data || {});
  },
};
document.querySelectorAll(".nav-item").forEach(it=>{it.addEventListener("click",()=>{const p=it.dataset.page;if(p)navigateTo(p)})});

async function navigateTo(page,params){const t=document.getElementById("hplayer-tooltip");if(t)t.style.display="none"
  document.querySelectorAll(".nav-item").forEach(i=>i.classList.toggle("active",i.dataset.page===page));
  const c=document.getElementById("content");
  c.innerHTML='<div class="page-loading"><span class="mdi mdi-loading mdi-spin"></span><p>加载中...</p></div>';
  try{const pages={dashboard:renderDashboard,gantt:renderGantt,heatmap:()=>renderHeatmap(params),groups:renderGroups,push:renderPush,settings:renderSettings,test:renderTest};await(pages[page]||renderDashboard)()}
  catch(e){c.innerHTML=`<div class="empty-state"><span class="mdi mdi-alert-circle"></span><p>加载失败: ${escapeHtml(e.message)}</p><button class="btn btn-primary mt-8" onclick="navigateTo(${jsArg(page)})">重试</button></div>`}
}

function toast(msg,t){const e=document.createElement("div");e.className=`toast toast-${t||"success"}`;e.textContent=msg;document.body.appendChild(e);setTimeout(()=>e.remove(),3000)}
let pageDialogResolve=null;
let pageDialogMode="confirm";
function showPageDialog({title,label="",mode="confirm",value=""}){
  const overlay=document.getElementById("page-dialog");
  const inputWrap=document.getElementById("page-dialog-input-wrap");
  const input=document.getElementById("page-dialog-input");
  document.getElementById("page-dialog-title").textContent=title;
  document.getElementById("page-dialog-label").textContent=label;
  inputWrap.style.display=mode==="prompt"?"block":"none";
  input.value=value;
  pageDialogMode=mode;
  overlay.classList.add("show");
  if(mode==="prompt")setTimeout(()=>input.focus(),0);
  return new Promise(resolve=>{pageDialogResolve=resolve})
}
function pagePrompt(title,label){return showPageDialog({title,label,mode:"prompt"})}
function pageConfirm(title){return showPageDialog({title,mode:"confirm"})}
window.resolvePageDialog=confirmed=>{
  const overlay=document.getElementById("page-dialog");
  const input=document.getElementById("page-dialog-input");
  overlay.classList.remove("show");
  if(!pageDialogResolve)return;
  const resolve=pageDialogResolve;
  pageDialogResolve=null;
  resolve(confirmed?(pageDialogMode==="prompt"?input.value.trim():true):(pageDialogMode==="prompt"?null:false))
};
function steamTheme(){return{backgroundColor:"transparent",textStyle:{color:"#e1e8ed"},legend:{textStyle:{color:"#aeb9c2"}},tooltip:{backgroundColor:"#2a475e",borderColor:"#355066",textStyle:{color:"#e1e8ed"}}}}
function disposeChart(id){const el=document.getElementById(id);if(el){const inst=echarts.getInstanceByDom(el);if(inst)inst.dispose()}}
function initChart(id){disposeChart(id);const el=document.getElementById(id);return el?echarts.init(el):null}
const coverCache=new Map();
async function loadCover(gameid){
  if(!gameid)return"";
  if(coverCache.has(gameid))return coverCache.get(gameid);
  try{
    const data=await API.get(`/api/games/cover/${gameid}`);
    const url=data.data_url||"";
    coverCache.set(gameid,url);
    return url
  }catch(_){
    coverCache.set(gameid,"");
    return""
  }
}

// ====== Dashboard ======
let dashPeriod="week";
async function renderDashboard(){
  const data=await API.get("/api/dashboard/stats");
  const c=document.getElementById("content");
  c.innerHTML=`<div class="flex-between mb-20"><h2 class="page-title">仪表盘</h2>
    <div class="tab-bar" id="dash-tabs">
      <button class="tab-btn" data-p="today">今天</button><button class="tab-btn" data-p="yesterday">昨天</button>
      <button class="tab-btn active" data-p="week">一周</button><button class="tab-btn" data-p="month">一个月</button>
    </div></div>
    <div class="stats-grid">
      <div class="stat-card"><div class="stat-value">${data.total_groups}</div><div class="stat-label">监控群聊</div></div>
      <div class="stat-card"><div class="stat-value">${data.total_players}</div><div class="stat-label">监控玩家</div></div>
      <div class="stat-card"><div class="stat-value">${data.today_active_players}</div><div class="stat-label">今日活跃</div></div>
      <div class="stat-card"><div class="stat-value">${data.total_bindings}</div><div class="stat-label">QQ绑定</div></div>
    </div>
    <div class="charts-row">
      <div class="card"><div class="card-title">玩家排行</div><div class="rank-img-wrapper"><img id="rank-image" style="max-width:100%;border-radius:var(--border-radius-lg)" onclick="document.getElementById('img-overlay').classList.add('show');document.getElementById('overlay-img').src=this.src"></div></div>
      <div class="card"><div class="card-title">热门游戏</div><div id="chart-top-games" class="chart-box" style="height:480px"></div></div>
    </div>
    <div class="card"><div class="card-title">在线玩家 (${data.players?.length||0}人)</div><div id="dash-player-cards" class="flex flex-wrap gap-8"></div></div>`;
  renderDashPlayerCards(data.players||[]);
  document.querySelectorAll("#dash-tabs .tab-btn").forEach(btn=>{btn.addEventListener("click",async()=>{document.querySelectorAll("#dash-tabs .tab-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");dashPeriod=btn.dataset.p;await loadDashboardCharts()})});
  await loadDashboardCharts();
}

function renderDashPlayerCards(players){
  const container=document.getElementById("dash-player-cards");if(!container)return;
  if(!players.length){container.innerHTML='<span style="color:var(--text-muted)">暂无玩家</span>';return}
  players.sort((a,b)=>{const sA=a.gameid?0:a.personastate>0?1:5;const sB=b.gameid?0:b.personastate>0?1:5;return sA-sB});
  container.innerHTML=players.map(p=>{
    const playing=!!p.gameid;const online=p.personastate>0;
    const border=playing?"var(--accent-green)":online?"var(--accent)":"var(--border-color)";
    const stxt=playing?`🎮 ${escapeHtml(p.game||"游戏中")}`:online?"● 在线":"○ 离线";
    const sc=playing?"var(--accent-green)":online?"var(--accent)":"var(--text-muted)";
    return `<div class="dash-pcard" data-sid="${escapeAttr(p.sid)}" style="border-color:${border}" onclick="navigateTo('heatmap',{player:${jsArg(p.sid)}})">
      <div class="dash-pav"><span class="mdi mdi-loading mdi-spin"></span></div>
      <div><div style="font-size:13px;font-weight:500;color:var(--text-bright)">${escapeHtml(p.name)}</div><div style="font-size:11px;color:${sc}">${stxt}</div></div></div>`
  }).join("");
  Promise.all(players.map(p=>loadAvatar(p.sid).then(av=>{const c=document.querySelector(`.dash-pcard[data-sid="${CSS.escape(String(p.sid))}"]`);if(!c)return;const a=c.querySelector(".dash-pav");if(a&&av)a.innerHTML=`<img src="${safeImageUrl(av)}">`})));
}

async function loadDashboardCharts(){
  const pm={today:{d:1,o:0},yesterday:{d:1,o:-1},week:{d:7,o:0},month:{d:30,o:0}};
  const {d:days,o:offset}=pm[dashPeriod]||pm.week;
  const img=document.getElementById("rank-image");
  if(img){
    try{
      const rank=await API.get(`/api/dashboard/rank-image?days=${days}&offset=${offset}`);
      const rankUrl=safeImageUrl(rank.data_url);
      img.src=rankUrl;
      img.style.display=rankUrl?"block":"none"
    }catch(_){
      img.removeAttribute("src");
      img.style.display="none"
    }
  }
  const gd=await API.get(`/api/gantt/data?days=${days}&offset=${offset}`);
  const players=gd.players||[];const details=gd.game_details||{};const gameColors=gd.game_colors||{};
  const gm={};
  players.forEach(p=>(p.sessions||[]).forEach(s=>{gm[s.gameid]=gm[s.gameid]||{name:s.game_name,minutes:0,gameid:s.gameid};gm[s.gameid].minutes+=s.duration_min||0}));
  const tg=Object.values(gm).sort((a,b)=>b.minutes-a.minutes);const top9=tg.slice(0,9);const restMins=tg.slice(9).reduce((s,g)=>s+g.minutes,0);if(restMins>0)top9.push({name:"其他",minutes:restMins,gameid:""});
  const covers={};
  await Promise.all(top9.filter(g=>g.gameid).map(async g=>{covers[g.gameid]=await loadCover(g.gameid)}));
  const gc=initChart("chart-top-games");if(!gc)return;
  const cols=["#5aa9d6","#b2d430","#e8a030","#b37cd4","#e05050","#50c8c8","#ff8c60","#60b0e0","#e0a0c0","#80d080"];
  gc.setOption({...steamTheme(),tooltip:{trigger:"item",backgroundColor:"#1e2c38",borderColor:"#2e4254",borderWidth:1,extraCssText:"border-radius:8px;box-shadow:0 4px 12px rgba(0,0,0,0.5)",formatter:p=>{
    const det=details[p.data.gameid];if(!det||!det.players)return`<b>${escapeHtml(p.name)}</b><br/>${p.value}分钟`;
    const cover=covers[p.data.gameid]||"";
    let h=`<div style="min-width:200px;max-width:260px"><b style="font-size:14px">${escapeHtml(det.name)}</b>${cover?`<img src="${safeImageUrl(cover)}" style="width:100%;max-width:240px;border-radius:6px;margin:8px 0;display:block">`:""}`;
    det.players.slice(0,5).forEach(pl=>{h+=`<div style="display:flex;justify-content:space-between;font-size:12px;margin:3px 0"><span>${escapeHtml(pl.name)}</span><span style="color:var(--accent)">${(pl.minutes/60).toFixed(1)}h</span></div>`});
    if(det.players.length>5){const om=det.players.slice(5).reduce((s,pl)=>s+pl.minutes,0);h+=`<div style="display:flex;justify-content:space-between;font-size:11px;color:var(--text-muted)"><span>其他${det.players.length-5}位</span><span>${(om/60).toFixed(1)}h</span></div>`}
    h+="</div>";return h}},legend:{bottom:0,textStyle:{color:"#aeb9c2"}},series:[{type:"pie",radius:["35%","60%"],center:["50%","32%"],data:top9.map((g,i)=>({name:g.name,value:g.minutes,gameid:g.gameid,itemStyle:{color:g.name==="其他"?"#555":cols[i%cols.length]}})),label:{color:"#e1e8ed",formatter:"{d}%",fontSize:12},emphasis:{label:{fontSize:16,fontWeight:"bold"}}}]});
}

// ====== Gantt ======
const GC=["#5aa9d6","#b2d430","#e8a030","#b37cd4","#e05050","#50c8c8","#ff8c60","#60b0e0","#e0a0c0","#80d080","#d0b060","#90c0f0"];
function gch(gid){let h=0;for(let i=0;i<gid.length;i++)h=((h<<5)-h)+gid.charCodeAt(i);return GC[Math.abs(h)%GC.length]}
async function renderGantt(){
  const c=document.getElementById("content");
  c.innerHTML=`<div class="flex-between mb-20"><h2 class="page-title">甘特图</h2>
    <div class="tab-bar" id="gantt-tabs">
      <button class="tab-btn active" data-d="1" data-o="0">今天</button><button class="tab-btn" data-d="1" data-o="-1">昨天</button>
      <button class="tab-btn" data-d="7" data-o="0">近7天</button><button class="tab-btn" data-d="30" data-o="0">近30天</button>
    </div></div>
    <div class="card"><div id="gantt-chart" class="gantt-container"></div></div>
    <div id="gantt-legend" style="margin-top:12px;display:flex;flex-wrap:wrap;gap:8px"></div>`;
  await loadGantt(1,0);
  document.querySelectorAll("#gantt-tabs .tab-btn").forEach(btn=>{btn.addEventListener("click",async()=>{document.querySelectorAll("#gantt-tabs .tab-btn").forEach(b=>b.classList.remove("active"));btn.classList.add("active");await loadGantt(parseInt(btn.dataset.d),parseInt(btn.dataset.o))})});
}
async function loadGantt(days,offset){
  const data=await API.get(`/api/gantt/data?days=${days}&offset=${offset}`);
  const players=data.players||[];
  const gs={};players.forEach(p=>(p.sessions||[]).forEach(s=>{gs[s.gameid]=s.game_name}));
  const leg=document.getElementById("gantt-legend");
  leg.innerHTML=Object.keys(gs).map(gid=>`<span style="display:inline-flex;align-items:center;gap:4px;font-size:12px;color:var(--text-secondary)"><span style="width:12px;height:12px;border-radius:2px;background:${gch(gid)};display:inline-block"></span>${escapeHtml(gs[gid])}</span>`).join("");
  const chart=initChart("gantt-chart");if(!chart)return;
  if(!players.length){chart.clear();leg.innerHTML='<span style="color:var(--text-muted)">暂无记录</span>';return}
  const names=players.map(p=>p.name);const sd=[];
  players.forEach((p,pi)=>(p.sessions||[]).forEach(s=>{if(s.start>0&&s.end>0)sd.push({name:p.name,value:[pi,new Date(s.start*1000),new Date(s.end*1000)],game_name:s.game_name,duration_min:s.duration_min,gameid:s.gameid,itemStyle:{color:gch(s.gameid)}})}));
  if(!sd.length){chart.clear();return}
  const mT=data.time_range?new Date(data.time_range.start):new Date();const xT=data.time_range?new Date(data.time_range.end):new Date();
  chart.setOption({...steamTheme(),tooltip:{formatter:p=>{const sd=new Date(p.data.value[1]).toTimeString().slice(0,5);const ed=new Date(p.data.value[2]).toTimeString().slice(0,5);return`<b>${escapeHtml(p.name)}</b> - ${escapeHtml(p.data.game_name)}<br/>${sd} → ${ed}<br/>时长: ${p.data.duration_min}分钟`}},grid:{left:140,right:30,top:20,bottom:50},
    xAxis:{type:"time",min:mT,max:xT,axisLabel:{color:"#aeb9c2",formatter:v=>{const d=new Date(v);return`${String(d.getHours()).padStart(2,"0")}:${String(d.getMinutes()).padStart(2,"0")}`}},splitLine:{show:true,lineStyle:{color:"rgba(255,255,255,0.06)",type:"dashed"}}},
    yAxis:{type:"category",data:names,axisLabel:{color:"#e1e8ed",width:120,overflow:"truncate"}},
    series:[{type:"custom",renderItem:(p,api)=>{const ci=api.value(0);const s=api.coord([api.value(1),ci]);const e=api.coord([api.value(2),ci]);return{type:"rect",shape:{x:s[0],y:s[1]-11,width:Math.max(e[0]-s[0],4),height:22},style:{fill:api.style().fill,opacity:0.85}}},encode:{x:[1,2],y:0},data:sd}],
    dataZoom:[{type:"slider",start:0,end:100,height:22,bottom:12,textStyle:{color:"#aeb9c2"}}]});
}

// ====== Heatmap ======
async function renderHeatmap(params){
  if(params&&params.player){await renderPlayerHeatmap(params.player);return}
  const data=await API.get("/api/heatmap/data?period=90");
  const c=document.getElementById("content");
  c.innerHTML=`<h2 class="page-title">团队贡献日历</h2>
    <div class="card mb-20"><div class="flex-between mb-12"><div class="card-title">近90天团队活跃度</div>
      <div style="display:flex;align-items:center;gap:6px;font-size:11px;color:var(--text-muted)"><span>少</span>
      <span style="width:10px;height:10px;border-radius:2px;background:transparent;border:1px solid var(--border-color)"></span>
      <span style="width:10px;height:10px;border-radius:2px;background:rgb(2,46,18)"></span>
      <span style="width:10px;height:10px;border-radius:2px;background:#006d32"></span>
      <span style="width:10px;height:10px;border-radius:2px;background:#26a641"></span>
      <span style="width:10px;height:10px;border-radius:2px;background:#39d353"></span>
      <span>多</span></div></div>
      <div id="heatmap-cal" class="chart-box-lg"></div></div>
    <div class="card"><div class="card-title">玩家列表</div><div id="heatmap-players" class="flex flex-wrap gap-12"></div></div>`;
  if(typeof echarts!=="undefined"&&data.heatmap_data){
    const hd=data.heatmap_data;const dates=Object.keys(hd).sort();const cd=dates.map(d=>[d,hd[d]]);const maxV=Math.max(...Object.values(hd),60);
    const hm=initChart("heatmap-cal");if(hm)hm.setOption({...steamTheme(),tooltip:{formatter:p=>{const hrs=(p.data[1]/60).toFixed(1);return`${p.data[0]}<br/><b>${p.data[1]}</b>分钟(${hrs}h)`}},
      visualMap:{min:0,max:maxV,orient:"horizontal",left:"center",bottom:0,inRange:{color:["rgb(17,22,28)","rgb(2,46,18)","#006d32","#26a641","#39d353","#6ae07a"]},textStyle:{color:"#aeb9c2"}},
      calendar:{range:dates.length?[dates[0],dates[dates.length-1]]:"2026-07",cellSize:[20,20],dayLabel:{color:"#aeb9c2"},monthLabel:{color:"#aeb9c2"},itemStyle:{borderColor:"#0a0e12",borderWidth:3,borderRadius:2}},
      series:[{type:"heatmap",coordinateSystem:"calendar",data:cd}]});
  }
  const pd=document.getElementById("heatmap-players");
  if(!data.players||!data.players.length){pd.innerHTML='<div class="empty-state"><span class="mdi mdi-calendar-blank"></span><p>暂无记录</p></div>';return}
  pd.innerHTML=data.players.map(p=>`<div class="player-card-mini" data-sid="${escapeAttr(p.sid)}" onclick="navigateTo('heatmap',{player:${jsArg(p.sid)}})">
    <div class="player-avatar"><span class="mdi mdi-loading mdi-spin" style="font-size:16px;color:var(--text-muted)"></span></div>
    <div><div style="font-size:14px;font-weight:500;color:var(--text-bright)">${escapeHtml(p.name)}</div><div style="font-size:12px;color:var(--text-secondary)">${(p.total_minutes/60).toFixed(1)}h</div></div></div>`).join("");
  Promise.all(data.players.map(p=>loadAvatar(p.sid).then(av=>{const card=document.querySelector(`.player-card-mini[data-sid="${CSS.escape(String(p.sid))}"]`);if(!card)return;const a=card.querySelector(".player-avatar");if(a&&av)a.innerHTML=`<img src="${safeImageUrl(av)}">`})));
  document.querySelectorAll(".player-card-mini").forEach(card=>{const sid=card.dataset.sid;
    card.addEventListener("mouseenter",async e=>await showTooltip(e,sid));
    card.addEventListener("mouseleave",()=>{const t=document.getElementById("hplayer-tooltip");if(t)t.style.display="none"});
    card.addEventListener("mousemove",e=>{const t=document.getElementById("hplayer-tooltip");if(t&&t.style.display!=="none"){t.style.left=(e.clientX+16)+"px";t.style.top=(e.clientY+16)+"px"}})});
}
async function loadAvatar(sid){try{const r=await API.get(`/api/players/avatar/${sid}`);return r.avatar_url||""}catch(e){return""}}
async function showTooltip(e,sid){
  const t=document.getElementById("hplayer-tooltip");try{
    const info=await API.get(`/api/players/info/${sid}`);const av=await loadAvatar(sid);
    t.innerHTML=`<div style="display:flex;gap:12px;align-items:flex-start"><img src="${safeImageUrl(av)}" style="width:48px;height:48px;border-radius:8px;background:var(--bg-tertiary)" onerror="this.style.display='none'"><div><div style="font-size:15px;font-weight:500;color:var(--text-bright)">${escapeHtml(info.name)}</div><div style="font-size:11px;color:var(--text-muted)">SteamID:${escapeHtml(info.steamid)}</div>${info.current_game?`<div style="font-size:12px;color:var(--accent-green);margin-top:2px">🎮 ${escapeHtml(info.current_game)}</div>`:""}<div style="font-size:12px;color:var(--text-secondary);margin-top:6px">总计${(info.total_minutes/60).toFixed(1)}h·${info.total_sessions}次</div></div></div>`;
    t.style.display="block";t.style.left=(e.clientX+16)+"px";t.style.top=(e.clientY+16)+"px"}catch(ex){t.style.display="none"}
}
async function renderPlayerHeatmap(sid){
  const data=await API.get(`/api/heatmap/player/${sid}?period=90`);const av=await loadAvatar(sid);
  const c=document.getElementById("content");
  c.innerHTML=`<div class="flex-between mb-20"><h2 class="page-title"><img src="${safeImageUrl(av)}" style="width:28px;height:28px;border-radius:6px;vertical-align:middle;margin-right:8px;background:var(--bg-tertiary)" onerror="this.style.display='none'">${escapeHtml(data.name)}的贡献日历</h2><button class="btn btn-primary" onclick="navigateTo('heatmap')">←返回</button></div>
    <div class="stats-grid mb-20"><div class="stat-card"><div class="stat-value">${(data.total_minutes/60).toFixed(1)}h</div><div class="stat-label">总时长</div></div><div class="stat-card"><div class="stat-value">${data.avg_daily_minutes}min</div><div class="stat-label">日均</div></div><div class="stat-card"><div class="stat-value">${data.days_played}</div><div class="stat-label">活跃天数</div></div></div>
    <div class="charts-row"><div class="card"><div class="card-title">日历热力图</div><div id="heatmap-player-cal" class="chart-box"></div></div><div class="card"><div class="card-title">游戏占比</div><div id="heatmap-player-pie" class="chart-box-lg" style="height:480px"></div></div></div>`;
  if(typeof echarts!=="undefined"){
    const hd=data.heatmap_daily||{};const dates=Object.keys(hd).sort();const cd=dates.map(d=>[d,hd[d]]);const maxV=Math.max(...Object.values(hd),60);
    const hm=initChart("heatmap-player-cal");if(hm)hm.setOption({...steamTheme(),tooltip:{formatter:p=>{const hrs=(p.data[1]/60).toFixed(1);return`${p.data[0]}<br/><b>${p.data[1]}</b>分钟(${hrs}h)`}},visualMap:{min:0,max:maxV,orient:"horizontal",left:"center",bottom:0,inRange:{color:["rgb(17,22,28)","rgb(2,46,18)","#006d32","#26a641","#39d353","#6ae07a"]},textStyle:{color:"#aeb9c2"}},calendar:{range:dates.length?[dates[0],dates[dates.length-1]]:"2026-07",cellSize:[20,20],dayLabel:{color:"#aeb9c2"},monthLabel:{color:"#aeb9c2"},itemStyle:{borderColor:"#0a0e12",borderWidth:3,borderRadius:2}},series:[{type:"heatmap",coordinateSystem:"calendar",data:cd}]});
    const cols=["#5aa9d6","#b2d430","#e8a030","#b37cd4","#e05050","#50c8c8","#ff8c60","#60b0e0"];
    const pc=initChart("heatmap-player-pie");
    if(pc){
      const pg=(data.top_games||[]).sort((a,b)=>b.minutes-a.minutes);
      const pt9=pg.slice(0,9);
      const pr=pg.slice(9).reduce((s,g)=>s+g.minutes,0);
      if(pr>0)pt9.push({name:"其他",minutes:pr,gameid:""});
      const covers={};
      await Promise.all(pt9.filter(g=>g.gameid).map(async g=>{covers[g.gameid]=await loadCover(g.gameid)}));
      pc.setOption({
        ...steamTheme(),
        tooltip:{
          backgroundColor:"#1e2c38",
          borderColor:"#2e4254",
          borderWidth:1,
          extraCssText:"border-radius:8px",
          trigger:"item",
          formatter:p=>{
            const gi=p.data.gameid;
            const cover=covers[gi]||"";
            return`<div style="min-width:160px"><b>${escapeHtml(p.name)}</b>${cover?`<br><img src="${safeImageUrl(cover)}" style="width:100%;max-width:220px;border-radius:6px;margin:8px 0">`:""}<br>${p.value}分钟</div>`
          },
        },
        legend:{bottom:0,textStyle:{color:"#aeb9c2"}},
        series:[{
          type:"pie",
          radius:["35%","60%"],
          center:["50%","32%"],
          data:pt9.map((g,i)=>({
            name:g.name,
            value:g.minutes,
            gameid:g.gameid,
            itemStyle:{color:g.name==="其他"?"#555":cols[i%cols.length]},
          })),
          label:{color:"#e1e8ed",formatter:"{d}%",fontSize:12},
          emphasis:{label:{fontSize:16,fontWeight:"bold"}},
        }],
      });
    }
  }
}

// ====== Groups ======
async function renderGroups(){
  const [gData,bData]=await Promise.all([API.get("/api/groups"),API.get("/api/bindings")]);
  const groups=gData.groups||{};const gids=Object.keys(groups);const binds={}; (bData.bindings||[]).forEach(b=>{if(b.steamid)binds[b.steamid]={qq:b.qq,nickname:b.nickname}});
  const c=document.getElementById("content");
  c.innerHTML=`<div class="flex-between mb-20"><h2 class="page-title">群聊管理</h2><div class="flex gap-8"><span style="color:var(--text-muted)">${gids.length}个群</span><button class="btn btn-primary btn-sm" onclick="addGroup()">+添加群聊</button></div></div>
    <div class="groups-layout"><div class="card" id="group-list"></div><div class="card" id="group-detail"><div class="empty-state">选择一个群</div></div></div>
    <div class="modal-overlay" id="batch-modal"><div class="modal" style="max-width:560px"><div class="modal-title">批量导入</div>
      <p style="font-size:12px;color:var(--text-muted);margin-bottom:8px">每行一条：SteamID/链接  QQ号  备注名（后两项可选，支持 # 注释）</p>
      <textarea id="batch-text" class="form-input" style="height:200px;font-family:monospace;font-size:12px" placeholder="76561198123456789 123456789 小明&#10;https://steamcommunity.com/id/MaoerMaster/ 987654321&#10;123456789&#10;# 这是注释行"></textarea>
      <div id="batch-result" style="margin-top:8px;font-size:12px"></div>
      <div class="modal-actions"><button class="btn" onclick="document.getElementById('batch-modal').classList.remove('show')">取消</button><button class="btn btn-primary" onclick="runBatch(${jsArg(gids[0])})">导入</button></div></div></div>
    <div class="modal-overlay" id="addsid-modal"><div class="modal" style="max-width:400px"><div class="modal-title" id="addsid-title"></div>
      <div class="form-group"><label class="form-label">SteamID</label><input id="addsid-sid" class="form-input" placeholder="17位SteamID / 链接 / 好友码"></div>
      <div class="form-group"><label class="form-label">QQ号（可选）</label><input id="addsid-qq" class="form-input"></div>
      <div class="form-group"><label class="form-label">备注名（可选）</label><input id="addsid-nick" class="form-input"></div>
      <div class="modal-actions"><button class="btn" onclick="document.getElementById('addsid-modal').classList.remove('show')">取消</button><button class="btn btn-primary" id="addsid-confirm">添加</button></div></div></div>
    <div class="modal-overlay" id="bind-modal"><div class="modal" style="max-width:360px"><div class="modal-title">绑定QQ</div>
      <div class="form-group"><label class="form-label">QQ号</label><input id="bind-qq" class="form-input"></div>
      <div class="form-group"><label class="form-label">备注名（可选）</label><input id="bind-nick" class="form-input"></div>
      <div class="modal-actions"><button class="btn" onclick="document.getElementById('bind-modal').classList.remove('show')">取消</button><button class="btn btn-primary" id="bind-confirm">确认</button></div></div></div>`;
  let sel=gids[0]||null;
  function renderList(){const l=document.getElementById("group-list");l.innerHTML=gids.map(gid=>`<div class="group-list-item${gid===sel?" active":""}" style="display:flex;justify-content:space-between;align-items:center" onclick="selG(${jsArg(gid)})"><span>群${escapeHtml(gid)}(${groups[gid].length}人)</span><button class="btn btn-danger btn-sm" style="padding:2px 6px;font-size:11px" onclick="event.stopPropagation();delGroup(${jsArg(gid)})">✕</button></div>`).join("")}
  window.selG=g=>{sel=g;renderList();renderDetail(g)};
  async function renderDetail(gid){
    const ps=groups[gid]||[];const d=document.getElementById("group-detail");
    let h=`<div class="flex-between mb-16"><span class="card-title" style="margin-bottom:0">群${escapeHtml(gid)}</span><div class="flex gap-8"><button class="btn btn-primary btn-sm" onclick="showAddSIDModal(${jsArg(gid)})">+添加</button><button class="btn btn-sm" onclick="showBatchModal(${jsArg(gid)})">📋批量导入</button></div></div>`;
    if(!ps.length){h+='<div class="empty-state"><span class="mdi mdi-account-off"></span><p>暂无玩家</p></div>';d.innerHTML=h;return}
    h+='<table class="table"><thead><tr><th></th><th>玩家</th><th style="width:100px">绑定QQ</th><th>备注</th><th>状态</th><th>操作</th></tr></thead><tbody>';
    ps.forEach(p=>{const bg=p.gameid?'<span class="badge badge-playing">游戏中</span>':p.personastate>0?'<span class="badge badge-online">在线</span>':'<span class="badge badge-offline">离线</span>';const b=binds[p.sid];const qqCell=b?`<span class="monospace" onclick="editBindNick(${jsArg(p.sid)},${jsArg(b.qq)},${jsArg(b.nickname)})" style="cursor:pointer" title="点击修改备注">${escapeHtml(b.qq)}</span><button class="btn btn-danger btn-sm" style="padding:0 5px;margin-left:6px;font-size:10px" onclick="event.stopPropagation();unbindQQ(${jsArg(p.sid)},${jsArg(b.qq)})" title="解绑">✕</button>`:`<button class="btn btn-sm" onclick="showBindModal(${jsArg(p.sid)})" title="绑定QQ">绑定</button>`;const nickCell=b?`<span onclick="editBindNick(${jsArg(p.sid)},${jsArg(b.qq)},${jsArg(b.nickname)})" style="cursor:pointer;color:var(--accent)" title="点击修改备注">${escapeHtml(b.nickname&&b.nickname!=="*"?b.nickname:"-")}</span>`:"-";h+=`<tr><td><div data-avatar-sid="${escapeAttr(p.sid)}" style="width:28px;height:28px;border-radius:6px;background:var(--bg-tertiary);display:flex;align-items:center;justify-content:center"><span class="mdi mdi-loading mdi-spin" style="font-size:14px;color:var(--text-muted)"></span></div></td><td>${escapeHtml(p.name)}</td><td>${qqCell}</td><td>${nickCell}</td><td>${bg}</td><td><button class="btn btn-danger btn-sm" onclick="removeSteamID(${jsArg(gid)},${jsArg(p.sid)},${jsArg(p.name)})">删除</button></td></tr>`});
    h+='</tbody></table>';d.innerHTML=h;
    Promise.all(ps.map(p=>loadAvatar(p.sid).then(av=>{const el=document.querySelector(`[data-avatar-sid="${CSS.escape(String(p.sid))}"]`);if(el&&av)el.innerHTML=`<img src="${safeImageUrl(av)}" style="width:28px;height:28px;border-radius:6px;background:var(--bg-tertiary)">`})));
  }
  renderList();if(sel)await renderDetail(sel);
}
window.addGroup=async()=>{const g=await pagePrompt("添加群聊","群号");if(!g)return;const r=await API.post("/api/groups/add-group",{group_id:g});if(r.ok){toast("已添加");navigateTo("groups")}else toast(r.error||"失败","error")};
window.delGroup=async gid=>{if(!await pageConfirm(`删除群 ${gid}？`))return;const r=await API.post("/api/groups/delete-group",{group_id:gid});if(r.ok){toast("已删除");navigateTo("groups")}else toast(r.error||"失败","error")};
window.showAddSIDModal=gid=>{document.getElementById("addsid-title").textContent=`向群 ${gid} 添加玩家`;document.getElementById("addsid-sid").value="";document.getElementById("addsid-qq").value="";document.getElementById("addsid-nick").value="";const btn=document.getElementById("addsid-confirm");const newBtn=btn.cloneNode(true);btn.parentNode.replaceChild(newBtn,btn);newBtn.onclick=async()=>{const s=document.getElementById("addsid-sid").value,q=document.getElementById("addsid-qq").value,n=document.getElementById("addsid-nick").value;if(!s){toast("请输入SteamID","error");return}const r=await API.post("/api/groups/add",{group_id:gid,steamid:s,qq:q,nickname:n});if(r.ok){toast("添加成功");document.getElementById("addsid-modal").classList.remove("show");navigateTo("groups")}else toast(r.error||"失败","error")};document.getElementById("addsid-modal").classList.add("show")};
window.removeSteamID=async(gid,sid,name)=>{if(!await pageConfirm(`从群 ${gid} 删除 ${name}？`))return;const r=await API.post("/api/groups/delete",{group_id:gid,steamid:sid});if(r.ok){toast("已删除");navigateTo("groups")}else toast(r.error||"失败","error")};
window.showBatchModal=gid=>{document.getElementById("batch-text").value="";document.getElementById("batch-result").innerHTML="";const textarea=document.getElementById("batch-text");textarea.dataset.gid=gid;document.getElementById("batch-modal").classList.add("show")};
window.runBatch=async gid=>{const text=document.getElementById("batch-text").value;if(!text.trim())return;const btn=event.target;btn.disabled=true;btn.textContent="导入中...";try{const r=await API.post("/api/groups/import-batch",{group_id:gid,text:text});const res=document.getElementById("batch-result");if(r.ok)res.innerHTML=`<span style="color:var(--accent-green)">✅ 已导入 ${r.imported} 条</span>${r.errors.length?`<br><span style="color:var(--accent-red)">${r.errors.join('<br>')}</span>`:""}`;if(r.imported>0)navigateTo("groups")}catch(e){toast(e.message,"error")}finally{btn.disabled=false;btn.textContent="导入"}};
window.showBindModal=sid=>{document.getElementById("bind-qq").value="";document.getElementById("bind-nick").value="";const btn=document.getElementById("bind-confirm");const newBtn=btn.cloneNode(true);btn.parentNode.replaceChild(newBtn,btn);newBtn.onclick=async()=>{const q=document.getElementById("bind-qq").value,n=document.getElementById("bind-nick").value;if(!q){toast("请输入QQ号","error");return}const r=await API.post("/api/bindings/add",{qq:q,steamid:sid,nickname:n});if(r.ok){toast("绑定成功");document.getElementById("bind-modal").classList.remove("show");navigateTo("groups")}else toast(r.error||"失败","error")};document.getElementById("bind-modal").classList.add("show")};
window.unbindQQ=async(sid,qq)=>{if(!await pageConfirm(`解除 ${qq} 的绑定？`))return;const r=await API.post("/api/bindings/delete",{qq});if(r.ok){toast("已解绑");navigateTo("groups")}else toast(r.error||"失败","error")};
window.editBindNick=async(sid,qq,oldNick)=>{const nick=await pagePrompt("修改备注",oldNick||"");if(nick===null)return;const r=await API.post("/api/bindings/update",{qq,nickname:nick});if(r.ok){toast("已更新");navigateTo("groups")}else toast(r.error||"失败","error")};

// ====== Push & Settings ======
async function renderPush(){
  const d=await API.get("/api/push/settings");const c=document.getElementById("content");
  c.innerHTML=`<h2 class="page-title">每日推送设置</h2>
    <div class="card mb-20"><div class="card-title">推送时间</div><div class="flex gap-8" style="align-items:center"><input id="push-hour" class="form-input" type="number" min="0" max="23" value="${d.rank_push_hour}" style="width:80px"><span>时</span><input id="push-min" class="form-input" type="number" min="0" max="59" value="${d.rank_push_minute}" style="width:80px"><span>分</span><button class="btn btn-primary" onclick="updPush()">保存</button></div></div>
    <div class="card mb-20"><div class="card-title">榜单内容范围</div><select id="push-rank-scope" class="form-input" onchange="setRankScope(this.value)" style="max-width:360px"><option value="group" ${d.rank_push_all?"":"selected"}>每个群独立统计本群玩家</option><option value="global" ${d.rank_push_all?"selected":""}>所有目标群共享全局榜单</option></select><p class="permission-help" style="margin-top:8px">默认使用分群榜单；只有显式选择全局模式时，目标群才会收到同一张总榜。</p></div>
    <div class="card"><div class="card-title">接收每日榜单的群聊</div><div id="push-group-list"></div></div>`;
  const allG=d.all_groups||[],pushG=d.rank_push_groups||[];
  document.getElementById("push-group-list").innerHTML=allG.map(gid=>`<div class="form-group" style="margin-bottom:8px"><label class="flex gap-8" style="align-items:center;cursor:pointer"><label class="toggle"><input type="checkbox" ${pushG.includes(gid)?"checked":""} onchange="togGroup(${jsArg(gid)},this.checked)"><span class="slider"></span></label><span>群${escapeHtml(gid)}</span></label></div>`).join("");
  window.updPush=async()=>{const h=parseInt(document.getElementById("push-hour").value),m=parseInt(document.getElementById("push-min").value);await API.post("/api/push/update",{rank_push_hour:h,rank_push_minute:m});toast("已更新")};
  window.togGroup=async(gid,on)=>{await API.post(on?"/api/push/groups/add":"/api/push/groups/remove",{group_id:gid})};
  window.setRankScope=async scope=>{await API.post("/api/push/rank-scope",{scope});toast(scope==="group"?"已切换为每群独立榜单":"已切换为共享全局榜单")};
}
// ====== Command Permissions ======
async function loadPermissionSettings(){
  const body=document.getElementById("permission-settings-body");if(!body)return;
  body.innerHTML='<div class="page-loading permission-loading"><span class="mdi mdi-loading mdi-spin"></span><p>正在读取 AstrBot 指令权限...</p></div>';
  try{
    const data=await API.get("/api/permissions");const commands=data.commands||[];
    if(!commands.length){body.innerHTML='<div class="empty-state">未发现本插件指令</div>';return}
    body.innerHTML=`<p class="permission-help">这里直接读取和修改 AstrBot 框架的实际权限。member 表示所有成员可用，admin 表示仅管理员可用。</p>
      <div class="permission-table-wrap"><table class="table"><thead><tr><th>有效指令</th><th>描述</th><th>权限</th><th>状态</th></tr></thead><tbody id="permission-tbody"></tbody></table></div>`;
    const tbody=document.getElementById("permission-tbody");
    tbody.innerHTML=commands.map((command,index)=>`<tr><td class="monospace">${escapeHtml(command.effective_command||command.original_command||command.handler_name||"-")}</td><td>${escapeHtml(command.description||"-")}</td><td><select class="form-input permission-select" data-permission-index="${index}"><option value="admin" ${command.permission==="admin"?"selected":""}>admin</option><option value="member" ${command.permission==="member"?"selected":""}>member</option></select></td><td><span class="badge ${command.enabled?"badge-online":"badge-offline"}">${command.enabled?"已启用":"已停用"}</span></td></tr>`).join("");
    document.querySelectorAll("[data-permission-index]").forEach(select=>select.addEventListener("change",async()=>{
      const command=commands[Number(select.dataset.permissionIndex)];const previous=command.permission;select.disabled=true;
      try{const result=await API.post("/api/permissions/update",{handler_full_name:command.handler_full_name,permission:select.value});command.permission=result.command.permission;select.value=command.permission;toast("指令权限已同步")}
      catch(error){select.value=previous;toast(error.message,"error")}
      finally{select.disabled=false}
    }));
  }catch(error){body.innerHTML=`<div class="empty-state"><span class="mdi mdi-alert-circle"></span><p>权限加载失败：${escapeHtml(error.message)}</p><button class="btn btn-primary mt-8" onclick="loadPermissionSettings()">重试</button></div>`}
}
function escapeHtml(value){const div=document.createElement("div");div.textContent=String(value??"");return div.innerHTML}
function escapeAttr(value){return escapeHtml(value).replace(/"/g,"&quot;").replace(/'/g,"&#39;").replace(/`/g,"&#96;")}
function jsArg(value){return escapeAttr(JSON.stringify(String(value??"")))}
function safeImageUrl(value){
  const url=String(value||"").trim();
  if(/^data:image\/[a-z0-9.+-]+;base64,/i.test(url))return escapeAttr(url);
  try{
    const parsed=new URL(url);
    if(parsed.protocol==="https:"||parsed.protocol==="http:")return escapeAttr(url)
  }catch(_){}
  return""
}

const SL={steam_api_key:"Steam Web API密钥",sgdb_api_key:"SteamGridDB API密钥",fixed_poll_interval:"固定轮询间隔(秒)",smart_poll_intervals:"智能轮询间隔(分)",retry_times:"API重试次数",max_group_size:"单群最大监控人数",detailed_poll_log:"详细轮询日志",enable_achievement_poll:"成就轮询推送",enable_game_start_notify:"游戏开始通知",enable_game_end_notify:"游戏结束通知",notify_send_image:"通知发送图片",notify_send_text:"通知发送文本",enable_proxy:"启用代理",proxy_url:"代理链接",cache_avatar_hours:"头像缓存(小时)",cache_avatar_frame_hours:"头像框缓存(小时)",game_filter_mode:"游戏过滤模式",game_filter_ids:"过滤游戏ID",rank_push_hour:"推送-时",rank_push_minute:"推送-分"};
const SO=["steam_api_key","sgdb_api_key","fixed_poll_interval","smart_poll_intervals","retry_times","max_group_size","enable_game_start_notify","enable_game_end_notify","enable_achievement_poll","notify_send_text","notify_send_image","detailed_poll_log","game_filter_mode","game_filter_ids","rank_push_hour","rank_push_minute","enable_proxy","proxy_url","cache_avatar_hours","cache_avatar_frame_hours"];
const BK=["detailed_poll_log","enable_achievement_poll","enable_game_start_notify","enable_game_end_notify","notify_send_image","notify_send_text"];
const IK=["fixed_poll_interval","retry_times","max_group_size","cache_avatar_hours","cache_avatar_frame_hours","rank_push_hour","rank_push_minute"];
const SK=["smart_poll_intervals","proxy_url","game_filter_ids"];const SEK=["steam_api_key","sgdb_api_key"];
async function renderSettings(){
  const data=await API.get("/api/settings");const c=document.getElementById("content");
  let h='<h2 class="page-title">插件设置</h2><div class="card">';
  for(const k of SO){const l=SL[k]||k;
    if(SEK.includes(k))h+=`<div class="form-group"><label class="form-label">${l}</label><input id="cfg-${k}" class="form-input" type="password"></div>`;
    else if(k==="game_filter_mode"){h+=`<div class="form-group"><label class="form-label">${l}</label><select id="cfg-${k}" class="form-input">${["全部游戏","白名单","黑名单"].map(m=>`<option ${data.game_filter_mode===m?"selected":""}>${m}</option>`).join("")}</select></div>`}
    else if(BK.includes(k))h+=`<div class="form-group"><label class="flex gap-8" style="align-items:center;cursor:pointer"><label class="toggle"><input type="checkbox" id="cfg-${k}" ${data[k]?"checked":""}><span class="slider"></span></label><span>${l}</span></label></div>`;
    else if(IK.includes(k))h+=`<div class="form-group"><label class="form-label">${l}</label><input id="cfg-${k}" class="form-input" type="number" value="${escapeAttr(data[k]??"")}"></div>`;
    else h+=`<div class="form-group"><label class="form-label">${l}</label><input id="cfg-${k}" class="form-input" value="${escapeAttr(data[k]??"")}"></div>`}
  h+='<button class="btn btn-primary mt-8" onclick="saveSet()">保存全部设置</button></div>';
  h+=`<details class="card settings-collapsible" id="permission-settings"><summary><span><span class="mdi mdi-shield-account"></span> 指令权限</span><span class="summary-hint">按指令设置 admin / member</span></summary><div id="permission-settings-body" class="collapsible-body"><p class="permission-help">展开后读取 AstrBot 当前权限配置。</p></div></details>`;
  c.innerHTML=h;
  const permissionDetails=document.getElementById("permission-settings");permissionDetails.addEventListener("toggle",()=>{if(permissionDetails.open&&!permissionDetails.dataset.loaded){permissionDetails.dataset.loaded="1";loadPermissionSettings()}});
  window.saveSet=async()=>{const p={};BK.forEach(k=>p[k]=document.getElementById(`cfg-${k}`).checked);SEK.forEach(k=>{const v=document.getElementById(`cfg-${k}`).value;if(v)p[k]=v});p.enable_proxy=document.getElementById("cfg-enable_proxy").checked;IK.forEach(k=>p[k]=parseInt(document.getElementById(`cfg-${k}`).value)||0);SK.forEach(k=>p[k]=document.getElementById(`cfg-${k}`).value);p.game_filter_mode=document.getElementById("cfg-game_filter_mode").value;const r=await API.post("/api/settings/update",p);if(r.ok)toast("已保存，部分需重启生效");else toast(r.error||"失败","error")};
}

// ====== Test ======
async function renderTest(){
  const c=document.getElementById("content");
  c.innerHTML=`<h2 class="page-title">连接测试</h2>
    <div class="card mb-20"><div class="flex-between mb-12"><div class="card-title">Steam API</div><button class="btn btn-sm btn-primary" onclick="runSteamTest()">开始测试</button></div>
      <p style="color:var(--text-muted);font-size:12px;margin-bottom:12px">Steam Web API · Steam Store · 横版封面获取</p><div id="test-steam-result" class="test-grid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr))"></div></div>
    <div class="card mb-20"><div class="flex-between mb-12"><div class="card-title">SteamGridDB</div><button class="btn btn-sm btn-primary" onclick="runSteamTest()">开始测试</button></div>
      <p style="color:var(--text-muted);font-size:12px;margin-bottom:12px">API连通性 · 竖版封面获取</p><div id="test-sgdb-result" class="test-grid" style="grid-template-columns:repeat(auto-fill,minmax(140px,1fr))"></div></div>
    <div class="card"><div class="card-title" style="margin-bottom:12px">SteamID查询</div>
      <div class="flex gap-8"><input id="test-sid-input" class="form-input" placeholder="7656119..." style="max-width:280px"><button class="btn btn-primary" onclick="runSidTest()">查询</button></div>
      <div id="test-sid-result" style="margin-top:12px"></div></div>`;
}
window.runSteamTest=async()=>{
  document.getElementById("test-steam-result").innerHTML='<div style="grid-column:1/-1;text-align:center;padding:16px;color:var(--text-muted)"><span class="mdi mdi-loading mdi-spin" style="font-size:24px"></span><p style="margin-top:8px">测试中...</p></div>';
  document.getElementById("test-sgdb-result").innerHTML='<div style="grid-column:1/-1;text-align:center;padding:16px;color:var(--text-muted)"><span class="mdi mdi-loading mdi-spin" style="font-size:24px"></span><p style="margin-top:8px">测试中...</p></div>';
  try{
    const r=await API.get("/api/test/steam");
    document.getElementById("test-steam-result").innerHTML=["steam_api","steam_store","cover_horizontal"].map(k=>`<div class="test-stat"><div class="test-stat-val" style="color:${r[k]==="ok"?"var(--accent-green)":"var(--accent-red)"}">${escapeHtml(r[k])}</div><div class="test-stat-lbl">${k}</div></div>`).join("");
    document.getElementById("test-sgdb-result").innerHTML=["sgdb","sgdb_cover"].map(k=>`<div class="test-stat"><div class="test-stat-val" style="color:${r[k]==="ok"?"var(--accent-green)":"var(--accent-red)"}">${escapeHtml(r[k])}</div><div class="test-stat-lbl">${k}</div></div>`).join("");
  }catch(e){document.getElementById("test-steam-result").innerHTML=`<span style="color:var(--accent-red)">${escapeHtml(e.message)}</span>`}
};
window.runSidTest=async()=>{
  const sid=document.getElementById("test-sid-input").value.trim();
  if(!/^\d{17}$/.test(sid)){toast("输入17位SteamID","error");return}
  const rs=document.getElementById("test-sid-result");
  rs.innerHTML='<span style="color:var(--text-muted)">查询中...</span>';
  try{
    const r=await API.get(`/api/test/steamid/${sid}`);
    if(r.from_cache||r.from_api){
      const pl=r.player||r;
      const avatar=safeImageUrl(pl.avatar||r.avatar);
      const name=escapeHtml(pl.name||r.name||sid);
      const game=pl.gameextrainfo||r.game;
      rs.innerHTML=`<div style="display:flex;gap:12px;padding:16px;background:var(--bg-primary);border-radius:var(--border-radius-lg);border:1px solid var(--border-color)"><img src="${avatar}" style="width:56px;height:56px;border-radius:8px;background:var(--bg-tertiary)" onerror="this.style.display='none'"><div><div style="font-size:15px;font-weight:500;color:var(--text-bright)">${name}</div><div style="font-size:12px;color:var(--text-muted)">${escapeHtml(sid)}</div>${game?`<div style="font-size:12px;color:var(--accent-green);margin-top:4px">🎮${escapeHtml(game)}</div>`:""}<div style="font-size:11px;color:var(--text-muted);margin-top:4px">来源:${r.from_cache?"本地缓存":"Steam API"}</div></div></div>`
    }else{
      rs.innerHTML=`<span style="color:var(--accent-red)">${escapeHtml(r.error||"查询失败")}</span>`
    }
  }catch(e){
    rs.innerHTML=`<span style="color:var(--accent-red)">${escapeHtml(e.message)}</span>`
  }
};

function applyBridgeContext(context) {
  document.documentElement.dataset.theme = context?.isDark ? "dark" : "light";
}

async function initPluginPage() {
  if (!bridge) {
    throw new Error("AstrBot Plugin Page bridge is unavailable");
  }
  const context = await bridge.ready();
  applyBridgeContext(context);
  bridge.onContext?.(applyBridgeContext);
  await navigateTo("dashboard");
}

initPluginPage().catch((error) => {
  const content = document.getElementById("content");
  if (content) {
    content.innerHTML = `<div class="empty-state"><span class="mdi mdi-alert-circle"></span><p>页面初始化失败: ${escapeHtml(error.message)}</p></div>`;
  }
});
