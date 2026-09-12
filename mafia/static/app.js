/* app.js v5 — نفس الشاشة للجميع + تعافٍ تلقائي من إعادة تشغيل الخادم */
const screen = document.getElementById("screen");

let pid = sessionStorage.getItem("pid") || null;
let token = sessionStorage.getItem("token") || null;
let state = null, clockOffset = 0, lastPhaseKey = null, selected = null, es = null, esErrors = 0;

/* ---------- صوت واهتزاز موحّد ---------- */
let actx = null;
function audioInit(){ try{ actx = new (window.AudioContext||window.webkitAudioContext)(); }catch(e){} }
function beep(f, d, delay=0, type="sine", vol=.25){
  if(!actx) return;
  const t = actx.currentTime + delay;
  const o = actx.createOscillator(), g = actx.createGain();
  o.type = type; o.frequency.value = f;
  g.gain.setValueAtTime(vol, t);
  g.gain.exponentialRampToValueAtTime(.001, t + d);
  o.connect(g); g.connect(actx.destination);
  o.start(t); o.stop(t + d + .05);
}
function phaseSound(ph){
  if(ph==="DAWN"){ beep(660,.15); beep(880,.25,.18); }
  else if(ph==="GAMEOVER"){ beep(440,.3); beep(330,.45,.3); }
  else if(ph==="REVEAL") beep(520,.25);
  else if(ph==="DISCUSSION") beep(700,.12);
  else if(ph==="VOTING") beep(590,.15);
  else if(ph && ph.startsWith("NIGHT")) beep(220,.35,0,"triangle");
  try{ navigator.vibrate && navigator.vibrate(ph==="GAMEOVER"?[200,100,200]:120); }catch(e){}
}
document.addEventListener("click", ()=>{ if(actx && actx.state==="suspended") actx.resume(); });

async function keepAwake(){
  try{ if("wakeLock" in navigator) await navigator.wakeLock.request("screen"); }catch(e){}
}
document.addEventListener("visibilitychange", ()=>{ if(document.visibilityState==="visible") keepAwake(); });

/* ---------- الشبكة ---------- */
setInterval(()=>{ fetch("/api/ping").catch(()=>{}); }, 60000);

async function post(url, body){
  const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const d = await r.json().catch(()=>({}));
  if(d.error){ alert(d.error); if(state) render(); return null; }
  return d;
}
/* فحص الجلسة: true صالحة | false ميتة | null الشبكة متعثرة (لا نحكم بعد) */
async function checkSession(){
  try{
    const r = await fetch(`/api/whoami?pid=${pid}&token=${token}`);
    return r.ok;
  }catch(e){ return null; }
}
function connect(){
  if(es) es.close();
  esErrors = 0;
  es = new EventSource(`/api/events?pid=${pid}&token=${token}`);
  es.onopen = () => { esErrors = 0; };
  es.onmessage = e => apply(JSON.parse(e.data));
  es.onerror = async () => {
    esErrors++;
    if(es.readyState === EventSource.CLOSED){ sessionStorage.clear(); location.reload(); return; }
    if(esErrors >= 4){                       // إعادة تشغيل خادم أثناء اللعب؟
      const ok = await checkSession();
      if(ok === false){ sessionStorage.clear(); location.reload(); }
    }
  };
}
async function join(){
  const name = document.getElementById("name").value.trim();
  if(!name) return alert("اكتب اسمك");
  audioInit(); keepAwake();
  const d = await post("/api/join",{name});
  if(!d) return;
  pid = d.pid; token = d.token;
  sessionStorage.setItem("pid",pid); sessionStorage.setItem("token",token);
  connect(); apply(d.state);
}
function apply(s){
  state = s;
  clockOffset = s.server_time - Date.now()/1000;
  render();
}

setInterval(()=>{
  document.querySelectorAll("[data-ends]").forEach(el=>{
    el.textContent = Math.max(0, Math.ceil(parseFloat(el.dataset.ends) - (Date.now()/1000 + clockOffset)));
  });
},250);

/* ---------- العرض ---------- */
const topbar = t => `<div class="topbar"><span>${t}</span>
  <span class="timer">⏳ <b data-ends="${state.phase_ends_at}">..</b></span></div>`;
const hostTools = () => state.you.is_host
  ? `<div class="hosttools"><button class="ghost" onclick="hostSkip()">⏭ تخطي الطور (مضيف)</button></div>` : "";
const roleEmoji = r => ({MAFIA:"🔪",DOCTOR:"💉",DETECTIVE:"🕵️",SNIPER:"🎯",INQUIRER:"📜",JAILER:"⛓️",CITIZEN:"👨‍🌾"}[r]||"");
const deadList = () => (state.dead_names||[]).length
  ? `<p class="hint">⛔ خارج اللعبة (لا يتكلم ولا يصوّت): <b>${state.dead_names.join("، ")}</b></p>` : "";

const ROLE_HINT = {
  MAFIA:"كل ليلة اتفقوا سراً على ضحية — الأكثر أصواتاً يُهاجم. تظاهر بالاختيار مع الجميع!",
  DOCTOR:"كل ليلة اختر من تنقذ — إن كان هدف المافيا نفسه نجا",
  DETECTIVE:"كل ليلة تعرف سراً: هل شخصٌ ما مافيا أم لا",
  SNIPER:"طلقة واحدة طوال اللعبة — التأكيد = إطلاق نار فوري",
  JAILER:"كل ليلة اسجن شخصاً: تُعطَّل قدرته ويُحمى من المافيا",
  INQUIRER:"كل ليلتين تقرر: كشف أدوار جميع الأموات للجميع — بدون أسماء",
  CITIZEN:"لا قدرة سحرية — راقب، سجّل شكوكك، وأقنع المجلس",
};

function render(){
  if(!state || !state.you){ renderJoin(); return; }
  const key = state.phase+"|"+state.round+"|"+state.night_index;
  if(key !== lastPhaseKey){
    if(lastPhaseKey) phaseSound(state.phase);
    lastPhaseKey = key; selected = null;
  }
  ({
    LOBBY:renderLobby, REVEAL:renderReveal, DAWN:renderDawn, DISCUSSION:renderDiscussion,
    EXECUTION:renderExecution, GAMEOVER:renderGameOver,
    NIGHT_MAFIA:renderNight, NIGHT_DOCTOR:renderNight, NIGHT_JAILER:renderNight,
    NIGHT_SNIPER:renderNight, NIGHT_DETECTIVE:renderNight, NIGHT_INQUIRER:renderNight,
    VOTING:renderVoting
  }[state.phase] || renderJoin)();
}

function renderJoin(){
  screen.innerHTML = `<div class="card center">
    <h1>🌙 مافيا</h1>
    <p class="hint">الحكم آلة… ولا هاتف يفضح أحداً</p>
    <input id="name" placeholder="اسمك في اللعبة" maxlength="20">
    <button class="primary" onclick="join()">دخول</button></div>`;
}

function renderLobby(){
  const me = state.you;
  let html = `<div class="card"><h2>اللاعبون (${state.players.length}) — الحد الأدنى 4</h2><ul class="plist">`;
  state.players.forEach(p => html += `<li>${p.name}${p.is_host?" 👑":""}</li>`);
  html += `</ul><p class="hint">🔗 الرابط: <b>${location.origin}</b></p>
    <p class="hint">🔪 مافيا يقتل | 💉 دكتور ينقذ | 🕵️ محقق: مافيا أم لا؟<br>
    🎯 قناص: طلقة واحدة | ⛓️ سجان: يسجن ويحمي | 📜 مستعلم: كشف أدوار الأموات كل ليلتين | 👨‍🌾 مواطن يسجّل شكوكه</p>`;
  if(me.is_host){
    html += `<hr><h2>إعدادات المضيف</h2>
      <label>عدد المافيا <input id="s_mafia" type="number" value="1" min="1" max="3"></label>
      <label><input id="s_doc" type="checkbox" checked> الدكتور 💉</label>
      <label><input id="s_det" type="checkbox" checked> المحقق 🕵️</label>
      <label><input id="s_sniper" type="checkbox"> القناص 🎯</label>
      <label><input id="s_inq" type="checkbox"> المستعلم 📜</label>
      <label><input id="s_jail" type="checkbox"> السجان ⛓️</label>
      <label>زمن كل مرحلة ليلية (ثانية) <input id="s_night" type="number" value="15" min="10" max="120"></label>
      <label>زمن المناقشة (ثانية) <input id="s_disc" type="number" value="120" min="30" max="600"></label>
      <label>زمن التصويت (ثانية) <input id="s_vote" type="number" value="30" min="10" max="120"></label>
      <button class="primary" onclick="hostStart()">🚀 ابدأ اللعبة</button>`;
  } else html += `<p class="hint">بانتظار المضيف…</p>`;
  screen.innerHTML = html + `</div>`;
}

async function hostStart(){
  const d = await post("/api/host/start",{token, settings:{
    mafia_count:+document.getElementById("s_mafia").value,
    doctor_enabled:document.getElementById("s_doc").checked,
    detective_enabled:document.getElementById("s_det").checked,
    sniper_enabled:document.getElementById("s_sniper").checked,
    inquirer_enabled:document.getElementById("s_inq").checked,
    jailer_enabled:document.getElementById("s_jail").checked,
    night_turn_seconds:+document.getElementById("s_night").value,
    discussion_seconds:+document.getElementById("s_disc").value,
    voting_seconds:+document.getElementById("s_vote").value,
  }});
  if(d && d.state) apply(d.state);
}

function renderReveal(){
  const y = state.you;
  let extra = "";
  if(y.role==="MAFIA")
    extra = y.partners && y.partners.length
      ? `<p class="hint">شركاؤك: <b>${y.partners.map(p=>p.name).join("، ")}</b> — سيتظاهرون بالاختيار معك كل ليلة!</p>`
      : `<p class="hint">أنت المافيا الوحيد 🤫</p>`;
  screen.innerHTML = `<div class="card center">
    <h2>احفظ دورك… ولا تُظهر هاتفك!</h2>
    <div class="role ${y.role}"><div class="emoji">${roleEmoji(y.role)}</div><div>${y.role_ar}</div></div>
    <p class="hint">${ROLE_HINT[y.role]||""}</p>
    ${extra}
    <p class="hint">الانتقال تلقائي بعد <b class="timer" data-ends="${state.phase_ends_at}">..</b> ثانية</p></div>`;
}

/* ---------- بطاقات خاصة ---------- */
function suspicionCard(){
  const s = state.you.suspicions || [];
  if(!s.length) return "";
  return `<div class="card"><h2>👁 شكوكك المسجلة</h2>` +
    s.map(x=>`<div class="tally"><span>${x.name}</span><b>×${x.count}</b></div>`).join("") + `</div>`;
}
function revealCardHTML(reveal, label){
  if(!reveal) return "";
  const rows = reveal.map(x=>`<div class="tally"><span>${x.role_ar}</span><b>×${x.count}</b></div>`).join("");
  return `<div class="card"><h2>📜 كشف المستعلم ${label||""} (بدون أسماء)</h2>${rows || `<p class="hint">لا أموات حتى الآن</p>`}</div>`;
}
function nightExtras(){
  const y = state.you; let h = "";
  if(y.role==="MAFIA" && state.phase==="NIGHT_MAFIA"){
    const pl = y.partners_live || [];
    h += `<div class="card"><h2>🔪 شركاؤك في المافيا</h2>`;
    if(!pl.length) h += `<p class="hint">أنت المافيا الوحيد</p>`;
    pl.forEach(p=> h += `<div class="tally"><span>${p.name}</span>
      <b>${p.confirmed ? ("→ "+p.target+" ✔") : "لم يؤكد بعد"}</b></div>`);
    h += `<p class="hint">الهدف الأكثر تصويتاً هو من سيُهاجم — اتفقوا قبل التأكيد</p></div>`;
  }
  if(y.role==="SNIPER" && state.phase==="NIGHT_SNIPER")
    h += `<div class="card center">${y.shot_used
      ? `<p class="hint">😞 نفدت طلقتك — أصبحت كالمواطن (اختياراتك تُسجّل كشكوك)</p>`
      : `<p class="hint">🎯 لديك طلقة واحدة فقط — <b>التأكيد يعني الإطلاق</b>، وعدم الاختيار يعني حفظها</p>`}</div>`;
  if(y.role==="JAILER" && state.phase==="NIGHT_JAILER")
    h += `<div class="card center"><p class="hint">⛓️ المسجون تُحجب قدرته الليلة ويُحمى من المافيا. لا تسجن نفسك ولا تكرر نفس الشخص ليلتين</p></div>`;
  if(y.role==="INQUIRER" && state.phase==="NIGHT_INQUIRER" && y.alive){
    if(y.inquirer_tonight){
      if(y.confirmed){
        const d = y.inquirer_decision;
        h += `<div class="card center">
          <p class="hint">📜 <b>ليلة الكشف!</b> هل توافق على كشف أدوار جميع الأموات للجميع (بدون أسماء)؟</p>
          ${d===true?`<div class="safe">📜 قرارك الحالي: موافقة ✔</div>`
                   : d===false?`<div class="safe">📜 قرارك الحالي: رفض ✔</div>`
                   : `<p class="hint">لم تقرر بعد — بدون قرار = رفض</p>`}
          <button class="primary" onclick="inqDecide(true)">✅ موافقة على الكشف</button>
          <button class="ghost" style="width:100%;margin-top:6px" onclick="inqDecide(false)">❌ رفض الكشف</button>
        </div>`;
      } else {
        h += `<div class="card center"><p class="hint">📜 <b>ليلة الكشف!</b> اختر اسماً تمويهياً وأكّد كالبقية… وبعدها يظهر لك زر القرار السري</p></div>`;
      }
    } else {
      const nxt = (state.round % 2 === 0) ? state.round + 2 : state.round + 1;
      h += `<div class="card center"><p class="hint">📜 ليلة راحة — قدرتك تتفعّل في الليلة ${nxt}. اختيارك يُسجَّل كشك شخصي.</p></div>`;
    }
  }
  return h;
}

/* ---------- شبكة الاختيار الموحّدة ---------- */
function pickGridHTML(subtitle){
  const y = state.you;
  let html = "";
  if(!y.alive) html += `<div class="deadbadge">☠️ أنت خارج اللعبة — تمثّل أنك تلعب كالبقية!</div>`;
  if(!y.confirmed){
    html += `<p class="hint">${subtitle}</p><div class="grid">`;
    state.players.filter(p=>p.alive).forEach(p=>{
      html += `<button class="cell ${selected===p.id?"sel":""}" onclick="pick('${p.id}')">${p.name}</button>`;
    });
    html += `</div><button class="primary" ${selected?"":"disabled"} onclick="confirmPick()">تأكيد ✔</button>`;
  } else {
    html += `<div class="card center"><p>✅ تم — بانتظار البقية…</p></div>`;
    if((y.private_results||[]).some(r=>r.id===y.pick))
      html += `<div class="card center">
        <button class="ghost" id="peekBtn">🔍 اضغط مطوّلاً (ثانية) لرؤية النتيجة</button>
        <div id="peek" class="peek hidden"></div></div>`;
    html += suspicionCard();
  }
  return html;
}
function renderNight(){
  screen.innerHTML =
    topbar(`🌙 الليل ${state.round} — المرحلة ${state.night_index} من ${state.night_total}`) +
    `<div class="card">` + pickGridHTML("اختر اسماً ثم اضغط تأكيد") + `</div>` +
    nightExtras() + hostTools();
  wirePeek();
}
function renderVoting(){
  screen.innerHTML = topbar("🗳️ التصويت") +
    `<div class="card">` + pickGridHTML("صوّت سراً: من نُعدم اليوم؟") + `</div>` + hostTools();
  wirePeek();
}

async function pick(id){
  selected = id;
  const d = await post("/api/pick",{token, target_id:id});
  if(d && d.state) apply(d.state);
}
async function confirmPick(){
  const d = await post("/api/pick",{token, confirm:true});
  if(d && d.state) apply(d.state);
}
async function inqDecide(approve){
  const d = await post("/api/inquirer/decide",{token, approve});
  if(d && d.state) apply(d.state);
}

/* نتيجة المحقق: ضغط مطول — على هاتف صاحبها فقط */
function wirePeek(){
  const b = document.getElementById("peekBtn");
  if(!b) return;
  const box = document.getElementById("peek");
  const y = state.you;
  const r = (y.private_results||[]).find(r=>r.id===y.pick);
  let t = null;
  const show = ()=>{
    box.textContent = r ? `${r.name}: ${r.text}` : "لا نتيجة";
    box.className = "peek " + (r ? r.tone : "");
    box.classList.remove("hidden"); b.classList.add("hidden");
  };
  const start = e => { e.preventDefault(); t = setTimeout(show, 1000); };
  const cancel = () => { clearTimeout(t); t = null; };
  b.addEventListener("touchstart", start, {passive:false});
  b.addEventListener("touchend", cancel);
  b.addEventListener("mousedown", start);
  b.addEventListener("mouseup", cancel);
  b.addEventListener("contextmenu", e=>e.preventDefault());
}

function renderDawn(){
  const pub = state.public || {};
  let body = (pub.deaths && pub.deaths.length)
    ? pub.deaths.map(d=>`<div class="death">☠️ قُتل <b>${d.name}</b></div>`).join("")
    : `<div class="safe">☀️ لم يمت أحد الليلة!</div>`;
  screen.innerHTML = topbar("🌅 الفجر") +
    `<div class="card center">${body}${deadList()}<p class="hint">تبدأ المناقشة تلقائياً…</p></div>` +
    revealCardHTML(pub.inquirer_reveal);
}

function renderDiscussion(){
  const hist = (state.reveals||[]).map(r=>revealCardHTML(r.roles, `— ليلة ${r.round}`)).join("");
  screen.innerHTML = topbar("💬 المناقشة") +
    `<div class="card center"><h2>ناقشوا! من هو المافيا؟ 🤔</h2>${deadList()}
     <p class="hint">لا أحد يعرف من كان يختار فعلاً بالليل — حتى الموتى كانوا يضغطون!</p></div>` +
    hist + suspicionCard() + hostTools();
}

function renderExecution(){
  const pub = state.public || {};
  const tallies = (pub.tallies||[]).map(x=>
    `<div class="tally"><span>${x.name}</span><b>${x.count} صوت</b></div>`).join("");
  const res = pub.executed
    ? `<div class="death">⚖️ أُعدم <b>${pub.executed.name}</b></div>`
    : `<div class="safe">⚖️ ${pub.tie ? "تعادل الأصوات — لم يُعدم أحد" : "لم تُصوّت لأحد"}</div>`;
  screen.innerHTML = topbar("⚖️ نتيجة التصويت") +
    `<div class="card center">${res}${deadList()}<hr>${tallies}</div>` + hostTools();
}

function renderGameOver(){
  const win = state.winner === "MAFIA";
  const rows = (state.reveal_all||[]).map(p=>
    `<div class="tally"><span>${p.alive?"🙂":"☠️"} ${p.name}</span><b>${p.role_ar}</b></div>`).join("");
  screen.innerHTML = `<div class="card center">
    <h1>${win ? "🔪 فوز المافيا!" : "🎉 فوز المواطنين!"}</h1><hr>${rows}
    ${state.you.is_host
      ? `<button class="primary" onclick="hostRestart()">🔄 لعبة جديدة (نفس اللاعبين)</button>`
      : `<p class="hint">بانتظار المضيف لبدء لعبة جديدة…</p>`}</div>`;
}

async function hostSkip(){ const d = await post("/api/host/skip",{token}); if(d && d.state) apply(d.state); }
async function hostRestart(){ const d = await post("/api/host/restart",{token}); if(d && d.state) apply(d.state); }

/* ---------- الإقلاع: افحص الجلسة أولاً — لا صفحات بيضاء بعد اليوم ---------- */
window.addEventListener("load", async ()=>{
  if(pid && token){
    audioInit(); keepAwake();
    renderJoin();                       // اعرض شاشة الدخول مؤقتاً بدل الصفحة البيضاء
    const ok = await checkSession();
    if(ok === false){                   // جلسة ميتة (الخادم أعيد تشغيله) → نظّف وابدأ من جديد
      sessionStorage.clear(); pid = token = null; state = null;
      renderJoin(); return;
    }
    connect();                          // صالحة (أو الشبكة متعثرة) → أكمل الاتصال
  } else renderJoin();
});