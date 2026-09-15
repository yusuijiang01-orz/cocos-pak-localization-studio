let records=[], project=null, selected=null;
let recordById=new Map();
let headerIndex=new Map();
let filters={language:'all',pak:'all',filetype:'all',status:'all',file:'all',scope:'all'};
let pendingPaks=[], pendingWorkspace=null;
let currentPage=1;
const PAGE_SIZE=200;
let searchTimer=null,liveSequence=0,activeUpcoming=[];
let workflowRunning=false,workflowCancelling=false;
let reviewRunning=false,reviewPausing=false,reviewCanResume=false;
let reviewedSelection=new Set(),visibleReviewedIds=[],reviewForceIds=[];
const expandedFileTypes=new Set(['TSV']);
const $=s=>document.querySelector(s);
const esc=s=>String(s??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

let appDialogResolve=null;
function dialogTone(message){const s=String(message||'').replace(/(?:失败(?:文件|桶|\/拒收)?|错误|异常)\s*[：:]\s*0(?:\s*(?:条|个|次))?/g,'');if(/失败|错误|异常|无法|禁止|损坏|超时|中断/.test(s))return 'error';if(/完成|成功|已导出|已生成|已通过/.test(s))return 'success';return 'info'}
function closeAppDialog(result){const modal=$('#appDialog');if(!modal||modal.classList.contains('hidden'))return;modal.classList.add('hidden');const resolve=appDialogResolve;appDialogResolve=null;if(resolve)resolve(result)}
function showAppDialog(message,{title,confirmText='确定',cancelText='',actionText='',tone}={}){
  if(appDialogResolve)closeAppDialog(false);
  const value=String(message??'');const resolvedTone=tone||dialogTone(value);const modal=$('#appDialog'),card=modal.querySelector('.dialog-card');
  card.dataset.tone=resolvedTone;$('#appDialogTitle').textContent=title||(resolvedTone==='error'?'操作失败':resolvedTone==='success'?'操作完成':'提示');$('#appDialogMessage').textContent=value;
  const action=$('#appDialogAction'),cancel=$('#appDialogCancel'),confirm=$('#appDialogConfirm');action.textContent=actionText||'一键恢复全部';action.classList.toggle('hidden',!actionText);action.disabled=false;cancel.textContent=cancelText||'取消';cancel.classList.toggle('hidden',!cancelText);confirm.textContent=confirmText;
  modal.classList.remove('hidden');setTimeout(()=>confirm.focus(),0);
  return new Promise(resolve=>{appDialogResolve=resolve});
}
function alert(message,options){return showAppDialog(message,options)}
function appConfirm(message,{title='确认操作',confirmText='继续',tone='warning'}={}){return showAppDialog(message,{title,confirmText,cancelText:'取消',tone})}
$('#appDialogConfirm').onclick=()=>closeAppDialog(true);
$('#appDialogCancel').onclick=()=>closeAppDialog(false);
$('#appDialogAction').onclick=()=>closeAppDialog('action');
$('#appDialog').addEventListener('mousedown',e=>{if(e.target===$('#appDialog')&&!$('#appDialogCancel').classList.contains('hidden'))closeAppDialog(false)});
document.addEventListener('keydown',e=>{if(e.key==='Escape'&&!$('#appDialog').classList.contains('hidden'))closeAppDialog(false)});

function headerKey(pak,file,column){return `${pak||''}\u0000${file||''}\u0000${Number(column)||1}`}
function rebuildRecordIndexes(){
  recordById=new Map();
  headerIndex=new Map();
  for(const r of records){
    if(r.source_original===undefined)r.source_original=r.original;
    r._fileType=computeFileType(r);
    r.language=detectLiveLanguage(r.original);
    recordById.set(r.id,r);
    if(r._fileType==='TSV'&&Number(r.line)===1)headerIndex.set(headerKey(r.pak,r.source_file,r.column),String(r.original||r.source_original||'').trim());
  }
  // text_records.json is already the analyzer's structure-aware allowlist.
  // Legacy projects predate the persisted marker, so treat those records as
  // visible instead of trying to infer a TSV header from a data-row context.
  for(const r of records)if(r._isPlayerVisible===undefined)r._isPlayerVisible=true;
}
function computeFileType(r){const m=String(r.source_file||'').match(/\.([^.]+)$/);return m?m[1].toUpperCase():'未知'}
function fileTypeOf(r){return r._fileType||computeFileType(r)}
function statusOf(r){if(r.review_status==='有风险'||r.review_status==='失败')return '有风险';if(r.review_status==='通过')return '已审核';return r.source_original!==undefined&&r.source_original!==null?((r.original||'')!==r.source_original?'已翻译':'未翻译'):(r.status||'未翻译')}
function countsBy(key){const m={};for(const r of records){const v=key==='filetype'?fileTypeOf(r):(key==='status'?statusOf(r):r[key]);m[v]=(m[v]||0)+1}return m}
function textLooksTranslated(r){return r.language==='zh'}
function headerNameOf(r){
  if(fileTypeOf(r)==='TSV'){
    const header=headerIndex.get(headerKey(r.pak,r.source_file,r.column));
    if(header)return header;
  }
  if(fileTypeOf(r)!=='TSV')return String(r.key||'');
  const line=String(r.context||'').split(/\r?\n/)[0]||'';
  if(!line)return String(r.key||'');
  const cells=line.split('\t');
  const idx=(Number(r.column)||1)-1;
  return String(cells[idx]||r.key||'').trim();
}
const VISIBLE_HEADER_RE=/(^|[_\s-])(name|title|intro|desc|description|text|content|message|msg|tip|caption|dialog|talk|say|quest|task|mission|skill|npc|item|weapon|equip|mapname|dropname|notice|label|help|story)([_\s-]|$)|名称|名字|标题|说明|描述|简介|内容|文本|对白|对话|提示|消息|任务|技能名|物品名|装备名|地图名|备注/i;
const INTERNAL_HEADER_RE=/(image|img|icon|spr|sprite|file|path|script|sound|music|texture|font|res|objid|id$|genre|type|kind|width|height|price|count|weight|trade|time|rate|param|frame|radius|speed|damage|defense|resist|color|lum|attrib|level|drop$|pk|ai|barrier|stun|confuse|freeze|timer|clientonly)/i;
const VISIBLE_KEY_RE=/(name|title|text|desc|description|message|msg|tip|dialog|talk|quest|task|mission|skill|item|weapon|equip|npc|notice|label|help|success|fail|error|warning|info)/i;
const INTERNAL_KEY_RE=/(image|img|icon|spr|file|path|script|sound|music|texture|font|res|id$|rate|param|frame|speed|color|stone|stunid|level|time|value|num|count|price|width|height)/i;
const UNSAFE_RUNTIME_TEXT_RE=/^\s*(?:=|@(?:media|keyframes|font-face|supports|import|charset)\b|@=.*@=)|^\s*(?:align-content|align-items|align-self|background(?:-[\w-]+)?|border(?:-[\w-]+)?|bottom|clear|color|column(?:-[\w-]+)?|display|float|font(?:-[\w-]+)?|height|left|line-height|margin(?:-[\w-]+)?|max-(?:width|height)|min-(?:width|height)|opacity|overflow(?:-[\w-]+)?|padding(?:-[\w-]+)?|position|right|text-align|text-decoration|text-shadow|top|transform|vertical-align|visibility|white-space|width|z-index)\s*:/i;
function isUnsafeRuntimeText(text){return UNSAFE_RUNTIME_TEXT_RE.test(String(text||'').trim())}
function isTsvCommonVisibleColumn(r){
  const c=Number(r.column)||1;
  const text=String(r.original||r.source_original||'');
  if(Number(r.line)===1)return false;
  if(!/[A-Za-z\u00c0-\u1ef9\u3400-\u9fff]/.test(text))return false;
  return c===1||c===2||c===5||c===9||c===11||c===12||c===15||c===19||c===26||c===28||c===30||c===31||c===32||c===88;
}
function computePlayerVisibleRecord(r){
  const type=fileTypeOf(r);
  const text=String(r.original||r.source_original||'').trim();
  if(!text)return false;
  if(type==='TSV'){
    const header=headerNameOf(r);
    if(Number(r.line)===1)return false;
    if(INTERNAL_HEADER_RE.test(header)&&!VISIBLE_HEADER_RE.test(header))return false;
    if(VISIBLE_HEADER_RE.test(header))return true;
    return false;
  }
  if(type==='INI'){
    const key=String(r.key||'');
    if(INTERNAL_KEY_RE.test(key)&&!VISIBLE_KEY_RE.test(key))return false;
    if(VISIBLE_KEY_RE.test(key))return true;
    return /<color=|<c=|[\u00c0-\u1ef9\u3400-\u9fff]/i.test(text)&&!/^[\\\/.\w:-]+$/i.test(text);
  }
  if(type==='TXT'){
    return /[\u00c0-\u1ef9\u3400-\u9fff]/.test(text)&&!/^[\\\/.\w:-]+$/i.test(text);
  }
  if(type==='LUA')return /[A-Za-z\u00c0-\u1ef9\u3400-\u9fff]/.test(text)&&!/^[\\\/\.\w:-]+$/i.test(text);
  return false;
}
function isPlayerVisibleRecord(r){
  // Enforce the current safety policy even for project caches produced by an
  // older Studio version. Backend export/import/build applies the same rule.
  if(isUnsafeRuntimeText(r.source_original??r.original))return false;
  return r._isPlayerVisible!==undefined?r._isPlayerVisible:computePlayerVisibleRecord(r)
}
function buildFilter(el, values, key, labels={}){
  el.innerHTML='';
  const total=document.createElement('button'); total.className=`filter-btn${filters[key]==='all'?' active':''}`; total.dataset.v='all'; total.innerHTML=`<span>全部</span><span class="count">${records.length}</span>`; el.appendChild(total);
  for(const [v,n] of Object.entries(values).sort((a,b)=>b[1]-a[1])){const b=document.createElement('button');b.className=`filter-btn${filters[key]===v?' active':''}`;b.dataset.v=v;b.innerHTML=`<span>${esc(labels[v]||v)}</span><span class="count">${n}</span>`;el.appendChild(b)}
  el.onclick=e=>{const b=e.target.closest('.filter-btn');if(!b)return;filters[key]=b.dataset.v;if(key==='pak'||key==='filetype')filters.file='all';if(key==='filetype'&&filters.filetype!=='all')expandedFileTypes.add(filters.filetype);currentPage=1;[...el.children].forEach(x=>x.classList.toggle('active',x===b));renderFileCoverage();renderRows()}
}
function renderScopeFilter(){
  const el=$('#scopeList');if(!el)return;
  let visible=0;for(const r of records)if(isPlayerVisibleRecord(r))visible++;
  const values={all:records.length,visible,internal:Math.max(0,records.length-visible)};
  const labels={all:'全部',visible:'玩家可见',internal:'内部字段'};
  el.innerHTML='';
  for(const key of ['all','visible','internal']){
    const b=document.createElement('button');
    b.className=`filter-btn${filters.scope===key?' active':''}`;
    b.dataset.v=key;
    b.innerHTML=`<span>${labels[key]}</span><span class="count">${values[key].toLocaleString()}</span>`;
    el.appendChild(b);
  }
  el.onclick=e=>{const b=e.target.closest('.filter-btn');if(!b)return;filters.scope=b.dataset.v;filters.file='all';currentPage=1;[...el.children].forEach(x=>x.classList.toggle('active',x===b));renderFileCoverage();renderRows()};
}
function renderFilters(){
  buildFilter($('#pakList'),countsBy('pak'),'pak');
  buildFilter($('#typeList'),countsBy('filetype'),'filetype');
  renderScopeFilter();
  renderFileCoverage();
  buildFilter($('#langList'),countsBy('language'),'language',{vi:'越南文',zh:'中文',mixed:'中越混合',other:'其他'});
  buildFilter($('#statusList'),countsBy('status'),'status');
}
function fileCoverageRows(){
  const byFile=new Map();
  for(const r of records){
    const type=fileTypeOf(r);
    const visible=isPlayerVisibleRecord(r);
    if(
      (filters.pak!=='all'&&r.pak!==filters.pak)
      || !['TSV','INI','TXT','LUA'].includes(type)
      || (filters.filetype!=='all'&&type!==filters.filetype)
      || (filters.scope!=='all'&&(filters.scope==='visible'?!visible:visible))
    )continue;
    const name=String(r.source_file||'');
    if(!name)continue;
    const item=byFile.get(name)||{file:name,type,total:0,zh:0,mixed:0,vi:0,other:0,visibleTotal:0,visibleDone:0};
    item.total++;
    if(visible){
      item.visibleTotal++;
      if(textLooksTranslated(r))item.visibleDone++;
    }
    if(r.language==='zh')item.zh++;
    else if(r.language==='mixed')item.mixed++;
    else if(r.language==='vi')item.vi++;
    else item.other++;
    byFile.set(name,item);
  }
  return [...byFile.values()].map(item=>({
    ...item,
    percent:item.total?Math.round(item.zh/item.total*1000)/10:0,
    visiblePercent:item.visibleTotal?Math.round(item.visibleDone/item.visibleTotal*1000)/10:null,
  })).sort((a,b)=>((a.visiblePercent??a.percent)-(b.visiblePercent??b.percent))||b.visibleTotal-a.visibleTotal||b.total-a.total||a.file.localeCompare(b.file));
}
function renderFileCoverage(){
  const el=$('#fileCoverageList');if(!el)return;
  const rows=fileCoverageRows();
  const grouped={TSV:[],INI:[],TXT:[],LUA:[]};
  for(const row of rows)grouped[row.type]?.push(row);
  const activeFile=filters.file||'all';
  const total=rows.reduce((s,x)=>s+x.total,0);
  const zh=rows.reduce((s,x)=>s+x.zh,0);
  const visibleTotal=rows.reduce((s,x)=>s+x.visibleTotal,0);
  const visibleDone=rows.reduce((s,x)=>s+x.visibleDone,0);
  const allPercent=total?Math.round(zh/total*1000)/10:0;
  const visibleAllPercent=visibleTotal?Math.round(visibleDone/visibleTotal*1000)/10:0;
  let html=`<button class="file-clear${activeFile==='all'?' active':''}" data-file="all"><span>全部文件</span><span title="玩家可见 / 全量">${visibleAllPercent}% / ${allPercent}%</span></button>`;
  for(const type of ['TSV','INI','TXT','LUA']){
    const items=grouped[type],open=expandedFileTypes.has(type);
    html+=`<button class="file-group" data-type="${type}"><span>${open?'▾':'▸'} ${type}</span><span>${items.length}</span></button>`;
    if(!open)continue;
    if(!items.length){html+=`<div class="file-empty">无文件</div>`;continue}
    for(const item of items){
      const pct=Math.max(0,Math.min(100,item.percent));
      const visiblePct=item.visiblePercent===null?null:Math.max(0,Math.min(100,item.visiblePercent));
      const mainPct=visiblePct===null?pct:visiblePct;
      const visibleLabel=visiblePct===null?'无可见字段':`可见 ${item.visibleDone.toLocaleString()} / ${item.visibleTotal.toLocaleString()}`;
      html+=`<button class="file-coverage${activeFile===item.file?' active':''}" data-file="${esc(item.file)}" title="${esc(item.file)}">
        <span class="file-name mono">${esc(item.file)}</span>
        <span class="file-rate">${mainPct.toFixed(1)}%</span>
        <span class="file-count">${esc(visibleLabel)} · 全量 ${item.zh.toLocaleString()} / ${item.total.toLocaleString()}</span>
        <span class="file-bar"><i style="width:${mainPct}%"></i></span>
      </button>`;
    }
  }
  el.innerHTML=html;
  el.onclick=e=>{
    const group=e.target.closest('.file-group');
    if(group){const type=group.dataset.type;if(expandedFileTypes.has(type))expandedFileTypes.delete(type);else expandedFileTypes.add(type);renderFileCoverage();return}
    const fileBtn=e.target.closest('[data-file]');
    if(!fileBtn)return;
    filters.file=fileBtn.dataset.file||'all';
    currentPage=1;
    renderFileCoverage();
    renderRows();
  };
}
function displayText(r){return r.original??''}
function updateReviewSelectionControls(){const count=reviewedSelection.size,btn=$('#reReviewSelected'),all=$('#selectReviewedPage');if(btn){btn.textContent=count?`重新审核 (${count})`:'重新审核';btn.disabled=reviewRunning||count===0}if(all){const selectedOnPage=visibleReviewedIds.filter(id=>reviewedSelection.has(id)).length;all.checked=visibleReviewedIds.length>0&&selectedOnPage===visibleReviewedIds.length;all.indeterminate=selectedOnPage>0&&selectedOnPage<visibleReviewedIds.length;all.disabled=visibleReviewedIds.length===0}}
function filtered(){
  const q=$('#search').value.trim().toLowerCase();
  const result=records.filter(r=>(filters.language==='all'||r.language===filters.language)&&(filters.pak==='all'||r.pak===filters.pak)&&(filters.filetype==='all'||fileTypeOf(r)===filters.filetype)&&(filters.status==='all'||statusOf(r)===filters.status)&&(filters.file==='all'||r.source_file===filters.file)&&(filters.scope==='all'||(filters.scope==='visible'?isPlayerVisibleRecord(r):!isPlayerVisibleRecord(r)))&&(!q||[r.id,r.source_file,r.line,displayText(r)].join(' ').toLowerCase().includes(q)));
  if(activeUpcoming.length)result.sort((a,b)=>(a._upcomingRank??Number.MAX_SAFE_INTEGER)-(b._upcomingRank??Number.MAX_SAFE_INTEGER)||(b._activitySeq||0)-(a._activitySeq||0));
  else if(filters.language==='vi')result.sort((a,b)=>(a._upcomingRank??Number.MAX_SAFE_INTEGER)-(b._upcomingRank??Number.MAX_SAFE_INTEGER)||(b._activitySeq||0)-(a._activitySeq||0));
  else if(filters.language==='zh')result.sort((a,b)=>(b._translatedSeq||0)-(a._translatedSeq||0));
  return result
}
function renderRows(){
  const xs=filtered();
  const pages=Math.max(1,Math.ceil(xs.length/PAGE_SIZE)); if(currentPage>pages)currentPage=pages;
  const start=(currentPage-1)*PAGE_SIZE, shown=xs.slice(start,start+PAGE_SIZE);
  visibleReviewedIds=shown.filter(r=>statusOf(r)==='已审核').map(r=>String(r.id));
  const pakLabel=filters.pak==='all'?'全部 PAK':filters.pak;
  const fileLabel=filters.file==='all'?'':` · ${filters.file}`;
  $('#summary').textContent=`${pakLabel}${fileLabel} · 结果 ${xs.length.toLocaleString()} / ${records.length.toLocaleString()} 条 · 第 ${currentPage}/${pages} 页`;
  $('#pageInfo').textContent=`${currentPage} / ${pages}`;
  $('#prevPage').disabled=currentPage<=1; $('#nextPage').disabled=currentPage>=pages;
  const tbody=$('#rows'); const frag=document.createDocumentFragment(); tbody.innerHTML='';
  for(const r of shown){
    const tr=document.createElement('tr'); tr.dataset.id=r.id; if(selected&&selected.id===r.id)tr.classList.add('selected');
    const canReview=statusOf(r)==='已审核';
    tr.innerHTML=`<td><input class="row-review-select" type="checkbox" aria-label="选择重新审核" ${reviewedSelection.has(String(r.id))?'checked':''} ${canReview?'':'disabled'} /></td><td><div class="cell-text mono" title="${esc(r.source_file||'')}">${esc(r.source_file||'')}</div></td><td class="mono">${esc(`L${r.line||''}${r.column?`:C${r.column}`:''}`)}</td><td><div class="cell-text current-text ${r.language==='vi'?'lang-vi':r.language==='zh'?'lang-zh':r.language==='mixed'?'lang-mixed':'lang-other'}" title="${esc(displayText(r))}">${esc(displayText(r))}</div></td><td>${esc({vi:'越南文',zh:'中文',mixed:'混合',other:'其他'}[r.language]||r.language||'')}</td><td>${esc(statusOf(r))}</td>`;
    frag.appendChild(tr);
  }
  tbody.appendChild(frag);
  updateReviewSelectionControls();
}
function setSelectedRow(id){for(const tr of $('#rows').querySelectorAll('tr.selected'))tr.classList.remove('selected');const tr=$(`#rows tr[data-id="${CSS.escape(id)}"]`);if(tr)tr.classList.add('selected')}
function showDetail(r){
  selected=r; setSelectedRow(r.id);
  $('#detailEmpty').classList.add('hidden');$('#detailBody').classList.remove('hidden');
  $('#originalText').value=r.source_original??r.original;$('#translationText').value=r.original||'';$('#restoreOriginal').disabled=String(r.original??'')===String(r.source_original??r.original??'');$('#contextText').textContent=r.context||'';
  const meta=[['ID',r.id],['PAK',r.pak],['文件类型',fileTypeOf(r)],['内容范围',isPlayerVisibleRecord(r)?'玩家可见':'内部/资源'],['语言',r.language],['状态',statusOf(r)],['来源文件',r.source_file],['位置',`Line ${r.line}${r.column?`, Column ${r.column}`:''}`]];
  $('#metaGrid').innerHTML=meta.map(([k,v])=>`<div class="k">${esc(k)}</div><div>${esc(v)}</div>`).join('');
}
$('#rows').onclick=e=>{const tr=e.target.closest('tr');if(!tr)return;const id=String(tr.dataset.id);if(e.target.classList.contains('row-review-select')){if(e.target.checked)reviewedSelection.add(id);else reviewedSelection.delete(id);updateReviewSelectionControls();return}const r=recordById.get(id);if(r)showDetail(r)};
$('#selectReviewedPage').onchange=e=>{for(const id of visibleReviewedIds){if(e.target.checked)reviewedSelection.add(id);else reviewedSelection.delete(id)}renderRows()};
let persistTimer=null;
let recordsDirty=false;
$('#translationText').addEventListener('input',e=>{if(selected){if(selected.source_original===undefined)selected.source_original=selected.original;selected.original=e.target.value;selected.language=detectLiveLanguage(selected.original);selected.status=selected.original!==selected.source_original?'已翻译':'未翻译';recordsDirty=true;delete selected.review_status;delete selected.review_signature;delete selected.reviewed_at;reviewedSelection.delete(String(selected.id));const tr=$(`#rows tr[data-id="${CSS.escape(selected.id)}"]`);if(tr){const td=tr.querySelector('.current-text');if(td){td.textContent=selected.original;td.title=selected.original}}scheduleLiveRefresh()}});
$('#translationText').addEventListener('change',async()=>{if(!recordsDirty||!selected)return;recordsDirty=false;const res=await window.studio.persistRecordEdit({workspace:project?.workspace,record:selected});if(!res?.ok){recordsDirty=true;alert(res?.error||'保存当前编辑失败');return}$('#progress').textContent='当前文本已快速保存；构建时自动同步资源'});
$('#restoreOriginal').onclick=async()=>{if(!selected)return;const source=String(selected.source_original??selected.original??'');const input=$('#translationText');input.value=source;input.dispatchEvent(new Event('input',{bubbles:true}));clearTimeout(persistTimer);persistTimer=null;const btn=$('#restoreOriginal');btn.disabled=true;$('#progress').textContent='正在恢复原文并同步到实际资源目录…';const res=await persistRecords();if(!res?.ok){btn.disabled=false;alert(res?.error||'恢复原文失败');return}showDetail(selected)};
$('#search').addEventListener('input',()=>{clearTimeout(searchTimer);searchTimer=setTimeout(()=>{currentPage=1;renderRows()},220)});
$('#prevPage').onclick=()=>{if(currentPage>1){currentPage--;renderRows();$('#tableWrap').scrollTop=0}};
$('#nextPage').onclick=()=>{currentPage++;renderRows();$('#tableWrap').scrollTop=0};

async function persistRecords(){if(!project)return {ok:false,error:'未打开项目'};const pak=selected?.pak||(filters.pak!=='all'?filters.pak:null);const res=await window.studio.persistRecords({workspace:project.workspace,records,project,pak});if(res&&!res.ok)$('#progress').textContent=`保存或同步失败：${res.error||'未知错误'}`;else if(res?.sync){const skipped=Number(res.sync.report?.skipped_count||0);$('#progress').textContent=skipped?`界面内容已保存；${skipped} 条因保护标记不完整未同步到 ${res.sync.outputDir}`:`界面内容已同步到实际构建目录：${res.sync.outputDir}`}return res}
function tokenDifference(left,right){const counts=new Map();for(const token of right||[])counts.set(token,(counts.get(token)||0)+1);const missing=[];for(const token of left||[]){const count=counts.get(token)||0;if(count)counts.set(token,count-1);else missing.push(token)}return missing}
function syncFailureDetails(report){const skipped=report?.skipped||[];return skipped.slice(0,20).map((entry,index)=>{const record=recordById.get(String(entry.id||''));const location=record?`L${record.line||'?'}:C${record.column||1}`:'位置未知';const missing=tokenDifference(entry.source_tokens,entry.target_tokens);const extra=tokenDifference(entry.target_tokens,entry.source_tokens);const tokenInfo=[missing.length?`缺少 ${missing.join(' ')}`:'',extra.length?`多出 ${extra.join(' ')}`:''].filter(Boolean).join('；');return `${index+1}. ${entry.file||record?.source_file||'未知文件'}  ${location}\nID：${entry.id||record?.id||'未知'}\n原因：${entry.reason||'格式保护校验失败'}${tokenInfo?`（${tokenInfo}）`:''}`}).join('\n\n')}
function focusSyncFailure(report){const first=(report?.skipped||[])[0];if(!first)return;const record=recordById.get(String(first.id||''));if(!record)return;filters.pak=record.pak||filters.pak;filters.file='all';filters.language='all';filters.status='all';filters.scope='all';$('#search').value=String(record.id);currentPage=1;renderFilters();renderRows();showDetail(record)}
async function restoreSyncFailureBatch(report){
  const detailIds=(report?.skipped||[]).map(entry=>String(entry.id||''));
  const ids=[...new Set([...(report?.skipped_ids||[]).map(String),...detailIds].filter(Boolean))];
  let restored=0,missing=0;
  for(const id of ids){
    const record=recordById.get(id);
    if(!record||record.source_original===undefined){missing++;continue}
    record.original=String(record.source_original??'');record.language=detectLiveLanguage(record.original);record.status='未翻译';delete record.review_status;delete record.review_signature;delete record.reviewed_at;reviewedSelection.delete(id);restored++;
  }
  if(!restored)return {ok:false,error:`没有找到可恢复的错误记录${missing?`（${missing} 条记录已失效）`:''}`};
  recordsDirty=true;$('#progress').textContent=`正在一键恢复并同步 ${restored} 条格式错误文本…`;
  const result=await persistRecords();
  if(!result?.ok)return result;
  rebuildRecordIndexes();renderFilters();currentPage=1;renderRows();
  if(selected){selected=recordById.get(String(selected.id))||null;if(selected)showDetail(selected)}
  return {ok:true,restored,missing,remaining:Number(result.sync?.report?.skipped_count||0),report:result.sync?.report||null};
}
async function restoreSyncFailures(initialReport){
  let report=initialReport,totalRestored=0,totalMissing=0,rounds=0;
  while(Number(report?.skipped_count||0)>0&&rounds<100){
    rounds++;
    const before=Number(report?.skipped_count||0);
    $('#progress').textContent=`正在自动恢复：第 ${rounds} 轮，剩余 ${before} 条格式错误…`;
    const result=await restoreSyncFailureBatch(report);
    if(!result?.ok)return {...result,restored:totalRestored,missing:totalMissing,rounds};
    totalRestored+=Number(result.restored||0);totalMissing+=Number(result.missing||0);
    if(Number(result.remaining||0)<=0)return {ok:true,restored:totalRestored,missing:totalMissing,remaining:0,rounds};
    if(Number(result.remaining)>=before)return {ok:false,error:`第 ${rounds} 轮后错误数量没有减少，已停止以避免死循环。`,restored:totalRestored,missing:totalMissing,remaining:Number(result.remaining),rounds};
    report=result.report||{};
  }
  return {ok:false,error:'自动恢复超过 100 轮，已停止以避免死循环。',restored:totalRestored,missing:totalMissing,remaining:Number(report?.skipped_count||0),rounds};
}
function loadData(data){if(!data||!data.ok){if(data!==null)alert(data?.error||'加载失败');return}project=data.project;records=data.records||[];rebuildRecordIndexes();selected=null;reviewedSelection.clear();reviewForceIds=[];filters={language:'all',pak:'all',filetype:'all',status:'all',file:'all',scope:'all'};currentPage=1;reviewCanResume=false;if(!reviewRunning)setReviewButton('idle');$('#projectPath').textContent=project.workspace||'';renderFilters();renderRows();const fixes=data.snapshotFix?.fixes||[];$('#progress').textContent=fixes.length?`已加载 ${records.length} 条有效文本；${fixes.join('；')}`:`已加载 ${records.length} 条有效文本`}
$('#openProject').onclick=async()=>loadData(await window.studio.loadProject());
let importRunning=false;
function setImportRunning(running,message=''){importRunning=running;$('#importStatus').classList.toggle('hidden',!running&&!message);$('#importProgress').classList.toggle('hidden',!running);if(running)$('#importProgress').removeAttribute('value');$('#importStatusText').textContent=message||'';$('#runImport').disabled=running;$('#pickPaks').disabled=running;$('#pickWorkspace').disabled=running;$('#cancelModal').disabled=running;$('#runImport').textContent=running?'分析中…':'开始分析'}
$('#importPaks').onclick=()=>{setImportRunning(false,'');$('#modal').classList.remove('hidden')};$('#cancelModal').onclick=()=>{if(importRunning)return;$('#modal').classList.add('hidden')};
$('#pickPaks').onclick=async()=>{pendingPaks=await window.studio.choosePaks();$('#pakSelection').textContent=pendingPaks.length?pendingPaks.join('\n'):'未选择'};
$('#pickWorkspace').onclick=async()=>{pendingWorkspace=await window.studio.chooseWorkspace();$('#workspaceSelection').textContent=pendingWorkspace||'未选择'};
$('#runImport').onclick=async()=>{if(!pendingPaks.length||!pendingWorkspace){alert('请先选择 PAK 和工作区');return}setImportRunning(true,'已开始分析，请稍候…');$('#progress').textContent='正在导入并分析 PAK…';const data=await window.studio.importPaks({workspace:pendingWorkspace,paks:pendingPaks});if(!data?.ok){setImportRunning(false,`分析失败：${data?.error||'未知错误'}`);$('#progress').textContent=`分析失败：${data?.error||'未知错误'}`;alert(data?.error||'分析失败');return}setImportRunning(false,'');$('#modal').classList.add('hidden');loadData(data)};
let translationClock=null;
function formatDuration(seconds){seconds=Math.max(0,Math.round(seconds||0));const h=Math.floor(seconds/3600),m=Math.floor(seconds%3600/60),s=seconds%60;return h?`${h}小时${String(m).padStart(2,'0')}分`:(m?`${m}分${String(s).padStart(2,'0')}秒`:`${s}秒`)}
function renderTranslationStatus(){if(!translationClock)return;const elapsed=(Date.now()-translationClock.started)/1000,p=translationClock.percent;const hasRows=Number.isFinite(translationClock.completedRows)&&Number.isFinite(translationClock.totalRows)&&translationClock.totalRows>0;const progressedRows=hasRows?Math.max(0,translationClock.completedRows-(translationClock.baseRows||0)):0;const rowSpeed=elapsed>0?progressedRows/elapsed:0;let eta='计算中';if(hasRows&&rowSpeed>0)eta=formatDuration(Math.max(0,translationClock.totalRows-translationClock.completedRows)/rowSpeed);else if(p>0.1&&p<100)eta=formatDuration(elapsed*(100-p)/p);else if(p>=100)eta='0秒';const speedText=hasRows&&rowSpeed>0?`${rowSpeed.toFixed(2)} 条/秒`:`${(elapsed>0?p/elapsed*60:0).toFixed(2)}%/分钟`;const rowText=hasRows?` · 行进度 ${Math.round(translationClock.completedRows).toLocaleString()}/${Math.round(translationClock.totalRows).toLocaleString()}`:'';const sample=translationClock.samples?.length?` · 样例：${translationClock.samples.join(' / ')}`:'';const text=`${translationClock.message}${rowText} · 已用 ${formatDuration(elapsed)} · 预计剩余 ${eta} · ${speedText}${sample}`;$('#progress').textContent=text;$('#progress').title=text}
function startTranslationClock(){stopTranslationClock();translationClock={started:Date.now(),percent:0,message:'正在准备翻译'};translationClock.timer=setInterval(renderTranslationStatus,1000);renderTranslationStatus()}
function stopTranslationClock(){if(translationClock?.timer)clearInterval(translationClock.timer);translationClock=null}
function setTranslationProgress(value,message,samples,metrics){const bar=$('#translationProgress');bar.classList.remove('hidden');bar.value=Math.max(0,Math.min(100,Number(value)||0));bar.setAttribute('aria-label',`翻译进度 ${bar.value.toFixed(1)}%`);if(translationClock){translationClock.percent=bar.value;if(message)translationClock.message=message;if(Array.isArray(samples))translationClock.samples=samples.map(x=>String(x||'').replace(/\s+/g,' ').slice(0,36)).filter(Boolean).slice(0,3);if(Number.isFinite(Number(metrics?.completed_rows))&&Number.isFinite(Number(metrics?.total_rows))){translationClock.completedRows=Number(metrics.completed_rows);translationClock.totalRows=Number(metrics.total_rows);if(!Number.isFinite(translationClock.baseRows))translationClock.baseRows=translationClock.completedRows}renderTranslationStatus()}}
function hideTranslationProgress(){stopTranslationClock();$('#translationProgress').classList.add('hidden')}
const VIETNAMESE_CHARS=/[ăâđêôơưĂÂĐÊÔƠƯàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵÀẢÃÁẠẰẲẴẮẶẦẨẪẤẬÈẺẼÉẸỀỂỄẾỆÌỈĨÍỊÒỎÕỌỒỔỖỐỘỜỞỠỚỢÙỦŨÚỤỪỬỮỨỰỲỶỸÝỴ]/;
const VIETNAMESE_WORDS=/\b(nhiệm|vụ|kỹ|năng|trang|bị|người|chơi|điểm|thương|phòng|thành|nhận|thưởng|thông|báo)\b/i;
function detectLiveLanguage(text){const value=String(text||''),han=/[\u3400-\u9fff]/.test(value),vi=VIETNAMESE_CHARS.test(value)||VIETNAMESE_WORDS.test(value);return han&&vi?'mixed':(han?'zh':(vi?'vi':'other'))}
let liveRefreshTimer=null;
function scheduleLiveRefresh(){clearTimeout(liveRefreshTimer);liveRefreshTimer=setTimeout(()=>{renderFilters();renderRows()},350)}
function applyUpcomingTranslations(ids){if(!Array.isArray(ids))return;for(const id of activeUpcoming){const old=recordById.get(String(id));if(old)delete old._upcomingRank}activeUpcoming=ids.map(String).filter(Boolean);for(let i=0;i<activeUpcoming.length;i++){const r=recordById.get(activeUpcoming[i]);if(r)r._upcomingRank=i}if(activeUpcoming.length)currentPage=1;scheduleLiveRefresh()}
function recordForUpdate(update){const ids=[update?.id,...(Array.isArray(update?.alt_ids)?update.alt_ids:[])].map(x=>String(x||'')).filter(Boolean);for(const id of ids){const r=recordById.get(id);if(r)return r}return null}
function applyLiveTranslationUpdates(updates){if(!Array.isArray(updates)||!updates.length)return;let changed=false,seen=new Set();for(const update of updates){const r=recordForUpdate(update);if(!r||seen.has(r.id))continue;seen.add(r.id);if(r.source_original===undefined)r.source_original=r.original;const value=String(update.text??'');const textChanged=r.original!==value;if(textChanged||update.review_status&&r.review_status!==update.review_status)changed=true;r.original=value;r.language=detectLiveLanguage(value);if(update.review_status)r.review_status=update.review_status;else if(textChanged){delete r.review_status;delete r.review_signature;delete r.reviewed_at;reviewedSelection.delete(String(r.id))}r._activitySeq=++liveSequence;if(r.language==='zh')r._translatedSeq=liveSequence;delete r._upcomingRank;r.status=r.original!==r.source_original?'已翻译':'未翻译';const tr=$(`#rows tr[data-id="${CSS.escape(r.id)}"]`);const cell=tr?.querySelector('.current-text');if(cell){cell.textContent=r.original;cell.title=r.original}if(selected?.id===r.id){selected=r;$('#translationText').value=r.original}}if(changed){if(filters.language!=='all')currentPage=1;scheduleLiveRefresh()}}
window.studio.onProgress(d=>{if(importRunning&&d.message)$('#importStatusText').textContent=d.message;if(d.phase==='model-csv'){applyUpcomingTranslations(d.upcoming_ids);applyLiveTranslationUpdates(d.updates);setTranslationProgress(d.percent,d.message||d.event)}else if(d.phase==='api-csv'){setTranslationProgress(d.percent,d.message||d.event,d.samples)}else if(d.phase==='api-review'){applyUpcomingTranslations(d.upcoming_ids);applyLiveTranslationUpdates(d.updates);setTranslationProgress(d.percent,d.message||'正在进行 API 审校',d.samples)}else if(d.phase==='xlsx-import'){applyLiveTranslationUpdates(d.updates);setTranslationProgress(d.percent,d.message||'正在导入润色')}else if(d.phase==='ollama'){applyLiveTranslationUpdates(d.updates);setTranslationProgress(d.percent,d.message,d.samples,d)}else if(d.phase==='cancel'){setTranslationProgress(translationClock?.percent||0,d.message||'正在取消')}else{hideTranslationProgress();$('#progress').textContent=d.message||d.event}});
function refreshRecordsFromResult(res){if(!res?.records)return;records=res.records;rebuildRecordIndexes();if(selected){selected=recordById.get(selected.id)||null;if(selected)showDetail(selected)}renderFilters();currentPage=1;renderRows()}
function setWorkflowRunning(running){workflowRunning=running;workflowCancelling=false;const btn=$('#autoModelBuild');if(btn){btn.disabled=false;btn.textContent=running?'取消翻译':'API 安全翻译并构建';btn.classList.toggle('danger',running)}}
function setWorkflowCancelling(){workflowCancelling=true;const btn=$('#autoModelBuild');if(btn){btn.disabled=true;btn.textContent='正在取消…'}}
function setReviewButton(state){
  const btn=$('#apiReview');if(!btn)return;
  reviewRunning=state==='running'||state==='pausing';reviewPausing=state==='pausing';
  btn.disabled=state==='pausing';
  const selectedMode=reviewForceIds.length>0;
  btn.textContent=state==='running'?(selectedMode?'暂停重审':'暂停审校'):state==='pausing'?'正在暂停…':state==='resume'?(selectedMode?'继续重审':'继续审校'):'API 审校';
  btn.classList.toggle('danger',reviewRunning);
  updateReviewSelectionControls();
}
function setWorkflowDisabled(disabled){for(const id of ['exportXlsx','importXlsxPolish','exportFullXlsx','exportGlossaryXlsx','importFullXlsx','apiReview','reReviewSelected','exportTsvCsv','mergeTsvCsv','splitMergedTsvCsv','translateTsvCsv','modelTranslateTsvCsv','apiTranslateMergedCsv','importTsvCsv','exportUntranslated','ollamaTranslate','importUntranslated','safePcMerge','autoModelBuild','buildPak','pushGithubPak']){const el=$('#'+id);if(!el)continue;if(id==='autoModelBuild'&&workflowRunning){el.disabled=false;continue}if(id==='apiReview'&&reviewRunning){el.disabled=false;continue}el.disabled=disabled}if(!disabled)updateReviewSelectionControls()}
function csvEscape(v){const s=String(v??'');return /[",\n\r]/.test(s)?'"'+s.replace(/"/g,'""')+'"':s}
function toExchangeText(value){
  let s=String(value??'');
  if(s.startsWith('$'))s=s.slice(1);
  return s.replace(/<\\n>/gi,'\n');
}
function fromExchangeText(value,sourceTemplate){
  let s=String(value??'').replace(/\r\n?/g,'\n');
  s=s.replace(/\n/g,'<\\n>');
  const src=String(sourceTemplate??'');
  if(src.startsWith('$')&&!s.startsWith('$'))s='$'+s;
  return s;
}
function selectedPakOrWarn(){
  if(filters.pak!=='all')return filters.pak;
  if(selected?.pak)return selected.pak;
  const paks=(project?.paks||[]).map(x=>x.pak).filter(Boolean);
  const unique=[...new Set(paks.length?paks:records.map(r=>r.pak).filter(Boolean))];
  if(unique.length===1)return unique[0];
  if(!unique.length){alert('当前项目没有可用的 PAK，请先导入 PAK。');return null}
  alert(`当前项目包含多个 PAK，无法自动判断要构建哪一个。\n\n请先在左侧“PAK”列表选择一个：\n${unique.join('\n')}`);
  return null
}
$('#exportCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak)return;const xs=records.filter(r=>r.pak===pak);const csv=['id,original',...xs.map(r=>`${csvEscape(r.id)},${csvEscape(toExchangeText(r.original))}`)].join('\r\n');const base=pak.replace(/\.pak$/i,'');const p=await window.studio.saveCsv(csv,`${base}_localization.csv`);if(p)$('#progress').textContent=`已导出 ${pak}：${xs.length} 条（CSV 中隐藏 $ 前缀，并把 <\\n> 显示为换行）→ ${p}`};
$('#importCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak)return;const result=await window.studio.loadCsv();if(!result)return;if(!result.ok){alert(result.error||'读取 CSV 失败');return}let rows;try{rows=parseCsv(result.text)}catch(e){alert(`CSV 解析失败：${e.message}`);return}if(!rows.length||rows[0][0].trim().replace(/^\ufeff/,'').toLowerCase()!=='id'||rows[0][1].trim().toLowerCase()!=='original'){alert('CSV 必须只有 id,original 两列，且第一行为表头。');return}const pakIds=new Set(records.filter(r=>r.pak===pak&&isPlayerVisibleRecord(r)).map(r=>r.id));let updated=0,changed=0,missing=0,wrongPak=0;for(let i=1;i<rows.length;i++){const id=(rows[i][0]||'').trim();if(!id)continue;const value=rows[i][1]??'';const r=recordById.get(id);if(!r){missing++;continue}if(!pakIds.has(id)){wrongPak++;continue}if(r.source_original===undefined)r.source_original=r.original;const before=r.original;const srcExchange=toExchangeText(r.source_original).replace(/\r\n?/g,'\n');const tgtExchange=String(value??'').replace(/\r\n?/g,'\n');const isChanged=tgtExchange!==srcExchange;r.original=isChanged?fromExchangeText(tgtExchange,r.source_original):r.source_original;r.language=detectLiveLanguage(r.original);r.status=isChanged?'已翻译':'未翻译';if(r.original!==before){delete r.review_status;delete r.review_signature;delete r.reviewed_at}if(isChanged)changed++;updated++}await persistRecords();currentPage=1;renderFilters();renderRows();if(selected){const fresh=recordById.get(selected.id);if(fresh)showDetail(fresh)}$('#progress').textContent=`导入 ${pak} 完成：读取 ${updated}，实际变化 ${changed}，未知 ID ${missing}，其他 PAK ID ${wrongPak}`};
$('#exportTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;$('#exportTsvCsv').disabled=true;$('#progress').textContent='正在批量导出文本资源 CSV…';const res=await window.studio.exportTsvCsv({project,pak});$('#exportTsvCsv').disabled=false;if(!res?.ok){alert(res?.error||'文本资源 CSV 导出失败');return}$('#progress').textContent=`文本资源 CSV 已导出：${res.report.records} 条 / ${res.report.csv_files} 个文件 → ${res.outputDir}`};
$('#mergeTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;const btn=$('#mergeTsvCsv');btn.disabled=true;$('#progress').textContent='正在按断点筛选未汉化内容并建立数字 ID 映射…';const res=await window.studio.mergeTsvCsv({project,pak});btn.disabled=false;if(!res?.ok){alert(res?.error||'CSV 合并失败');return}$('#progress').textContent=`未汉化内容合并完成：${res.report.files} 个文件 / ${res.report.rows} 行 → ${res.mergedCsv}`;alert(`未汉化内容合并完成。\n\n已完成文件：${res.report.completed_files||0}\n剩余文件：${res.report.remaining_files||res.report.files}\n包含待翻译内容：${res.report.files}\n导出待翻译行：${res.report.rows}\n数字 ID：1 - ${res.report.rows}\n\n合并文件：${res.mergedCsv}\n映射 JSON：${res.mappingJson}\n映射表格：${res.report.mapping_csv||''}\n\n合并 CSV 只保留 id/text 两列；标签、路径和占位符会用符号临时隔离，拆分时自动还原。\n译后会局部更新原 CSV，已汉化行不会被覆盖。`)};
$('#splitMergedTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;const btn=$('#splitMergedTsvCsv');btn.disabled=true;$('#progress').textContent='请选择外部翻译后的合并 CSV…';const res=await window.studio.splitMergedTsvCsv({project,pak});btn.disabled=false;if(res?.canceled){$('#progress').textContent='已取消拆分合并 CSV';return}if(!res?.ok){alert(res?.error||'合并 CSV 拆分失败');return}$('#progress').textContent=`合并 CSV 已拆分：恢复 ${res.report.files} 个文件 / ${res.report.rows} 行 → ${res.outputDir}`;alert(`拆分完成，原始哈希 ID 和占位符已全部恢复。\n\n文件：${res.report.files}\n行数：${res.report.rows}\n输出目录：${res.outputDir}\n\n下一步可点击“批量导入 CSV 还原文本资源”。`)};
let apiConfigState=null;
function activeApiProfile(){if(!apiConfigState)return null;return (apiConfigState.profiles||[]).find(p=>p.id===apiConfigState.activeProfileId)||(apiConfigState.profiles||[])[0]||null}
function currentModelList(){try{return JSON.parse($('#apiModel').dataset.models||'[]')}catch{return[]}}
function renderModelDropdown(query=''){const box=$('#apiModelDropdown');const models=currentModelList();const q=String(query||'').trim().toLowerCase();const shown=(q?models.filter(m=>m.toLowerCase().includes(q)):models).slice(0,300);box.innerHTML='';if(!shown.length){const div=document.createElement('div');div.className='model-option empty';div.textContent=models.length?'没有匹配的模型':'请点击获取模型按钮以获取模型';box.appendChild(div)}else{for(const model of shown){const div=document.createElement('div');div.className=`model-option${model===$('#apiModel').value?' active':''}`;div.textContent=model;div.title=model;div.dataset.model=model;box.appendChild(div)}}box.classList.remove('hidden')}
function hideModelDropdown(){const box=$('#apiModelDropdown');if(box)box.classList.add('hidden')}
function fillModelSelect(models,selectedModel){const input=$('#apiModel');const values=[...new Set([selectedModel,...(models||[])].filter(Boolean))];input.dataset.models=JSON.stringify(values);if(!values.length){input.value='';input.placeholder='请点击获取模型按钮以获取模型';hideModelDropdown();if($('#apiModelHint'))$('#apiModelHint').textContent='模型列表为空，请点击获取模型按钮以获取模型';return}input.value=selectedModel||values[0]||'';input.placeholder='点击显示全部模型，输入关键词筛选';if($('#apiModelHint'))$('#apiModelHint').textContent=`已加载 ${values.length} 个模型，点击输入框可查看全部`}
function fillProfileSelect(){const select=$('#apiProfile');select.innerHTML='';for(const profile of apiConfigState?.profiles||[]){const option=document.createElement('option');option.value=profile.id;option.textContent=profile.name||profile.model||profile.id;select.appendChild(option)}if(apiConfigState?.activeProfileId)select.value=apiConfigState.activeProfileId}
function loadProfileToForm(profile){$('#apiProfileName').value=profile?.name||'';$('#apiBaseUrl').value=profile?.baseUrl||'';$('#apiKey').value=profile?.apiKey||'';fillModelSelect(profile?.models||[],profile?.model||'');$('#apiBatchSize').value=Number.isFinite(Number(profile?.batchSize))?Number(profile.batchSize):0;$('#apiReviewMode').value=profile?.reviewMode==='all'?'all':'risk';$('#apiPrompt').value=profile?.prompt||'';$('#ollamaThink').checked=profile?.think===true;$('#ollamaThinkRow').classList.toggle('hidden',!(/ollama/i.test(profile?.name||'')||/:(11434|11435)/.test(profile?.baseUrl||'')))}
function saveFormToState(){if(!apiConfigState)apiConfigState={activeProfileId:'default',profiles:[]};let profile=activeApiProfile();if(!profile){profile={id:'default',name:'默认配置'};apiConfigState.profiles=[profile];apiConfigState.activeProfileId=profile.id}profile.name=$('#apiProfileName').value.trim()||profile.model||'未命名配置';profile.baseUrl=$('#apiBaseUrl').value.trim();profile.apiKey=$('#apiKey').value.trim();profile.model=$('#apiModel').value.trim();try{profile.models=JSON.parse($('#apiModel').dataset.models||'[]')}catch{profile.models=[]}if(profile.model&&!profile.models.includes(profile.model))profile.models.unshift(profile.model);const batchValue=Number($('#apiBatchSize').value);profile.batchSize=Number.isFinite(batchValue)?Math.max(0,Math.min(5000,batchValue)):0;profile.reviewMode=$('#apiReviewMode').value==='all'?'all':'risk';profile.think=$('#ollamaThink').checked;profile.prompt=$('#apiPrompt').value;return apiConfigState}
function newProfile(){saveFormToState();const id=`profile-${Date.now()}`;const prompt=activeApiProfile()?.prompt||'';const profile={id,name:'新配置',baseUrl:'',apiKey:'',model:'',models:[],batchSize:0,prompt};apiConfigState.profiles.push(profile);apiConfigState.activeProfileId=id;fillProfileSelect();loadProfileToForm(profile)}
async function deleteProfile(){if(!apiConfigState||apiConfigState.profiles.length<=1){alert('至少保留一个翻译配置。');return}const current=activeApiProfile();if(!await appConfirm(`删除配置“${current?.name||current?.id}”？`,{title:'删除翻译配置',confirmText:'删除',tone:'error'}))return;apiConfigState.profiles=apiConfigState.profiles.filter(p=>p.id!==current.id);apiConfigState.activeProfileId=apiConfigState.profiles[0].id;fillProfileSelect();loadProfileToForm(activeApiProfile())}
function readSettingsForm(){return saveFormToState()}
async function apiConfigForRun(){return apiConfigState?saveFormToState():await window.studio.apiTranslatorConfig()}
async function openSettings(){apiConfigState=await window.studio.apiTranslatorConfig();fillProfileSelect();loadProfileToForm(activeApiProfile());$('#settingsModal').classList.remove('hidden')}
$('#translatorSettings').onclick=openSettings;
$('#cancelSettings').onclick=()=>$('#settingsModal').classList.add('hidden');
$('#saveSettings').onclick=async()=>{const res=await window.studio.saveApiTranslatorConfig(readSettingsForm());if(!res?.ok){alert(res?.error||'保存设置失败');return}apiConfigState=res.config||apiConfigState;fillProfileSelect();$('#settingsModal').classList.add('hidden');$('#progress').textContent='翻译设置已保存'};
$('#apiProfile').onchange=()=>{const next=$('#apiProfile').value;saveFormToState();apiConfigState.activeProfileId=next;loadProfileToForm(activeApiProfile())};
$('#newApiProfile').onclick=newProfile;
$('#deleteApiProfile').onclick=deleteProfile;
$('#fetchApiModels').onclick=async()=>{const btn=$('#fetchApiModels');btn.disabled=true;btn.textContent='获取中…';const res=await window.studio.fetchApiModels(readSettingsForm());btn.disabled=false;btn.textContent='获取模型';if(!res?.ok){alert(res?.error||'获取模型失败');return}apiConfigState=res.config||apiConfigState;const profile=activeApiProfile();if(profile&&Array.isArray(res.models)&&res.models.length){profile.models=res.models;if(!profile.model||!profile.models.includes(profile.model))profile.model=profile.models[0]}fillProfileSelect();loadProfileToForm(activeApiProfile());const loaded=res.models?.length||activeApiProfile()?.models?.length||0;$('#progress').textContent=`已获取 ${loaded} 个模型`;if($('#apiModelHint'))$('#apiModelHint').textContent=`已获取 ${loaded} 个模型，可输入关键词筛选`};
$('#apiModel').addEventListener('focus',()=>renderModelDropdown(''));
$('#apiModel').addEventListener('click',()=>renderModelDropdown(''));
$('#apiModel').addEventListener('input',e=>renderModelDropdown(e.target.value));
$('#apiModelDropdown').addEventListener('mousedown',e=>{const item=e.target.closest('.model-option');if(!item||!item.dataset.model)return;e.preventDefault();$('#apiModel').value=item.dataset.model;hideModelDropdown()});
document.addEventListener('mousedown',e=>{if(!e.target.closest('.model-picker'))hideModelDropdown()});
$('#exportXlsx').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;const btn=$('#exportXlsx');btn.disabled=true;$('#progress').textContent='正在按玩家可见白名单逐文件导出 XLSX…';try{const res=await window.studio.exportXlsx({project,pak});if(!res?.ok){alert(res?.error||'导出 XLSX 失败');$('#progress').textContent=`导出失败：${res?.error||'未知错误'}`;return}const r=res.report||{};$('#progress').textContent=`玩家可见 XLSX 已导出：${r.exported_files||0} 个文件 / ${r.unique_rows||0} 条 → ${res.outputDir}`;alert(`逐文件导出完成。\n\n范围：仅玩家展示字段（后台白名单）\nPAK：${pak}\n扫描文本文件：${r.scanned_files||0}\n生成独立 XLSX：${r.exported_files||0}\n没有玩家展示文本：${r.skipped_files||0}\n玩家展示文本单元格：${r.source_rows||0}\n文件内去重后：${r.unique_rows||0}\n带入现有中文：${r.prefilled_translations||0}\n扫描器额外候选已排除：${r.excluded_not_player_visible||0}\n\nXLSX 文件夹只放工作簿，可以直接交给 Google 翻译。配置文件已自动放到旁边的 config 文件夹，不需要操作。\n\nXLSX：\n${res.outputDir}\n\n配置：\n${res.configDir||r.config_dir||''}\n\n导出过程未复制或修改游戏资源。`)}catch(e){$('#progress').textContent=`导出异常：${e.message||e}`;alert(e.message||String(e))}finally{btn.disabled=false}};
$('#importXlsxPolish').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;const btn=$('#importXlsxPolish');btn.disabled=true;setWorkflowDisabled(true);startTranslationClock();setTranslationProgress(0,'请选择只包含译后 XLSX 的文件夹');try{const res=await window.studio.importXlsxPolish({project,pak});if(res?.canceled){hideTranslationProgress();stopTranslationClock();return}if(!res?.ok){hideTranslationProgress();stopTranslationClock();refreshRecordsFromResult(res);alert(res?.error||'批量导入 XLSX 失败',{title:'导入链路未完成',tone:'error'});return}refreshRecordsFromResult(res);const r=res.import||{},s=res.sync||{},rejected=Number(r.rejected||0)+Number(res.autoRejected||0);setTranslationProgress(100,rejected?'安全译文已同步，坏格式译文已保留原文':'整个 XLSX 文件夹已导入并同步');stopTranslationClock();$('#progress').textContent=`文件夹导入：${r.imported_files||0}/${r.total_files||0} 个，安全写回 ${s.modified_records||0} 条，拒收 ${rejected} 条，资源 ${s.changed_file_count||0} 个`;alert(`XLSX 文件夹处理完成。\n\nXLSX：${res.sourceFolder||''}\n自动使用配置：${res.configFolder||r.config_dir||''}\n发现文件：${r.total_files||0}\n成功读取：${r.imported_files||0}\n文件失败：${r.failed_files||0}\n安全更新记录：${r.updated||0}\n本次实际变化：${r.changed||0}\n安全拒收并保留原文：${rejected} 条\n参与资源回写：${s.modified_records||0} 条\n生成资源文件：${s.changed_file_count||0} 个\n最终回写跳过：${s.skipped_count||0} 条\n\n修改目录：${res.modifiedDir||''}`,{title:rejected?'导入完成，部分译文被安全拒收':'导入与资源同步完成',tone:rejected?'warning':'success'})}finally{setWorkflowDisabled(false);btn.disabled=false}};
$('#exportFullXlsx').onclick=async()=>{if(!project)return;const btn=$('#exportFullXlsx');btn.disabled=true;setWorkflowDisabled(true);$('#progress').textContent='正在合并导出全部 PAK 的免占位符 XLSX…';try{const res=await window.studio.exportFullXlsx({project});if(!res?.ok){alert(res?.error||'导出全部 PAK XLSX 失败');$('#progress').textContent=`导出失败：${res?.error||'未知错误'}`;return}refreshRecordsFromResult(res);const r=res.report||{},added=Number(res.refresh?.added||0);$('#progress').textContent=`全部 PAK 已合并：${r.pak_count||0} 个 PAK / ${r.unique_rows||0} 条 → ${res.xlsxPath}`;alert(`全部 PAK XLSX 导出完成。\n\n本次自动补齐漏检文本：${added} 条\nPAK：${(r.paks||[]).join('、')}\n玩家可见记录：${r.source_rows||0}\n自然语言去重片段：${r.unique_rows||0}\n带入现有译文：${r.prefilled_translations||0}\n\n列：id / pak / source_file / text\n只有 text 需要交给谷歌翻译。所有标签、路径、$变量、格式符和数字均未进入 XLSX，而是保存在 config 骨架中。\n\nXLSX：\n${res.xlsxPath}\n\n必须保留的配置：\n${res.configDir||''}`)}finally{setWorkflowDisabled(false);btn.disabled=false}};
$('#exportGlossaryXlsx').onclick=async()=>{if(!project)return;const btn=$('#exportGlossaryXlsx');btn.disabled=true;setWorkflowDisabled(true);$('#progress').textContent='正在从全部 PAK 玩家可见文本提取纯术语库…';try{const res=await window.studio.exportGlossaryXlsx({project});if(!res?.ok){alert(res?.error||'导出术语库 XLSX 失败');$('#progress').textContent=`术语库导出失败：${res?.error||'未知错误'}`;return}refreshRecordsFromResult(res);const r=res.report||{},added=Number(res.refresh?.added||0);$('#progress').textContent=`纯术语库 XLSX 已导出：${r.unique_terms||0} 个去重词语 → ${res.xlsxPath}`;alert(`纯术语库 XLSX 导出完成。\n\n本次自动补齐漏检文本：${added} 条\nPAK：${(r.paks||[]).join('、')}\n玩家可见记录：${r.source_rows||0}\n安全文本片段：${r.scanned_segments||0}\n去重词语：${r.unique_terms||0}\n\nXLSX 只有一列：text\n每行一个词/术语，可直接作为后续 DB 术语来源。\n\n标签、路径、$变量、格式符、数字和标点不会作为术语导出。\n\nXLSX：\n${res.xlsxPath}`,{title:'纯术语库 XLSX 已导出',tone:'success'})}finally{setWorkflowDisabled(false);btn.disabled=false}};
$('#importFullXlsx').onclick=async()=>{
  if(!project)return;
  const btn=$('#importFullXlsx');
  btn.disabled=true;setWorkflowDisabled(true);startTranslationClock();
  setTranslationProgress(0,'请选择谷歌翻译后的全部 PAK XLSX');
  try{
    const res=await window.studio.importFullXlsx({project});
    if(res?.canceled){hideTranslationProgress();stopTranslationClock();return}
    if(!res?.ok){hideTranslationProgress();stopTranslationClock();refreshRecordsFromResult(res);alert(res?.error||'导入全部 PAK XLSX 失败',{title:'导入链路未完成',tone:'error'});return}
    refreshRecordsFromResult(res);
    const r=res.import||{},s=res.sync||{},rr=res.remaining||{};
    const rejectedRefs=Number(r.rejected_segments||0)+Number(res.autoRejected||0);
    const rejectedUnique=Number(r.rejected_unique_segments||0)+Number(res.autoRejected||0);
    const remainingRefs=Number(r.remaining_vietnamese_records||0);
    const remainingUnique=Number(rr.workbook_rows??r.remaining_vietnamese_unique_segments??0);
    const remainingPath=res.remainingXlsx||rr.xlsx||'';
    setTranslationProgress(100,remainingRefs?`已导入；剩余 ${remainingUnique} 个去重片段（${remainingRefs} 处引用）`:'全部 PAK 已导入并同步');
    stopTranslationClock();
    $('#progress').textContent=`全部 PAK 导入：XLSX 译文 ${r.translated_xlsx_rows||0} 行，记录变化 ${r.changed||0} 条，资源 ${s.changed_file_count||0} 个`;
    alert(`全部 PAK XLSX 导入完成。\n\nPAK：${(r.paks||[]).join('、')}\nXLSX 总行数：${r.xlsx_rows||0}\n成功识别 ID：${r.recognized_xlsx_rows||0}\n检测到谷歌译文：${r.translated_xlsx_rows||0} 个去重片段\n与导出原文相同：${r.unchanged_xlsx_rows||0} 个去重片段\n本次记录实际变化：${r.changed||0}\n剩余未翻译：${remainingUnique} 个去重片段（${remainingRefs} 处资源引用）\n安全回退：${rejectedUnique} 个去重片段（${rejectedRefs} 处资源引用）\n生成资源文件：${s.changed_file_count||0} 个\n\n导入审计报告：\n${r.import_report||''}\n\n已另存新的残留 XLSX（不会覆盖你选中的谷歌译文表）：\n${remainingPath}`,{title:remainingRefs||r.missing_segments||rejectedRefs?'导入完成，已生成残留 XLSX':'全部 PAK 导入完成',tone:remainingRefs||r.missing_segments||rejectedRefs?'warning':'success'})
  }finally{setWorkflowDisabled(false);btn.disabled=false}
};
async function runApiReview(forceIds=[]){
  const pak=selectedPakOrWarn();if(!pak||!project)return;
  reviewForceIds=[...new Set((forceIds||[]).map(String).filter(Boolean))];
  const config=await apiConfigForRun();
  const profile=(config.profiles||[]).find(p=>p.id===config.activeProfileId)||(config.profiles||[])[0]||{};
  if(!profile.baseUrl||!profile.model){alert('请先在“翻译设置”中填写 BaseURL，并获取、选择模型。');await openSettings();return}
  const scope=reviewForceIds.length?`选中的 ${reviewForceIds.length} 条已审核内容`:(profile.reviewMode==='all'?'全部文本':'中越混合、有风险和低质量中文');
  if(!reviewCanResume&&!await appConfirm(`将使用“${profile.name||profile.model}”审校 ${scope}。\n\n相同文本会先查缓存；每批完成都会保存检查点和译后 XLSX。是否继续？`,{title:'开始 API 审校',confirmText:'开始审校'}))return;
  setReviewButton('running');setWorkflowDisabled(true);startTranslationClock();
  setTranslationProgress(0,reviewCanResume?'正在从检查点继续 API 审校':(reviewForceIds.length?'正在准备重新审核选中内容':'正在筛选需要 API 审校的风险文本'));
  try{
    const res=await window.studio.apiReview({project,pak,config,forceIds:reviewForceIds});
    if(res?.canceled){reviewCanResume=true;hideTranslationProgress();$('#progress').textContent='API 审校已暂停；已完成批次已保存，点击“继续审校”可接着处理';setReviewButton('resume');return}
    if(!res?.ok){reviewCanResume=true;hideTranslationProgress();$('#progress').textContent=`API 审校中断：${res?.error||'未知错误'}；可从检查点继续`;setReviewButton('resume');alert(res?.error||'API 审校失败');return}
    refreshRecordsFromResult(res);reviewCanResume=(res.report?.remaining||0)>0;
    for(const id of reviewForceIds)reviewedSelection.delete(id);
    if(!reviewCanResume)reviewForceIds=[];
    renderRows();
    setTranslationProgress(100,'API 审校与文本资源回写完成');stopTranslationClock();
    $('#progress').textContent=`API 审校完成：候选 ${res.report?.candidates||0}，通过 ${res.report?.reviewed||0}，缓存 ${res.report?.cached||0}，剩余 ${res.report?.remaining||0}`;
    const scopeName=res.report?.mode==='selected'?'选中项重新审核':(res.report?.mode==='all'?'全部重新审校':'仅风险项');
    alert(`API 审校完成。\n\n范围：${scopeName}\n候选：${res.report?.candidates||0} 条\n通过：${res.report?.reviewed||0} 条\n缓存命中：${res.report?.cached||0} 条\nAPI 请求：${res.report?.api_calls||0} 次\n接口超时/坏响应：${res.report?.api_errors||0} 条\n失败/仍有风险：${res.report?.remaining||0} 条\n\n译后 XLSX：${res.reviewedXlsx||res.report?.reviewed_xlsx}\n检查点：${res.report?.checkpoint}\n\n现在可以点击“构建 PAK”。`);
    setReviewButton(reviewCanResume?'resume':'idle');
  }catch(e){reviewCanResume=true;hideTranslationProgress();$('#progress').textContent=`API 审校中断：${e.message||e}；可从检查点继续`;setReviewButton('resume');alert(e.message||String(e))}
  finally{setWorkflowDisabled(false);if(reviewRunning)setReviewButton(reviewCanResume?'resume':'idle')}
}
$('#apiReview').onclick=async()=>{if(reviewRunning){if(reviewPausing)return;setReviewButton('pausing');setTranslationProgress(translationClock?.percent||0,'正在暂停 API 审校，已完成批次不会丢失');await window.studio.cancelCurrentTask();return}await runApiReview(reviewCanResume?reviewForceIds:[])};
$('#reReviewSelected').onclick=async()=>{if(reviewRunning)return;const ids=[...reviewedSelection].filter(id=>statusOf(recordById.get(id)||{})==='已审核');if(!ids.length){reviewedSelection.clear();updateReviewSelectionControls();alert('请先勾选需要重新审核的“已审核”内容。');return}reviewCanResume=false;await runApiReview(ids)};
$('#apiTranslateMergedCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;let config=await apiConfigForRun();let profile=(config.profiles||[]).find(p=>p.id===config.activeProfileId)||(config.profiles||[])[0]||{};if(!profile.baseUrl||!profile.model){alert('请先在“翻译设置”里填写 BaseURL 和模型。远程 API 通常还需要 API key，Ollama 可以留空。');await openSettings();return}const btn=$('#apiTranslateMergedCsv');btn.disabled=true;startTranslationClock();setTranslationProgress(0,'正在导出完整本地化 CSV、去重并拆分 API 分片');const res=await window.studio.apiTranslateMergedCsv({project,pak,config});btn.disabled=false;if(!res?.ok){hideTranslationProgress();alert(res?.error||'API 翻译失败');return}refreshRecordsFromResult(res);setTranslationProgress(100,'API 翻译完成');stopTranslationClock();$('#progress').textContent=`API 翻译完成：完整 CSV ${res.prepare?.source_rows||0} 行，缓存命中 ${res.report.tm_hits||0}，回写 ${res.import?.updated||0} 条 → ${res.csvAfter}`;alert(`API 翻译完成。\n\n配置：${profile.name||''}\n模型：${res.report.model}\n完整 CSV：${res.inputCsv}\n完整 CSV 行数：${res.prepare?.source_rows||0} 条\n原始待翻译：${res.prepare?.occurrence_rows||0} 条\n去重后：${res.prepare?.unique_rows||0} 条\n分片：${res.prepare?.chunks||0} 个 CSV\n数据库命中：${res.report.tm_hits||0} 条\n实际 API 调用：${res.report.api_calls||0} 次\n新增 API 缓存：${res.report.learned_tm||0} 条\nAPI 修改：${res.report.changed}\nAPI 失败/拒收：${res.report.failed}\n译后完整 CSV：${res.csvAfter}\n资源回写：${res.import?.updated||0} 条\n界面同步：${res.sync?.changed||0} 条\n\nAPI 包：${res.packageDir}\n译后分片：${res.outputDir}\n构建目录：${res.modifiedDir}`)};
$('#translateTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;$('#translateTsvCsv').disabled=true;$('#progress').textContent='正在使用 TM 数据库 + translate.py 批量翻译文本资源 CSV…';const res=await window.studio.translateTsvCsv({project,pak});$('#translateTsvCsv').disabled=false;if(!res?.ok){alert(res?.error||'脚本翻译失败');return}if((res.report.changed||0)===0){alert(`脚本已运行，但没有任何 CSV 文本发生变化。\n\n请检查 translate.py 的 translate_string(text) 是否返回了不同内容。\n输入目录：${res.inputDir}\n输出目录：${res.outputDir}\n\n注意：脚本翻译不会覆盖 tsv_csv_before，只会写入 tsv_csv_after。`)}else if((res.report.rejected_partial||0)>0){alert(`已拒绝 ${res.report.rejected_partial} 行夹生翻译，并在 CSV 中保留越南原文。\n\nTM 命中：${res.report.tm_hits||0}\n词典完整翻译：${res.report.script_hits||0}\n\n需要完整中文时，请使用“本地模型翻译文本资源 CSV”。\n输出目录：${res.outputDir}`)}$('#progress').textContent=`翻译完成：TM ${res.report.tm_hits||0}，词典完整 ${res.report.script_hits||0}，拒绝夹生 ${res.report.rejected_partial||0}`};
$('#modelTranslateTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;if(!await appConfirm('将按“翻译记忆 -> 词典脚本 -> 本地模型”逐个 CSV 翻译并保存断点。自动机翻结果不会写入翻译记忆库；再次运行会跳过已完成文件。是否继续？',{title:'开始本地模型翻译',confirmText:'开始翻译'}))return;const btn=$('#modelTranslateTsvCsv');btn.disabled=true;startTranslationClock();setTranslationProgress(0,'正在检查已完成文件并加载翻译链路');const res=await window.studio.modelTranslateTsvCsv({project,pak});btn.disabled=false;if(!res?.ok){hideTranslationProgress();alert(res?.error||'本地模型翻译失败');return}if(res.records){records=res.records;for(const item of records){if(item.source_original===undefined)item.source_original=item.original;item.language=detectLiveLanguage(item.original)}recordById=new Map(records.map(item=>[item.id,item]));renderFilters();renderRows()}const r=res.report||{};setTranslationProgress(100,'翻译完成');stopTranslationClock();$('#progress').textContent=`翻译完成：本次 ${r.translated_files||0} 个，跳过 ${r.skipped_files||0} 个，界面同步 ${res.sync?.changed||0} 条`;alert(`组合翻译完成。\n\nCSV 总数：${r.csv_files||0}\n本次翻译：${r.translated_files||0}\n断点跳过：${r.skipped_files||0}\nTM 命中：${r.tm_hits||0}\n词典完整翻译：${r.script_hits||0}\n模型整句翻译：${r.model_hits||0}\n新增翻译记忆：${r.learned_tm||0}\n质量检查拒绝：${r.rejected||0}\n界面同步：${res.sync?.changed||0}\n\n输出目录：${res.outputDir}`)};
$('#importTsvCsv').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;$('#importTsvCsv').disabled=true;$('#progress').textContent='正在批量导入 CSV 并还原文本资源…';const res=await window.studio.importTsvCsv({project,pak});$('#importTsvCsv').disabled=false;if(!res?.ok){alert(res?.error||'文本资源 CSV 导入失败');return}if(res.records){records=res.records;recordById=new Map(records.map(r=>[r.id,r]));if(selected){selected=recordById.get(selected.id)||null;if(selected)showDetail(selected)}renderFilters();currentPage=1;renderRows()}$('#progress').textContent=`文本资源已生成：更新 ${res.report.updated}，跳过风险 ${res.report.skipped}；界面同步 ${res.sync?.changed||0} 条 → ${res.outputDir}`};
$('#autoModelBuild').onclick=async()=>{if(workflowRunning){if(workflowCancelling)return;setWorkflowCancelling();setTranslationProgress(translationClock?.percent||0,'正在取消翻译任务');await window.studio.cancelCurrentTask();return}const pak=selectedPakOrWarn();if(!pak||!project)return;let config=await apiConfigForRun();let profile=(config.profiles||[]).find(p=>p.id===config.activeProfileId)||(config.profiles||[])[0]||{};if(!profile.baseUrl||!profile.model){alert('安全翻译并构建现在使用 API 完整 CSV 流程。\n\n请先在“翻译设置”里填写 BaseURL，并点击“获取模型”后选择模型。');await openSettings();return}if(!await appConfirm('将自动执行：导出完整本地化 CSV -> 去重拆分 CSV -> API 翻译 -> 合并译后完整 CSV -> 回写文本资源 -> 快速构建 PAK。\n\n路径、INI 段名、资源文件名、标签和占位符会被保护；质量检查拒收的条目会保留原文，不会写入缓存。是否继续？',{title:'API 安全翻译并构建',confirmText:'开始执行'}))return;setWorkflowRunning(true);setWorkflowDisabled(true);try{startTranslationClock();setTranslationProgress(0,'安全流程：正在导出完整本地化 CSV 并拆分 API 分片');let apiRes=await window.studio.apiTranslateMergedCsv({project,pak,config});if(!apiRes?.ok){if(apiRes.canceled){$('#progress').textContent='安全流程已取消';hideTranslationProgress();return}throw new Error(apiRes?.error||'API 完整 CSV 翻译失败')}refreshRecordsFromResult(apiRes);setTranslationProgress(99,'安全流程：正在快速构建 PAK');let buildRes=await window.studio.buildPak({project,pak});if(!buildRes?.ok){if(buildRes.canceled){$('#progress').textContent='安全流程已取消';hideTranslationProgress();return}throw new Error(buildRes?.error||'构建 PAK 失败')}setTranslationProgress(100,'安全流程完成');stopTranslationClock();$('#progress').textContent=`安全流程完成：${pak} API 回写 ${apiRes.import?.updated||0} 条，构建修改 ${buildRes.report.changed_entries} 个条目 → ${buildRes.report.output}`;alert(`安全流程完成。\n\nPAK：${pak}\n配置：${profile.name||''}\n模型：${apiRes.report?.model||profile.model}\n完整 CSV：${apiRes.inputCsv}\n完整 CSV 行数：${apiRes.prepare?.source_rows||0} 条\n待翻译：${apiRes.prepare?.occurrence_rows||0} 条\n去重后：${apiRes.prepare?.unique_rows||0} 条\n分片：${apiRes.prepare?.chunks||0} 个 CSV\n数据库命中：${apiRes.report?.tm_hits||0} 条\nAPI 调用：${apiRes.report?.api_calls||0} 次\nAPI 失败/拒收：${apiRes.report?.failed||0}\n资源回写：${apiRes.import?.updated||0} 条\n构建修改：${buildRes.report.changed_entries} 个条目\n校验：${buildRes.report.roundtrip==='skipped'?'快速模式已跳过完整解包校验':buildRes.report.roundtrip}\n\n译后完整 CSV：${apiRes.csvAfter}\n构建目录：${apiRes.modifiedDir}\n输出：${buildRes.report.output}`)}catch(e){hideTranslationProgress();alert(e.message||String(e));$('#progress').textContent=`安全流程失败：${e.message||e}`}finally{setWorkflowDisabled(false);setWorkflowRunning(false)}};
$('#exportUntranslated').onclick=async()=>{
  if(!project)return;
  const btn=$('#exportUntranslated');
  btn.disabled=true;setWorkflowDisabled(true);
  $('#progress').textContent='正在导出全部 PAK 的未翻译去重 XLSX…';
  try{
    const res=await window.studio.exportUntranslatedXlsx({project});
    if(!res?.ok){alert(res?.error||'导出未翻译 XLSX 失败',{title:'导出失败',tone:'error'});$('#progress').textContent=`导出失败：${res?.error||'未知错误'}`;return}
    refreshRecordsFromResult(res);
    const r=res.report||{},total=Number(r.workbook_rows||0),added=Number(res.refresh?.added||0);
    if(total===0){
      $('#progress').textContent='所有已识别玩家可见文本均无越南文残留';
      alert(`未检测到需要再次翻译的越南文片段。\n\n检查范围：${r.source_rows||0} 条玩家可见记录\nPAK：${(r.paks||[]).join('、')}`,{title:'没有未翻译内容',tone:'success'});
      return
    }
    $('#progress').textContent=`未翻译 XLSX 已导出：${total} 个去重片段 → ${res.xlsxPath}`;
    alert(`未翻译 XLSX 导出完成。\n\nPAK：${(r.paks||[]).join('、')}\n本次自动补齐漏检文本：${added} 条\n玩家可见记录：${r.source_rows||0}\n需要谷歌翻译：${total} 个去重片段\n\nXLSX：\n${res.xlsxPath}\n\n只需翻译 text 列。翻译完成后仍点击“导入全部 PAK XLSX”，不需要另一个导入按钮。\n标签、路径、变量、格式符和数字保存在：\n${res.configDir||''}`,{title:'未翻译 XLSX 已导出',tone:'success'})
  }finally{setWorkflowDisabled(false);btn.disabled=false}
};
$('#ollamaTranslate').onclick=async()=>{if(!project)return;const pak=(filters.pak!=='all'?filters.pak:(selected?.pak||project.paks?.map(item=>item.pak).find(Boolean)));if(!pak){alert('当前项目没有可用的 PAK，请先导入 PAK。');return}const config=await apiConfigForRun();const profiles=config?.profiles||[];const profile=profiles.find(p=>p.id==='ollama'||String(p.name||'').trim().toLowerCase()==='ollama')||profiles.find(p=>/:(11434|11435)(?:\/|$)/.test(String(p.baseUrl||'')))||profiles[0]||{};const model='translategemma:4b';const modelBucketCap=/qwen/i.test(model)?25:50;const configured=Number(profile.batchSize);const bucketSize=Number.isFinite(configured)&&configured>0?Math.min(modelBucketCap,Math.max(1,Math.trunc(configured))):modelBucketCap;if(!await appConfirm(`请选择 XLSX 文件夹。既可选“逐文件 XLSX”目录、“全部 PAK 未翻译 XLSX”目录，也可选“术语库 XLSX”目录；术语库只会翻译表格本身，不会写回 PAK。Ollama 会先处理仍含越南文/中越混合的文件，纯中文文件直接复用或跳过；已完成文件会从检查点续跑。\n\n模型：${model}\n每桶最多：${bucketSize} 行（长文本仍按约 3000 字自动拆桶）`,{title:'开始全部 PAK 文件队列',confirmText:'选择 XLSX 文件夹'}))return;ollamaActiveThink=profile.think===true;const btn=$('#ollamaTranslate');btn.disabled=true;setWorkflowDisabled(true);startTranslationClock();setTranslationProgress(0,'正在识别 XLSX 类型并建立全部 PAK 翻译队列');try{const res=await window.studio.ollamaTranslate({project,pak,bucketSize,think:profile.think===true});if(res?.canceled){hideTranslationProgress();stopTranslationClock();return}if(!res?.ok){hideTranslationProgress();stopTranslationClock();refreshRecordsFromResult(res);alert(res?.error||'Ollama 文件夹翻译失败',{title:'Ollama 链路未完成',tone:'error'});return}refreshRecordsFromResult(res);const r=res.report||{};if(res.glossaryOnly){setTranslationProgress(100,r.stopped?'术语库翻译已停止':'术语库翻译完成');stopTranslationClock();$('#progress').textContent=`术语库 Ollama：完成 ${r.completed_files||0}/${r.total_files||0} 个文件，剩余 ${r.remaining_files||0}`;alert(`术语库 Ollama ${r.stopped?'已停止':'已完成'}。\n\nXLSX：${res.xlsxPath||res.folder||r.folder||''}\n配置与检查点：${res.configFolder||r.config_dir||''}\n术语行数：${r.reports?.[0]?.total_rows||0}\n已翻译：${r.reports?.[0]?.translated_rows||0}\n剩余：${r.reports?.[0]?.remaining_rows||0}\n\n这是术语表翻译，不会导入 text_records，也不会同步资源或构建 PAK。`,{title:'术语库翻译完成',tone:'success'});return}const imp=res.importReport||{},sync=res.resourceSync?.report||{},rejected=Number(imp.rejected||0)+Number(res.autoRejected||0);setTranslationProgress(100,r.stopped?'文件夹队列已停止':(rejected?'翻译完成，坏格式译文已保留原文':'文件夹队列翻译完成'));stopTranslationClock();$('#progress').textContent=`Ollama 队列：越南文优先 ${r.prioritized_vietnamese_files||0} 个，完成 ${r.completed_files||0}/${r.total_files||0}，跳过 ${r.skipped_completed_files||0}，剩余 ${r.remaining_files||0}`;alert(`Ollama 文件夹队列${r.stopped?'已停止':'已完成'}。\n\nXLSX：${res.folder||r.folder||''}\n配置与检查点：${res.configFolder||r.config_dir||''}\n文件总数：${r.total_files||0}\n仍含越南文并优先处理：${r.prioritized_vietnamese_files||0}\n累计完成：${r.completed_files||0}\n本次直接跳过已完成：${r.skipped_completed_files||0}\n本次实际进入处理：${r.processed_files||0}\n剩余文件：${r.remaining_files||0}\n失败文件：${r.failed_files||0}\n安全导回变化：${imp.changed||0} 条\n安全拒收并保留原文：${rejected} 条\n参与资源回写：${sync.modified_records||0} 条\n生成资源文件：${sync.changed_file_count||0} 个\n最终回写跳过：${sync.skipped_count||0} 条`,{title:rejected?'Ollama 完成，部分译文被安全拒收':'Ollama 翻译与资源同步完成',tone:rejected?'warning':'success'})}finally{setWorkflowDisabled(false);btn.disabled=false}};
$('#importUntranslated').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;if(!await appConfirm(`将把“Ollama 翻译”生成的译后 XLSX 导回 ${pak} 的 text_records.json，并做占位符/格式一致性校验（拒绝不合格译文）。是否继续？`,{title:'导回翻译',confirmText:'导回'}))return;const btn=$('#importUntranslated');btn.disabled=true;$('#progress').textContent='正在把译后 XLSX 导回 text_records.json…';try{const res=await window.studio.importUntranslatedXlsx({project,pak});if(!res?.ok){alert(res?.error||'导回翻译失败');$('#progress').textContent=`导回失败：${res?.error||'未知错误'}`;return}refreshRecordsFromResult(res);const r=res.report||{};const cov=r.coverage_after||{};$('#progress').textContent=`导回完成：更新 ${r.updated||0} 条，拒绝 ${r.rejected||0} 条，覆盖率 ${cov.percent||0}%`;alert(`导回翻译完成。\n\nPAK：${pak}\n更新：${r.updated||0} 条\n拒绝：${r.rejected||0} 条\n未翻译原文跳过：${r.unchanged_rows_skipped||0}\n空行跳过：${r.empty_rows_skipped||0}\n未映射 ID：${r.unmapped_xlsx_ids||0}\n覆盖率：${cov.percent||0}%\n\n下一步点击“构建 PAK”。`)}finally{btn.disabled=false}};
// Query the current mobile resource set, then migrate only structurally verified
// international Chinese into the selected PAK.
let ollamaRunning=false;
let ollamaStopping=false;
let ollamaActiveThink=false;
window.studio.onProgress(d=>{
  if(ollamaRunning && d.phase==='ollama'){
    const btn=$('#ollamaTranslate');
    if(d.stopped)$('#progress').textContent=d.message||'Ollama 已停止，正在保存成果和检查点';
    btn.disabled=false;
    btn.textContent=ollamaStopping?'正在停止…':'停止文件队列';
    btn.title='完成当前桶后停止，保留已译内容和逐文件检查点';
  }
});
const startOllamaTranslation=$('#ollamaTranslate').onclick;
$('#ollamaTranslate').onclick=async()=>{
  const btn=$('#ollamaTranslate');
  if(ollamaRunning){
    if(ollamaStopping)return;
    btn.disabled=true;
    try {
      const res=await window.studio.ollamaControl({stop:true,think:ollamaActiveThink});
      if(!res.ok){$('#progress').textContent=res.error;return;}
      ollamaStopping=true;
      btn.textContent='正在停止…';
      $('#progress').textContent='正在停止：当前桶完成后保存成果和检查点';
    } finally {btn.disabled=ollamaStopping;}
    return;
  }
  ollamaRunning=true;
  ollamaStopping=false;
  btn.disabled=true;
  try { await startOllamaTranslation(); }
  finally {ollamaRunning=false;ollamaStopping=false;btn.disabled=false;btn.textContent='按文件 Ollama';btn.title='';}
};
$('#safePcMerge').onclick=async()=>{
  const pak=selectedPakOrWarn();if(!pak||!project)return;
  if(!await appConfirm(`将完全在本地按序号、资源哈希、稳定行和资源路径优先迁移国际版官方中文；官方原文未命中时，采用已通过占位符校验的术语表回译缓存。\n\n冲突项与结构不兼容项不会覆盖，回译结果会单独标记，便于后续润色。是否继续？`,{title:'国际版迁移中文',confirmText:'开始迁移'}))return;
  const btn=$('#safePcMerge');btn.disabled=true;setWorkflowDisabled(true);startTranslationClock();setTranslationProgress(0,`正在查询 ${pak} 并匹配国际版对象`);
  try{
    const res=await window.studio.safePcMerge({project,pak});
    if(!res?.ok){hideTranslationProgress();alert(res?.error||'国际版迁移失败');$('#progress').textContent=`国际版迁移失败：${res?.error||'未知错误'}`;return}
    refreshRecordsFromResult(res);setTranslationProgress(100,`${pak} 国际版中文迁移完成`);stopTranslationClock();
    const r=res.report||{};const manuals=(r.manual_files||[]).map(x=>`${String(x.file||'').split(/[\\/]/).pop()}：${x.updated||0} 条（${x.status||''}）`).join('\n')||'当前 PAK 无人工 TSV';const coverage=r.coverage_after||{};
    const methods=Object.entries(r.migration_methods||{}).map(([name,count])=>`${name}: ${count}`).join('\n')||'无新增匹配';
    const skipped=(r.materialized||[]).reduce((sum,item)=>sum+Number(item.skipped_count||0),0);
    $('#progress').textContent=`${pak} 迁移完成：国际版 ${r.pc_safe_merged||0} 条，人工 ${r.manual_merged||0} 条，风险跳过 ${skipped} 条`;
    alert(`国际版迁移完成。\n\n当前 PAK：${pak}\n国际版可靠中文：${r.pc_safe_merged||0} 条\n人工 TSV：${r.manual_merged||0} 条\n合计更新：${r.total_changed||0} 条\n纯中文覆盖率：${coverage.percent??'--'}%\n跨文件关系：${r.cross_file_relations||0} 组\n冲突跳过：${r.conflicts_skipped||0} 条\n结构风险跳过：${skipped} 条\n写入可信 TM：${r.translation_memory?.learned||0} 条\n国际版缓存：${r.pc_root||''}\n\n匹配方法：\n${methods}\n\n人工文件：\n${manuals}\n\n备份：${r.backup||''}\n对齐报告：${r.alignment_report_dir||''}\n报告：${r.report||''}\n\n当前 PAK 的 modified 已刷新，现在可以直接点击“构建 PAK”。`,{tone:'success'})
  }finally{setWorkflowDisabled(false);btn.disabled=false}
};

$('#buildPak').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;clearTimeout(persistTimer);persistTimer=null;const saved=await persistRecords();if(!saved?.ok){alert(saved?.error||'保存当前编辑失败，已取消构建');return}const syncSkipped=Number(saved.sync?.report?.skipped_count||0);if(syncSkipped){const details=syncFailureDetails(saved.sync?.report);focusSyncFailure(saved.sync?.report);const choice=await alert(`有 ${syncSkipped} 条界面文本尚未同步，已取消构建。\n\n${details}${syncSkipped>20?'\n\n仅显示前 20 条。':''}\n\n可一键将本次全部错误项恢复为原文，正常译文不会受影响；也可以关闭弹窗后逐条补齐标记。`,{title:'文本格式需要修复',actionText:'一键恢复全部',tone:'error'});if(choice==='action'){const restored=await restoreSyncFailures(saved.sync?.report);if(!restored?.ok){await alert(restored?.error||'批量恢复失败',{title:'恢复失败',tone:'error'});return}if(restored.remaining){await alert(`已恢复 ${restored.restored} 条错误文本，但同步后仍有 ${restored.remaining} 条格式错误。\n\n请再次点击“构建 PAK”查看剩余项目。`,{title:'部分恢复完成',tone:'warning'});return}$('#progress').textContent=`已一键恢复并同步 ${restored.restored} 条格式错误文本`;await alert(`已恢复本次全部 ${restored.restored} 条格式错误文本。${restored.missing?`\n另有 ${restored.missing} 条记录已失效，未处理。`:''}\n\n正常译文未被修改，现在可以重新构建 PAK。`,{title:'恢复完成',tone:'success'})}return}const review=await window.studio.apiReviewStatus({project,pak});if(review?.started&&!review.complete){const proceed=await appConfirm(`API 审校尚未完成。\n\n待审校/失败：${review.pending||0} 条\n本轮已通过：${review.passed||0} 条\n\n现在构建只能包含已经保存到文本记录中的结果，其余内容仍是旧译文。\n建议先点击“继续审校”并等待完成。\n\n仍要构建当前部分结果吗？`,{title:'审校未完成',confirmText:'仍要构建'});if(!proceed){$('#progress').textContent='已取消构建：请先完成 API 审校';return}}$('#buildPak').disabled=true;$('#progress').textContent='正在把最新文本写回资源并构建 PAK…';const res=await window.studio.buildPak({project,pak});$('#buildPak').disabled=false;if(!res?.ok){alert(res?.error||'构建 PAK 失败');$('#progress').textContent=`构建失败：${res?.error||'未知错误'}`;return}const builtAt=new Date().toLocaleString();const repaired=res.report?.auto_repair?.repaired_count||0;$('#progress').textContent=`PAK 构建完成（${builtAt}）：修改 ${res.report.changed_entries} 个条目，自动修复 ${repaired} 处 → ${res.report.output}`;alert(`PAK 构建完成。\n\nPAK：${pak}\n构建时间：${builtAt}\n修改条目：${res.report.changed_entries}\n自动修复：${repaired} 处\n校验：${res.report.roundtrip==='skipped'?'快速构建':'通过'}\n\n请替换这个新文件：\n${res.report.output}`)};
$('#pushGithubPak').onclick=async()=>{const pak=selectedPakOrWarn();if(!pak||!project)return;if(!await appConfirm(`将把已构建的 ${pak} 推送到 PakRedirect 仓库的 pak 目录，并自动更新 linkspak.txt 中的文件大小。\n\n请确认你已经先点击“构建 PAK”，并且构建结果是要发布的版本。`,{title:'推送 GitHub',confirmText:'开始推送'}))return;const btn=$('#pushGithubPak');btn.disabled=true;$('#progress').textContent=`正在准备推送 ${pak} 到 GitHub…`;try{const res=await window.studio.pushGithubPak({project,pak});if(!res?.ok){alert(res?.error||'GitHub 推送失败');$('#progress').textContent=`GitHub 推送失败：${res?.error||'未知错误'}`;return}const summary=res.alreadyUpToDate?'远端已是最新版本':(res.createdCommit?'已创建提交并推送':'已推送此前未上传的本地提交');$('#progress').textContent=`GitHub 推送完成：${pak} ${res.size} bytes -> ${res.branch}`;alert(`推送完成。\n\n状态：${summary}\nPAK：${pak}\n大小：${res.size} bytes\n本地构建：${res.source}\n仓库文件：${res.target}\nlinkspak：${res.linkspak}\n分支：${res.branch}\n提交：${res.commit||'读取失败'}`)}catch(e){alert(e.message||String(e));$('#progress').textContent=`GitHub 推送失败：${e.message||e}`}finally{btn.disabled=false}};
// Replace the legacy single-PAK button action with the complete project-wide build.
$('#buildPak').onclick=async()=>{
  if(!project)return;
  const paks=[...new Set((project.paks||[]).map(item=>item.pak).filter(Boolean))];
  if(!paks.length){alert('当前项目没有可构建的 PAK');return}
  clearTimeout(persistTimer);persistTimer=null;
  const saved=await window.studio.persistRecords({workspace:project.workspace,records,project,pak:null});
  if(!saved?.ok){alert(saved?.error||'保存当前编辑失败，已取消构建');return}
  const btn=$('#buildPak');btn.disabled=true;setWorkflowDisabled(true);
  $('#progress').textContent=`正在依次构建 ${paks.length} 个 PAK…`;
  try{
    const res=await window.studio.buildPaks({project,paks});
    if(!res?.ok){alert(res?.error||'批量构建失败',{title:'批量构建未完成',tone:'error'});$('#progress').textContent=`批量构建失败：${res?.error||'未知错误'}`;return}
    const rows=(res.results||[]).map(item=>`${item.pak}：修改 ${item.report?.changed_entries||0} 个条目 → ${item.output||item.report?.output||''}`);
    $('#progress').textContent=`全部 ${rows.length} 个 PAK 构建完成`;
    alert(`全部 PAK 构建完成。\n\n${rows.join('\n')}\n\n汉化率不是构建门槛；未翻译文本会保留原文。结构损坏才会被自动恢复或阻止写入。`,{title:'批量构建完成',tone:'success'});
  }finally{setWorkflowDisabled(false);btn.disabled=false}
};

function parseCsv(text){const out=[];let row=[],field='',q=false;for(let i=0;i<text.length;i++){const c=text[i];if(q){if(c==='"'&&text[i+1]==='"'){field+='"';i++}else if(c==='"')q=false;else field+=c}else{if(c==='"')q=true;else if(c===','){row.push(field);field=''}else if(c==='\n'){row.push(field.replace(/\r$/,''));out.push(row);row=[];field=''}else field+=c}}if(field.length||row.length){row.push(field.replace(/\r$/,''));out.push(row)}return out.filter(r=>r.some(x=>x!==''))}
