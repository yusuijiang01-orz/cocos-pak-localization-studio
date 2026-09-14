const { app, BrowserWindow, dialog, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn } = require('child_process');
const os = require('os');

let win;
const crashLog=path.join(__dirname,'..','studio-crash.log');
function logMainFailure(kind,error){
  try{fs.appendFileSync(crashLog,`[${new Date().toISOString()}] ${kind}: ${error?.stack||error}\n`,'utf8')}catch{}
}
process.on('uncaughtException',error=>logMainFailure('uncaughtException',error));
process.on('unhandledRejection',error=>logMainFailure('unhandledRejection',error));
const activeBackendProcesses = new Set();
const hasSingleInstanceLock = app.requestSingleInstanceLock();
if(!hasSingleInstanceLock) app.quit();
app.on('second-instance', () => {
  if(!win) return;
  if(win.isMinimized()) win.restore();
  win.show();
  win.focus();
});
function createWindow() {
  win = new BrowserWindow({
    width: 1480, height: 900, minWidth: 1100, minHeight: 700,
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false }
  });
  win.loadFile(path.join(__dirname, '..', 'renderer', 'index.html'));
}
if(hasSingleInstanceLock) app.whenReady().then(createWindow);
app.on('window-all-closed', () => { if (process.platform !== 'darwin') app.quit(); });
app.on('before-quit', () => {
  for(const proc of Array.from(activeBackendProcesses)) {
    try { proc.__cancelled=true; proc.kill(); } catch {}
  }
  activeBackendProcesses.clear();
});

function pythonCommand() {
  const root=path.join(__dirname,'..');
  const isolated=process.platform==='win32'
    ? path.join(root,'.venv','Scripts','python.exe')
    : path.join(root,'.venv','bin','python');
  const parent=process.platform==='win32'
    ? path.join(root,'..','.venv','Scripts','python.exe')
    : path.join(root,'..','.venv','bin','python');
  return fs.existsSync(isolated) ? isolated : fs.existsSync(parent) ? parent : (process.platform === 'win32' ? 'python' : 'python3');
}

function pythonSpawnOptions() {
  const usableCpus=Math.max(1,(os.cpus()?.length || 1)-4);
  return {
    windowsHide:true,
    env:{...process.env,PYTHONIOENCODING:'utf-8',PYTHONUTF8:'1',
      OMP_NUM_THREADS:String(usableCpus),MKL_NUM_THREADS:String(usableCpus),
      OPENBLAS_NUM_THREADS:String(usableCpus),NUMEXPR_NUM_THREADS:String(usableCpus),
      VECLIB_MAXIMUM_THREADS:String(usableCpus)}
  };
}

function pakItem(project, pakName) {
  return (project.paks || []).find(x => x.pak === pakName) || (project.paks || [])[0];
}

function playerVisibleXlsxRoot(workspace, base) {
  return path.join(workspace,'xlsx_export',`${base}_player_visible`);
}

function multiPakXlsxRoot(workspace) {
  return path.join(workspace,'xlsx_export','all_paks_player_visible');
}

function rawReferenceDir(project, item, pakName) {
  const base=(pakName || item?.pak || '').replace(/\.pak$/i,'');
  const candidates=[
    item?.raw_reference,
    item?.rawReference,
    project?.workspace && base ? path.join(project.workspace,'_raw_reference',base) : '',
    item?.extracted,
  ].filter(Boolean);
  return candidates.find(candidate => fs.existsSync(candidate)) || item?.extracted;
}

function runGit(repoDir, args) {
  return new Promise((resolve) => {
    const proc = spawn('git', args, { cwd: repoDir, windowsHide: true });
    let stdout = '';
    let stderr = '';
    proc.stdout.on('data', d => { stdout += d.toString(); });
    proc.stderr.on('data', d => { stderr += d.toString(); });
    proc.on('error', e => resolve({ ok: false, code: -1, stdout, stderr: e.message }));
    proc.on('close', code => resolve({ ok: code === 0, code, stdout, stderr }));
  });
}

function updateLinkspakSize(linkspakPath, pakName, size) {
  if(!fs.existsSync(linkspakPath)) throw new Error(`未找到 linkspak.txt：${linkspakPath}`);
  const raw = fs.readFileSync(linkspakPath, 'utf8');
  const eol = raw.includes('\r\n') ? '\r\n' : '\n';
  let found = false;
  const lines = raw.split(/\r?\n/).map(line => {
    if(!line.trim()) return line;
    const cols = line.split(',');
    if((cols[2] || '').trim().toLowerCase() !== pakName.toLowerCase()) return line;
    if(cols.length < 4) throw new Error(`linkspak.txt 中 ${pakName} 对应行格式不完整`);
    cols[3] = String(size);
    found = true;
    return cols.join(',');
  });
  if(!found) throw new Error(`linkspak.txt 中没有找到 ${pakName} 对应行`);
  fs.writeFileSync(linkspakPath, lines.join(eol), 'utf8');
}

function defaultPakRedirectRepo() {
  return path.join(__dirname, '..', 'github push', 'PakRedirect');
}

function readPakHeaderSummary(filePath) {
  if(!filePath || !fs.existsSync(filePath)) return null;
  const fd=fs.openSync(filePath,'r');
  try {
    const header=Buffer.alloc(12);
    if(fs.readSync(fd,header,0,12,0)!==12) return null;
    const signature=header.subarray(0,4).toString('ascii');
    if(signature!=='PACK' && signature!=='PAK ') return null;
    return {
      path:filePath,
      size:fs.statSync(filePath).size,
      signature,
      count:header.readUInt32LE(4),
      indexOffset:header.readUInt32LE(8),
    };
  } finally {
    fs.closeSync(fd);
  }
}

function readExtractedManifestSummary(extractedDir) {
  const manifestPath=path.join(extractedDir || '', 'manifest.json');
  if(!fs.existsSync(manifestPath)) return null;
  const manifest=JSON.parse(fs.readFileSync(manifestPath,'utf8'));
  const header=manifest.header || {};
  return {
    manifestPath,
    source:manifest.source || '',
    size:Number(manifest.archive_size || 0),
    count:Number(header.count || 0),
    indexOffset:Number(header.index_offset || 0),
  };
}

function pakSummaryMatches(a,b) {
  return Boolean(a && b && a.size===b.size && a.count===b.count && a.indexOffset===b.indexOffset);
}

function sameFileBytes(a,b) {
  if(!a || !b || !fs.existsSync(a) || !fs.existsSync(b)) return false;
  const sa=fs.statSync(a), sb=fs.statSync(b);
  if(sa.size!==sb.size) return false;
  const crypto=require('crypto');
  const digest=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
  return digest(a)===digest(b);
}

function normalizeProjectPakSnapshots(project, projectFile=null) {
  if(!project || !project.workspace || !Array.isArray(project.paks)) return {changed:false,fixes:[]};
  const fixes=[];
  const originalsRoot=path.join(project.workspace,'original_paks');
  fs.mkdirSync(originalsRoot,{recursive:true});
  for(const item of project.paks) {
    if(!item || !item.pak || !item.extracted) continue;
    const expected=readExtractedManifestSummary(item.extracted);
    if(!expected) continue;
    const snapshot=path.join(originalsRoot,path.basename(item.pak));
    const current=readPakHeaderSummary(item.path);
    const existingSnapshot=readPakHeaderSummary(snapshot);
    // Size/count/index alone are not an identity check: a rebuilt PAK can have
    // exactly the same summary as its source.  Never silently bless a build
    // output as the immutable original snapshot.
    const buildOutput=path.join(project.workspace,'build',path.basename(item.pak));
    const snapshotIsBuild=sameFileBytes(snapshot,buildOutput);
    if(pakSummaryMatches(existingSnapshot,expected) && !snapshotIsBuild) {
      if(path.normalize(item.path || '')!==path.normalize(snapshot)) {
        item.source_path=item.source_path || item.path || expected.source || '';
        item.path=snapshot;
        fixes.push(`${item.pak}: 使用项目内原包快照`);
      }
      continue;
    }
    if(pakSummaryMatches(current,expected) && !sameFileBytes(item.path,buildOutput)) {
      fs.copyFileSync(item.path,snapshot);
      item.source_path=item.source_path || item.path;
      item.path=snapshot;
      fixes.push(`${item.pak}: 已把导入原包复制为项目快照`);
      continue;
    }
    const source=readPakHeaderSummary(item.source_path);
    if(pakSummaryMatches(source,expected)) {
      fs.copyFileSync(item.source_path,snapshot);
      item.path=snapshot;
      fixes.push(`${item.pak}: 已从 source_path 恢复项目快照`);
      continue;
    }
  }
  if(fixes.length && projectFile) {
    fs.writeFileSync(projectFile,JSON.stringify(project,null,2),'utf8');
  }
  return {changed:fixes.length>0,fixes};
}

function dbPath() {
  return path.join(__dirname, '..', 'localization.db');
}

function apiReviewStatus(project, pak) {
  const base=String(pak || '').replace(/\.pak$/i,'');
  const dir=path.join(project.workspace,'api_review',base);
  const checkpointPath=path.join(dir,'review_checkpoint.json');
  const reportPath=path.join(dir,'review_report.json');
  if(!fs.existsSync(checkpointPath)) return {started:false,complete:false,pending:0,passed:0,failed:0};
  try {
    const checkpoint=JSON.parse(fs.readFileSync(checkpointPath,'utf8'));
    const counts={};
    for(const item of Object.values(checkpoint.items || {})) counts[item.status]=(counts[item.status] || 0)+1;
    const checkpointMtime=fs.statSync(checkpointPath).mtimeMs;
    const report=fs.existsSync(reportPath) ? JSON.parse(fs.readFileSync(reportPath,'utf8')) : null;
    const reportMtime=fs.existsSync(reportPath) ? fs.statSync(reportPath).mtimeMs : 0;
    const pending=(counts['待审校'] || 0)+(counts['失败'] || 0);
    return {
      started:true,
      complete:Boolean(report && reportMtime >= checkpointMtime && Number(report.remaining || 0) === 0),
      pending,
      passed:counts['通过'] || 0,
      failed:counts['失败'] || 0,
      checkpoint:checkpointPath,
      report:reportPath,
      updatedAt:new Date(checkpointMtime).toISOString(),
    };
  } catch(e) {
    return {started:true,complete:false,pending:0,passed:0,failed:0,error:e.message,checkpoint:checkpointPath};
  }
}

function modelPath() {
  const local=path.join(__dirname, '..', 'models', 'nllb-200-distilled-600M');
  const parent=path.join(__dirname, '..', '..', 'models', 'nllb-200-distilled-600M');
  return fs.existsSync(local) ? local : parent;
}

function apiConfigPath() {
  return path.join(app.getPath('userData'), 'api-translator-config.json');
}

function promptTempPath() {
  return path.join(app.getPath('userData'), 'api-translator-prompt.txt');
}

function defaultApiTranslatorConfig() {
  const glossary = 'Máy chủ=服务器; Nhiệm vụ=任务; Đẳng cấp=等级; Cấp=等级; Kinh nghiệm=经验;' +
    'Trang bị=装备; Kỹ năng=技能; Môn phái=门派; Vũ khí=武器; Vật phẩm=物品; Đạo cụ=道具;' +
    'Thuộc tính=属性; Tấn công=攻击; Phòng thủ=防御; Phòng ngự=防御; Nội lực=内力;' +
    'Pháp lực=法力; Sinh lực=生命; Lực chiến=战力; Độ bền=耐久; Trọng lượng=负重;' +
    'Bang hội=帮会; Đội ngũ=队伍; Bạn bè=好友; Hệ thống=系统; Thiết lập=设置; Trợ giúp=帮助;' +
    'Đăng nhập=登录; Đăng ký=注册; Đăng xuất=退出登录; Thoát=退出; Kết nối=连接;' +
    'Tiếp tục=继续; Hủy bỏ=取消; Quay lại=返回; Xác nhận=确认; Đồng ý=同意;' +
    'Mua=购买; Bán=出售; Sử dụng=使用; Giao dịch=交易; Nhận thưởng=领取奖励;' +
    'Hoàn thành=完成; Chúc mừng=恭喜; Thăng cấp=升级; Chính phái=正派; Tà phái=邪派;' +
    'Thiếu Lâm=少林; Thiên Vương=天王; Đường Môn=唐门; Na Tra=哪吒; Khương Tử Nha=姜子牙;' +
    'Dương Tiễn=杨戬; Lôi Chấn Tử=雷震子; Nữ Oa=女娲; Trụ Vương=纣王; Đắc Kỷ=妲己; Xi Vưu=蚩尤;' +
    'Đát Kỷ=妲己; Từ Hàng Đạo Nhân=慈航道人; Văn Trọng=闻仲; Bá Ấp Khảo=伯邑考; Tỷ Can=比干; Bích Tiêu=碧霄; Ngao Quảng=敖广; Long Cát=龙吉;' +
    'Nguyên Thần=元神; Linh Tướng=灵将; Danh Thần=神将; Thần Khí=神器; Pháp Bảo=法宝; Pháp Bảo Tinh Phách=法宝精魄; Kỹ năng Pháp Tượng=法相技能; Ngũ Hành=五行;' +
    'Tông Môn=宗门; Hệ phái=流派; Trân Bảo Các=珍宝阁; Đấu giá=拍卖; Thập Tuyệt Trận=十绝阵; Thiên Cực Chiến=天极战; Sách Phong Thần=封神书册; Chú Ấn=符印; Tu Luyện=修炼;' +
    'Nguyên Bảo=元宝; Huyền Tinh=玄晶; Linh Thạch=灵石; Nguyên liệu=材料; Phẩm chất=品质; Thuộc tính chính=主属性; Thuộc tính phụ=副属性;' +
    'Sát thương=伤害; Pháp công=法攻; Vật công=物攻; Né tránh=闪避; Hồi phục=回复; Trị liệu=治疗; Bạo kích=暴击; Tê liệt=麻痹;' +
    'Khống chế=控制; Miễn khống chế=免疫控制; Lá chắn=护盾; Xác suất=概率; Chính xác=精准; Phá phòng=破防; Cấp sao=星级; Cường hóa=强化; Tăng cấp=升级;' +
    'Quái=怪物; Quái thường=普通怪; Quái tinh anh=精英怪; Kiếm Xỉ Hổ=剑齿虎; Kiếm Xỉ Hổ Vương=剑齿虎王; Bản Đồ=地图; Bản Đồ Luyện Cấp=练级地图;' +
    'Hỏa Tiêm Thương=火尖枪; Hỗn Thiên Lăng=混天绫; Càn Khôn Quyển=乾坤圈; Thanh Tịnh Lưu Ly Bình=清净琉璃瓶; Tam Tiêm Lưỡng Nhận Đao=三尖两刃刀; Kim Giao Tiễn=金蛟剪;' +
    'Bình Lưu Ly=琉璃瓶; Càn Khôn khuyên=乾坤圈; Ngũ Quang Thạch=五光石; Hình Thiên ấn=刑天印; Càn Khôn Xích=乾坤尺; Hỏa Long Tiêu=火龙镖; Âm Dương Kính=阴阳镜; Túi Ngô Phong=蜈蜂袋; Bích Tỳ Bà=碧琵琶; Kim Cang Phách=金刚帕; Thái Dương Châm=太阳针; Bàn Cổ phướn=盘古幡; Dây Phược Long=缚龙索; ấm Vạn Nha=万鸦壶; Dây Khổn Tiên=捆仙绳; Chấn Thiên Cung=震天弓; Kim Bát Vu=金钵盂; Linh Lung Tháp=玲珑塔; Toàn Tâm Đinh=攒心钉; Hồng Hồ Lô=红葫芦; Lạc Hồn Chung=落魂钟; Ngọc Hư Phù=玉虚符; Cọc Độn Long=遁龙桩; Kính Chiếu Yêu=照妖镜; Phong Hỏa Luân=风火轮; Kim Quang Tỏa=金光锉; Hạnh Hoàng Kỳ=杏黄旗; Bạch Cốt phướn=白骨幡; Định Phong Châu=定风珠;' +
    'Thắp sáng=点亮; Tách=分解; Phân Giải=分解; Trang Trí=装饰; Liên kết=链接';
  const prompt = `你是资深游戏本地化翻译员，负责把越南语武侠/仙侠/封神题材 MMORPG 的 UI 文本与剧情对白，翻译成符合中国大陆玩家阅读习惯的简体中文。

核心术语表（全文必须严格遵守，保证术语统一，不得另译或音译替换）：
${glossary}

翻译要求：
1. 只输出译文，不解释、不追加任何说明。
2. 完整保留原文字符中的所有占位符与特殊符号（如 ◈1◈、♥ ♣ ♦ ♠ ★ ☆ ● ■ ▲ ◆ ※ ◎ ◇ □ △ ▽），数量与顺序必须与原文一致。◈数字◈ 必须逐字原样输出，绝不能改成大括号、书名号、圆括号或全角符号。
3. 完整保留代码、函数名、变量名、URL、文件路径、数字、日期和格式标记，不翻译、不修改字符、顺序或大小写。
4. UI 按钮与标签要精简，例如 Chơi ngay 译为 立即游玩，Bạn quên mật khẩu? 译为 忘记密码？
5. 剧情对白采用仙侠/武侠国风口吻，自然流畅，避免机翻腔与逐字硬译；人名、地名、门派、装备、技能名优先套用术语表，不要生造。
6. 同一术语全文保持一致译法。
7. 严格只返回 JSON 数组，格式：[{"id":"1","text":"译文"}]，id 必须与输入逐条对应且不得遗漏。`;
  const profile = {
    id: 'qwen-cloud',
    name: 'Qwen3.6 云端',
    baseUrl: '',
    apiKey: '',
    model: '',
    models: [],
    batchSize: 0,
    reviewMode: 'risk',
    prompt
  };
  return {
    activeProfileId: profile.id,
    profiles: [
      profile,
      {...profile,id:'local-31415',name:'本机 31415',baseUrl:'http://127.0.0.1:31415/v1'},
      {...profile,id:'ollama',name:'Ollama 本地',baseUrl:'http://127.0.0.1:11434/v1',apiKey:'',model:'',models:[]},
    ],
  };
}

function normalizeApiTranslatorConfig(raw) {
  const defaults=defaultApiTranslatorConfig();
  if(!raw || typeof raw!=='object') return defaults;
  if(Array.isArray(raw.profiles)) {
    const profiles=raw.profiles.map((p,i)=>({
      ...defaults.profiles[0],
      id: String(p.id || `profile-${i+1}`),
      name: String(p.name || p.model || `配置 ${i+1}`),
      ...p,
      model: String(p.model || ''),
      models: Array.isArray(p.models) ? p.models.filter(Boolean).map(String) : [],
      batchSize: Number.isFinite(Number(p.batchSize)) ? Number(p.batchSize) : 0,
      reviewMode: p.reviewMode === 'all' ? 'all' : 'risk',
      think: p.think === true,
    })).filter(p=>p.id);
    if(!profiles.length) return defaults;
    const activeProfileId=profiles.some(p=>p.id===raw.activeProfileId) ? raw.activeProfileId : profiles[0].id;
    return {activeProfileId,profiles};
  }
  const migrated={...defaults.profiles[0],...raw,id:'default',name:raw.name || raw.model || '默认配置'};
  migrated.model=String(raw.model || '');
  migrated.models=Array.isArray(raw.models)?raw.models.filter(Boolean).map(String):(migrated.model?[migrated.model]:[]);
  return {activeProfileId:migrated.id,profiles:[migrated]};
}

function activeApiProfile(config) {
  const normalized=config && Array.isArray(config.profiles) ? config : normalizeApiTranslatorConfig(config);
  return normalized.profiles.find(p=>p.id===normalized.activeProfileId) || normalized.profiles[0];
}

function readApiTranslatorConfig() {
  try {
    if(!fs.existsSync(apiConfigPath())) return defaultApiTranslatorConfig();
    return normalizeApiTranslatorConfig(JSON.parse(fs.readFileSync(apiConfigPath(),'utf8')));
  } catch {
    return defaultApiTranslatorConfig();
  }
}

function writeApiTranslatorConfig(config) {
  const next=normalizeApiTranslatorConfig(config);
  fs.mkdirSync(path.dirname(apiConfigPath()),{recursive:true});
  fs.writeFileSync(apiConfigPath(),JSON.stringify(next,null,2),'utf8');
  return next;
}

function recordEditsPath(workspace) {
  return path.join(workspace,'localization','record_edits.jsonl');
}

function readRecords(workspace) {
  const records=JSON.parse(fs.readFileSync(path.join(workspace, 'localization', 'text_records.json'), 'utf8'));
  const editsPath=recordEditsPath(workspace);
  if(!fs.existsSync(editsPath)) return records;
  const byId=new Map(records.map(record=>[String(record.id),record]));
  for(const line of fs.readFileSync(editsPath,'utf8').split(/\r?\n/)) {
    if(!line.trim()) continue;
    try {
      const edit=JSON.parse(line);
      const record=byId.get(String(edit.id));
      if(record) Object.assign(record,edit);
    } catch {}
  }
  return records;
}

function newestFileMtime(target) {
  if(!fs.existsSync(target)) return 0;
  const st=fs.statSync(target);
  if(st.isFile()) return st.mtimeMs;
  let newest=st.mtimeMs;
  for(const name of fs.readdirSync(target)) {
    const child=path.join(target,name);
    const cst=fs.statSync(child);
    if(cst.isDirectory()) newest=Math.max(newest,newestFileMtime(child));
    else newest=Math.max(newest,cst.mtimeMs);
  }
  return newest;
}

function needsRecordMaterialize(project, modifiedDir) {
  const currentEncoding='force-utf8-v8';
  const recordsPath=path.join(project.workspace,'localization','text_records.json');
  if(!fs.existsSync(recordsPath)) return false;
  if(!fs.existsSync(modifiedDir)) return true;
  const xlsxImportReport=path.join(modifiedDir,'_xlsx_localization_import_report.json');
  const reportPath=path.join(modifiedDir,'_records_materialize_report.json');
  const reports=[xlsxImportReport,reportPath]
    .filter(candidate => fs.existsSync(candidate))
    .sort((a,b) => fs.statSync(b).mtimeMs-fs.statSync(a).mtimeMs);
  // A report-less modified directory may have been supplied manually. Keep it
  // intact; only managed output trees are regenerated from text_records.json.
  if(!reports.length) return !hasBuildableEntryFiles(modifiedDir);
  try {
    const newestReport=reports[0];
    const report=JSON.parse(fs.readFileSync(newestReport,'utf8'));
    // text_records.json is the authoritative state shown by Studio.  Import,
    // safe merge and manual edits can update it after an XLSX modified tree was
    // produced.  Reusing that older tree made the UI show Chinese while build-pak
    // silently packed the stale Vietnamese files.
    if(fs.statSync(recordsPath).mtimeMs > fs.statSync(newestReport).mtimeMs) return true;
    return report.encoding !== currentEncoding;
  } catch {
    return true;
  }
}

function hasBuildableEntryFiles(dir) {
  if(!fs.existsSync(dir)) return false;
  return fs.readdirSync(dir).some(name => /^\d+_/.test(name) && /\.(tsv|ini|txt|bin|lua|spr|jpg|png|dat)$/i.test(name));
}

const recordSyncQueues=new Map();
function recordsOutputDir(project,pak) {
  const base=pak.replace(/\.pak$/i,'');
  const modified=path.join(project.workspace,'modified',base);
  const legacy=path.join(project.workspace,'tsv_after',base);
  if(fs.existsSync(modified)) return modified;
  if(fs.existsSync(legacy)) return legacy;
  return modified;
}

function syncRecordsToResources(project,pak) {
  const key=`${project.workspace}\0${pak}`;
  const previous=recordSyncQueues.get(key) || Promise.resolve();
  const current=previous.catch(()=>null).then(async()=>{
    normalizeProjectPakSnapshots(project);
    const item=pakItem(project,pak);
    if(!item?.extracted) throw new Error(`没有找到 ${pak} 的解包目录`);
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    if(!fs.existsSync(recordsPath)) throw new Error('没有找到 text_records.json');
    const outputDir=recordsOutputDir(project,pak);
    const result=await runCli(['materialize-records',item.extracted,recordsPath,pak,outputDir],{suppressProgress:true});
    if(!result.ok) throw new Error(result.error || '本地化文本同步失败');
    return {outputDir,report:result.report};
  });
  const queued=current.finally(()=>{if(recordSyncQueues.get(key)===queued)recordSyncQueues.delete(key)});
  recordSyncQueues.set(key,queued);
  return queued;
}

async function syncRecordsWithAutoReject(project,pak) {
  const recordsPath=path.join(project.workspace,'localization','text_records.json');
  let sync=await syncRecordsToResources(project,pak);
  let autoRejected=0;
  const repairBackups=[];
  for(let round=0;round<10 && Number(sync?.report?.skipped_count||0)>0;round++) {
    const reportPath=path.join(sync.outputDir,'_records_materialize_report.json');
    const repaired=await runCli(['restore-materialize-rejections',recordsPath,reportPath],{suppressProgress:true});
    if(!repaired.ok) throw new Error(repaired.error||'自动恢复结构错误译文失败');
    const count=Number(repaired.report?.restored||0);
    if(!count) break;
    autoRejected+=count;
    if(repaired.report?.backup) repairBackups.push(repaired.report.backup);
    sync=await syncRecordsToResources(project,pak);
  }
  return {...sync,autoRejected,repairBackups};
}

function ensureModifiedDirInitialized(extractedDir, modifiedDir) {
  if(!extractedDir || !fs.existsSync(extractedDir)) return {created:false, reason:'missing extracted'};
  if(fs.existsSync(modifiedDir) && hasBuildableEntryFiles(modifiedDir)) return {created:false, reason:'already exists'};
  fs.mkdirSync(path.dirname(modifiedDir), {recursive:true});
  if(fs.existsSync(modifiedDir)) fs.rmSync(modifiedDir, {recursive:true, force:true});
  fs.cpSync(extractedDir, modifiedDir, {recursive:true});
  return {created:true, reason:'copied extracted'};
}

function countChangedEntryFiles(originalDir, modifiedDir) {
  if(!fs.existsSync(originalDir) || !fs.existsSync(modifiedDir)) return 0;
  let changed=0;
  for(const name of fs.readdirSync(modifiedDir)) {
    if(!/^\d+_/.test(name) || !/\.(tsv|ini|txt|bin|lua|spr|jpg|png|dat)$/i.test(name)) continue;
    const src=path.join(originalDir,name);
    const dst=path.join(modifiedDir,name);
    if(!fs.existsSync(src)) { changed++; continue; }
    const srcStat=fs.statSync(src);
    const dstStat=fs.statSync(dst);
    if(srcStat.size!==dstStat.size || !fs.readFileSync(src).equals(fs.readFileSync(dst))) changed++;
  }
  return changed;
}

function runCli(args, options={}) {
  return new Promise((resolve) => {
    const cli = path.join(__dirname, '..', 'backend', 'studio_cli.py');
    const proc = spawn(pythonCommand(), [cli, ...args], pythonSpawnOptions());
    activeBackendProcesses.add(proc);
    let stderr=''; let done=null; let buf='';
    proc.stdout.setEncoding('utf8'); proc.stderr.setEncoding('utf8');
    proc.stdout.on('data', chunk => {
      buf += chunk;
      const lines = buf.split(/\r?\n/); buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.event === 'progress' && !options.suppressProgress) win.webContents.send('backend-progress', obj);
          else if (obj.event === 'done') done = obj;
          else if (obj.event === 'error') { done = obj; win.webContents.send('backend-progress', obj); }
        } catch { win.webContents.send('backend-progress', {event:'progress',message:line}); }
      }
    });
    proc.stderr.on('data', x => stderr += x);
    proc.on('close', code => {
      activeBackendProcesses.delete(proc);
      if (proc.__cancelled) resolve({ok:false,canceled:true,error:'已取消当前任务'});
      else if (code !== 0 || !done || done.event === 'error') resolve({ok:false,error:done?.message || stderr || `Backend exited ${code}`,trace:done?.trace});
      else resolve({ok:true,...done});
    });
  });
}

ipcMain.handle('cancel-current-task', async () => {
  let count = 0;
  for (const proc of Array.from(activeBackendProcesses)) {
    try {
      proc.__cancelled = true;
      proc.kill('SIGTERM');
      count++;
    } catch {}
  }
  win?.webContents.send('backend-progress', {event:'progress', phase:'cancel', message:`正在取消当前任务…已请求停止 ${count} 个后端进程`});
  return {ok:true,count};
});

ipcMain.handle('api-review-status', async (_e,{project,pak}) => apiReviewStatus(project,pak));

ipcMain.handle('choose-paks', async () => {
  const r = await dialog.showOpenDialog(win, { properties: ['openFile','multiSelections'], filters: [{name:'PAK files',extensions:['pak']},{name:'All files',extensions:['*']}] });
  return r.canceled ? [] : r.filePaths;
});
ipcMain.handle('choose-workspace', async () => {
  const r = await dialog.showOpenDialog(win, { properties: ['openDirectory','createDirectory'] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle('import-paks', async (_e, { workspace, paks }) => {
  return await new Promise((resolve) => {
    const cli = path.join(__dirname, '..', 'backend', 'studio_cli.py');
    const proc = spawn(pythonCommand(), [cli, 'import', workspace, ...paks], pythonSpawnOptions());
    activeBackendProcesses.add(proc);
    let stderr=''; let done=null;
    proc.stdout.setEncoding('utf8'); proc.stderr.setEncoding('utf8');
    let buf='';
    proc.stdout.on('data', chunk => {
      buf += chunk;
      const lines = buf.split(/\r?\n/); buf = lines.pop();
      for (const line of lines) {
        if (!line.trim()) continue;
        try {
          const obj = JSON.parse(line);
          if (obj.event === 'progress') win.webContents.send('backend-progress', obj);
          else if (obj.event === 'done') done = obj;
          else if (obj.event === 'error') { done = obj; win.webContents.send('backend-progress', obj); }
        } catch { win.webContents.send('backend-progress', {event:'progress',message:line}); }
      }
    });
    proc.stderr.on('data', x => stderr += x);
    proc.on('close', code => {
      activeBackendProcesses.delete(proc);
      if (proc.__cancelled) resolve({ok:false,canceled:true,error:'已取消当前任务'});
      else if (code !== 0 || !done || done.event === 'error') resolve({ok:false,error:done?.message || stderr || `Backend exited ${code}`,trace:done?.trace});
      else {
        try {
          const records = JSON.parse(fs.readFileSync(done.records_path,'utf8'));
          resolve({ok:true,project:done.project,records});
        } catch (e) { resolve({ok:false,error:e.message}); }
      }
    });
  });
});
ipcMain.handle('load-project', async () => {
  const r = await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'Studio Project',extensions:['json']}]});
  if (r.canceled) return null;
  try {
    const project = JSON.parse(fs.readFileSync(r.filePaths[0],'utf8'));
    const snapshotFix=normalizeProjectPakSnapshots(project,r.filePaths[0]);
    const records = readRecords(project.workspace);
    return {ok:true,project,records,snapshotFix};
  } catch (e) { return {ok:false,error:e.message}; }
});
ipcMain.handle('save-csv', async (_e,{csvText,defaultName}) => {
  const r=await dialog.showSaveDialog(win,{defaultPath:defaultName || 'localization.csv',filters:[{name:'CSV',extensions:['csv']}]});
  if(r.canceled) return null;
  fs.writeFileSync(r.filePath,'\ufeff'+csvText,'utf8'); return r.filePath;
});
ipcMain.handle('load-csv', async () => {
  const r=await dialog.showOpenDialog(win,{properties:['openFile'],filters:[{name:'CSV',extensions:['csv']}]});
  if(r.canceled) return null;
  try { let text=fs.readFileSync(r.filePaths[0],'utf8'); if(text.charCodeAt(0)===0xFEFF)text=text.slice(1); return {ok:true,path:r.filePaths[0],text}; }
  catch(e){ return {ok:false,error:e.message}; }
});
let skipNextPersistAfterOllama = false;
ipcMain.handle('persist-records', async (_e,{workspace,records,project,pak}) => {
  try {
    if(skipNextPersistAfterOllama){
      skipNextPersistAfterOllama=false;
      return {ok:true,skipped:'ollama-import-is-authoritative'};
    }
    if(!workspace) throw new Error('Missing workspace');
    const p=path.join(workspace,'localization','text_records.json');
    fs.mkdirSync(path.dirname(p),{recursive:true});
    const temp=`${p}.tmp`;
    fs.writeFileSync(temp,JSON.stringify(records),'utf8');
    fs.renameSync(temp,p);
    fs.rmSync(recordEditsPath(workspace),{force:true});
    let sync=null;
    if(project?.workspace && pak) sync=await syncRecordsToResources(project,pak);
    return {ok:true,path:p,sync};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('persist-record-edit', async (_e,{workspace,record}) => {
  try {
    if(!workspace || !record?.id) throw new Error('Missing workspace or record');
    const p=recordEditsPath(workspace);
    fs.mkdirSync(path.dirname(p),{recursive:true});
    fs.appendFileSync(p,`${JSON.stringify(record)}\n`,'utf8');
    return {ok:true,path:p};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('sync-records', async (_e,{project,pak}) => {
  try {
    if(!project?.workspace || !pak) throw new Error('请先选择要同步的 PAK');
    const sync=await syncRecordsToResources(project,pak);
    return {ok:true,...sync};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('batch-translate', async (_e,{workspace,pak}) => {
  const res=await runCli(['batch-translate',workspace,pak,dbPath()]);
  if(!res.ok) return res;
  return {ok:true,result:res.result,records:readRecords(workspace)};
});
ipcMain.handle('learn-record', async (_e,{workspace,id}) => runCli(['learn-record',workspace,id,dbPath()]));
ipcMain.handle('learn-modified', async (_e,{workspace,pak,quality}) => runCli(['learn-modified',workspace,pak,dbPath(),quality || 'manual']));
ipcMain.handle('queue-prepare', async (_e,{workspace,pak}) => runCli(['queue-prepare',workspace,pak,dbPath()]));
ipcMain.handle('queue-stats', async (_e,{workspace,pak}) => runCli(['queue-stats',workspace,pak,dbPath()]));
ipcMain.handle('queue-list', async (_e,{workspace,pak,status,limit}) => runCli(['queue-list',workspace,pak,dbPath(),status || 'pending',String(limit || 500)]));
ipcMain.handle('queue-apply', async (_e,{workspace,pak,batch}) => {
  const res=await runCli(['queue-apply',workspace,pak,dbPath(),String(batch || 200)]);
  if(!res.ok) return res;
  return {ok:true,result:res.result,records:readRecords(workspace)};
});
ipcMain.handle('tm-stats', async () => runCli(['tm-stats',dbPath()]));
ipcMain.handle('model-status', async () => runCli(['model-status',modelPath()]));
ipcMain.handle('model-translate', async (_e,{workspace,pak,batch}) => {
  const res=await runCli(['model-translate',workspace,pak,dbPath(),modelPath(),String(batch || 24)]);
  if(!res.ok) return res;
  return {ok:true,result:res.result,records:readRecords(workspace)};
});
ipcMain.handle('export-tsv-csv', async (_e,{project,pak}) => {
  try {
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const outDir=path.join(project.workspace,'tsv_csv_before',base);
    const res=await runCli(['export-tsv-csv',item.extracted,outDir,pak]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,outputDir:outDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('export-xlsx', async (_e,{project,pak}) => {
  try {
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const outDir=playerVisibleXlsxRoot(project.workspace,base);
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    const xlsxDir=path.join(outDir,'xlsx');
    const configDir=path.join(outDir,'config');
    const args=['export-xlsx-files',item.extracted,xlsxDir,pak,recordsPath,configDir];
    const res=await runCli(args);
    if(!res.ok) return res;
    // Export is read-only. Never synchronously clone the entire extracted tree
    // here: updatefs contains thousands of binary assets and the copy can block
    // or kill Electron. Import/build materialize only changed text files later.
    const modifiedDir=path.join(project.workspace,'modified',base);
    const modifiedInit={created:false,reason:'export-is-read-only'};
    return {ok:true,report:res.report,outputDir:xlsxDir,configDir,modifiedDir,modifiedInit};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('export-full-xlsx', async (_e,{project}) => {
  try {
    const paks=[...new Set((project?.paks||[]).map(item=>item.pak).filter(Boolean))];
    if(!project?.workspace || !paks.length) throw new Error('当前项目没有可用的 PAK');
    const exportDir=multiPakXlsxRoot(project.workspace);
    const xlsxDir=path.join(exportDir,'full_xlsx');
    const configDir=path.join(exportDir,'config');
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    const exportBase='all_paks_player_visible_full';
    const refreshArgs=['refresh-visible-records',recordsPath];
    for(const pak of paks){const item=pakItem(project,pak);if(item?.extracted)refreshArgs.push(pak,item.extracted)}
    const refreshed=await runCli(refreshArgs);
    if(!refreshed.ok)return refreshed;
    const res=await runCli(['export-xlsx-multi-full',recordsPath,xlsxDir,exportBase,configDir,...paks]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,refresh:refreshed.report,records:readRecords(project.workspace),xlsxPath:res.report?.xlsx||path.join(xlsxDir,`${exportBase}_localization.xlsx`),outputDir:xlsxDir,configDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('import-xlsx-polish', async (_e,{project,pak}) => {
  try {
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const exportDir=playerVisibleXlsxRoot(project.workspace,base);
    const picked=await dialog.showOpenDialog(win,{title:'选择只包含译后 XLSX 的文件夹',defaultPath:path.join(exportDir,'xlsx'),properties:['openDirectory']});
    if(picked.canceled || !picked.filePaths.length) return {ok:false,canceled:true};
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    const xlsxFolder=picked.filePaths[0];
    const adjacentConfig=path.join(path.dirname(xlsxFolder),'config');
    const projectConfig=path.join(exportDir,'config');
    const configFolder=fs.existsSync(adjacentConfig)?adjacentConfig:(fs.existsSync(projectConfig)?projectConfig:null);
    const importArgs=['apply-xlsx-folder-records',recordsPath,xlsxFolder,pak];
    if(configFolder) importArgs.push(configFolder);
    const imported=await runCli(importArgs);
    if(!imported.ok) return imported;
    if(Number(imported.report?.failed_files||0)>0) {
      return {ok:false,error:`XLSX 文件夹只完成了部分导入：${imported.report.imported_files||0}/${imported.report.total_files||0}，失败 ${imported.report.failed_files||0} 个。已停止资源同步，请先处理失败文件。`,report:imported.report,records:readRecords(project.workspace)};
    }
    const sync=await syncRecordsWithAutoReject(project,pak);
    const importedChanges=Number(imported.report?.changed||0);
    const materialized=Number(sync?.report?.modified_records||0);
    const changedFiles=Number(sync?.report?.changed_file_count||0);
    const skipped=Number(sync?.report?.skipped_count||0);
    if(skipped>0) {
      return {ok:false,error:`自动恢复结构错误译文后仍有 ${skipped} 条无法写回资源。已阻止显示为完成。`,report:imported.report,sync:sync.report,records:readRecords(project.workspace)};
    }
    if(importedChanges>0 && (materialized===0 || changedFiles===0)) {
      return {ok:false,error:`XLSX 已写入 ${importedChanges} 条译文，但没有生成任何可构建资源。已判定整条链路失败，不允许继续构建。`,report:imported.report,sync:sync.report,records:readRecords(project.workspace)};
    }
    return {ok:true,import:imported.report,sync:sync?.report,autoRejected:sync.autoRejected||0,repairBackups:sync.repairBackups||[],records:readRecords(project.workspace),sourceFolder:xlsxFolder,configFolder:imported.report?.config_dir,modifiedDir:sync.outputDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('import-full-xlsx', async (_e,{project}) => {
  try {
    if(!project?.workspace) throw new Error('请先打开项目');
    const exportDir=multiPakXlsxRoot(project.workspace);
    const picked=await dialog.showOpenDialog(win,{
      title:'选择译后的完整 XLSX',
      defaultPath:path.join(exportDir,'full_xlsx'),
      properties:['openFile'],
      filters:[{name:'Excel 工作簿',extensions:['xlsx']}],
    });
    if(picked.canceled || !picked.filePaths.length) return {ok:false,canceled:true};
    const sourceXlsx=picked.filePaths[0];
    const workbookStem=path.basename(sourceXlsx,'.xlsx');
    // Google/Windows may append a translated/copy suffix. Resolve the original
    // Studio mapping without requiring the user to rename the translated file.
    const workbookStems=[workbookStem];
    let stripped=workbookStem;
    for(const suffix of [/_translated$/i,/_google(?:_translated)?$/i,/\s*-\s*translated$/i,/\s*\(\d+\)$/]) {
      stripped=stripped.replace(suffix,'');
      if(stripped&&!workbookStems.includes(stripped)) workbookStems.push(stripped);
    }
    const mappingNames=workbookStems.map(stem=>`${stem}_mapping.json`);
    const mappingCandidates=[];
    for(const mappingName of mappingNames) mappingCandidates.push(
      path.join(path.dirname(sourceXlsx),'config',mappingName),
      path.join(path.dirname(path.dirname(sourceXlsx)),'config',mappingName),
      path.join(exportDir,'config',mappingName),
    );
    const mappingJson=mappingCandidates.find(candidate=>fs.existsSync(candidate));
    if(!mappingJson) throw new Error(`找不到所选 XLSX 的配套映射：${mappingNames.join('、')}。请保留 Studio 导出时生成的 config 文件夹。`);
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    const imported=await runCli(['apply-xlsx-multi-records',recordsPath,sourceXlsx,mappingJson]);
    if(!imported.ok) return imported;
    const syncs=[];
    let autoRejected=0;
    for(const pak of imported.report?.paks||[]) {
      const sync=await syncRecordsWithAutoReject(project,pak);
      syncs.push({pak,...sync.report,outputDir:sync.outputDir});
      autoRejected+=Number(sync.autoRejected||0);
      if(Number(sync.report?.skipped_count||0)>0) {
        return {ok:false,error:`${pak} 自动恢复后仍有 ${sync.report.skipped_count} 条结构错误，已停止该 PAK 的资源同步。未翻译内容不会造成这个错误。`,report:imported.report,syncs,records:readRecords(project.workspace)};
      }
    }
    const sync={
      modified_records:syncs.reduce((n,x)=>n+Number(x.modified_records||0),0),
      changed_file_count:syncs.reduce((n,x)=>n+Number(x.changed_file_count||0),0),
      skipped_count:syncs.reduce((n,x)=>n+Number(x.skipped_count||0),0),
    };
    // Always leave the user with an exact, deduplicated second-pass workbook.
    // It contains only text that is still Vietnamese after this import; rows
    // already translated in the full workbook are intentionally omitted. Use a
    // unique name: never overwrite the Google-translated workbook just selected.
    const remainingDir=path.join(exportDir,'remaining_xlsx');
    const importStamp=new Date().toISOString().replace(/[-:]/g,'').replace(/\.\d{3}Z$/,'Z');
    const remainingBase=`all_paks_player_visible_remaining_${importStamp}`;
    const remainingConfig=path.join(exportDir,'config');
    const remaining=await runCli([
      'export-xlsx-multi-remaining',recordsPath,remainingDir,remainingBase,
      remainingConfig,...(imported.report?.paks||[]),
    ]);
    if(!remaining.ok) {
      return {ok:false,error:`译文已经导入并同步，但生成“剩余未翻译 XLSX”失败：${remaining.error||'未知错误'}`,report:imported.report,sync,records:readRecords(project.workspace)};
    }
    return {ok:true,import:imported.report,sync,syncs,autoRejected,remaining:remaining.report,remainingXlsx:remaining.report?.xlsx,records:readRecords(project.workspace),sourceXlsx,mappingJson,modifiedDirs:syncs.map(x=>x.outputDir)};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('merge-tsv-csv', async (_e,{project,pak}) => {
  try {
    const base=pak.replace(/\.pak$/i,'');
    const inputDir=path.join(project.workspace,'tsv_csv_before',base);
    const afterDir=path.join(project.workspace,'tsv_csv_after',base);
    const checkpoint=path.join(afterDir,'_model_translation_checkpoint.json');
    const mergedDir=path.join(project.workspace,'tsv_csv_merged',base);
    const remainingMode=fs.existsSync(afterDir)&&fs.existsSync(checkpoint);
    const mergedCsv=path.join(mergedDir,remainingMode?`${base}_untranslated_merged.csv`:`${base}_merged.csv`);
    const mappingJson=path.join(mergedDir,remainingMode?`${base}_untranslated_mapping.json`:`${base}_merged_mapping.json`);
    if(!fs.existsSync(inputDir)) throw new Error(`未找到导出目录：${inputDir}`);
    const res=remainingMode
      ? await runCli(['merge-remaining-tsv-csv',afterDir,checkpoint,mergedCsv,mappingJson])
      : await runCli(['merge-tsv-csv',inputDir,mergedCsv,mappingJson]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,mergedCsv,mappingJson};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('api-translator-config', async () => {
  const config=readApiTranslatorConfig();
  return config;
});
ipcMain.handle('save-api-translator-config', async (_e,config) => {
  try { return {ok:true,config:writeApiTranslatorConfig(config)}; }
  catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('fetch-api-models', async (_e,config) => {
  try {
    const current=writeApiTranslatorConfig(config || readApiTranslatorConfig());
    const profile=activeApiProfile(current);
    if(!profile.baseUrl) throw new Error('请先填写 BaseURL');
    const res=await runCli(['api-fetch-models',profile.baseUrl,profile.apiKey]);
    if(!res.ok) return res;
    profile.models=res.result.models || [];
    if(profile.models.length && !profile.models.includes(profile.model)) profile.model=profile.models[0];
    writeApiTranslatorConfig(current);
    return {ok:true,models:profile.models,config:current};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('api-translate-merged-csv', async (_e,{project,pak,config}) => {
  try {
    const current=writeApiTranslatorConfig(config || readApiTranslatorConfig());
    const profile=activeApiProfile(current);
    if(!profile.baseUrl || !profile.model) throw new Error('请先在设置里填写 BaseURL 和模型');
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const beforeDir=path.join(project.workspace,'text_csv_before',base);
    const beforeCsv=path.join(beforeDir,`${base}_localization.csv`);
    const beforeIndex=path.join(beforeDir,'_text_localization_index.json');
    const packageDir=path.join(project.workspace,'text_csv_api',base);
    const chunksDir=path.join(packageDir,'chunks');
    const translatedDir=path.join(packageDir,'translated');
    const afterDir=path.join(project.workspace,'text_csv_after',base);
    const afterCsv=path.join(afterDir,`${base}_localization.csv`);
    const modifiedDir=path.join(project.workspace,'tsv_after',base);
    const exported=await runCli(['export-text-csv',item.extracted,beforeDir,base,pak]);
    if(!exported.ok) return exported;
    const prepared=await runCli(['prepare-api-localization-csv',beforeCsv,packageDir,base,'1000']);
    if(!prepared.ok) return prepared;
    fs.writeFileSync(promptTempPath(),profile.prompt || defaultApiTranslatorConfig().profiles[0].prompt,'utf8');
    const res=await runCli(['api-translate-csv-dir',chunksDir,translatedDir,profile.baseUrl,profile.apiKey,profile.model,promptTempPath(),String(Number.isFinite(Number(profile.batchSize)) ? Number(profile.batchSize) : 0),dbPath()]);
    if(!res.ok) return res;
    const applied=await runCli(['apply-api-localization-csv',packageDir,translatedDir,afterCsv]);
    if(!applied.ok) return applied;
    const imported=await runCli(['import-text-csv',item.extracted,afterCsv,beforeIndex,modifiedDir]);
    if(!imported.ok) return imported;
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    let sync=null,records=null;
    if(fs.existsSync(recordsPath)) {
      sync=await runCli(['apply-text-csv-records',recordsPath,afterCsv,beforeIndex,pak]);
      if(!sync.ok) return sync;
      records=readRecords(project.workspace);
    }
    return {ok:true,report:res.report,export:exported.report,prepare:prepared.report,apply:applied.report,import:imported.report,sync:sync?.report,records,inputCsv:beforeCsv,inputDir:chunksDir,outputDir:translatedDir,csvAfterDir:afterDir,csvAfter:afterCsv,packageDir,modifiedDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('api-review', async (_e,{project,pak,config,forceIds}) => {
  try {
    const current=writeApiTranslatorConfig(config || readApiTranslatorConfig());
    const profile=activeApiProfile(current);
    if(!profile.baseUrl || !profile.model) throw new Error('请先在翻译设置中填写 BaseURL，并获取、选择模型');
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    if(!fs.existsSync(recordsPath)) throw new Error('未找到文本记录，请先导入 PAK');
    const modifiedDir=path.join(project.workspace,'modified',base);
    const importReportPath=path.join(modifiedDir,'_xlsx_localization_import_report.json');
    if(!fs.existsSync(importReportPath)) throw new Error('未找到最近的 XLSX 导入结果，请先点击“导入润色”');
    const importReport=JSON.parse(fs.readFileSync(importReportPath,'utf8'));
    const sourceXlsx=path.isAbsolute(importReport.xlsx || '')
      ? path.normalize(importReport.xlsx)
      : path.resolve(project.workspace, importReport.xlsx || '');
    if(!fs.existsSync(sourceXlsx)) throw new Error(`未找到最近导入的 XLSX：${sourceXlsx}`);
    const mappingJson=importReport.mapping_json
      ? (path.isAbsolute(importReport.mapping_json) ? path.normalize(importReport.mapping_json) : path.resolve(project.workspace,importReport.mapping_json))
      : path.join(project.workspace,'xlsx_export',base,`${base}_localization_mapping.json`);
    if(!fs.existsSync(mappingJson)) throw new Error(`未找到映射表：${mappingJson}`);
    const reviewDir=path.join(project.workspace,'api_review',base);
    fs.mkdirSync(reviewDir,{recursive:true});
    const selectionPath=path.join(reviewDir,'review_selection_ids.json');
    const selectedIds=Array.isArray(forceIds)?[...new Set(forceIds.map(String).filter(Boolean))]:[];
    fs.writeFileSync(selectionPath,JSON.stringify(selectedIds),'utf8');
    fs.writeFileSync(promptTempPath(),profile.prompt || defaultApiTranslatorConfig().profiles[0].prompt,'utf8');
    const reviewed=await runCli(['api-review-records',recordsPath,pak,reviewDir,profile.baseUrl,profile.apiKey || '',profile.model,promptTempPath(),String(Number(profile.batchSize)||20),dbPath(),profile.reviewMode==='all'?'all':'risk',sourceXlsx,mappingJson,item.extracted,selectionPath]);
    if(!reviewed.ok) return reviewed;
    win.webContents.send('backend-progress',{event:'progress',phase:'api-review',percent:96,message:'正在把审校结果回写到文本资源…'});
    const reviewedXlsx=reviewed.report.reviewed_xlsx;
    const imported=await runCli(['import-xlsx',item.extracted,reviewedXlsx,mappingJson,modifiedDir],{suppressProgress:true});
    if(!imported.ok) return imported;
    const synced=await runCli(['apply-xlsx-records',recordsPath,reviewedXlsx,mappingJson,pak],{suppressProgress:true});
    if(!synced.ok) return synced;
    win.webContents.send('backend-progress',{event:'progress',phase:'api-review',percent:100,message:'API 审校与资源回写完成'});
    return {ok:true,report:reviewed.report,import:imported.report,sync:synced.report,records:readRecords(project.workspace),modifiedDir,reviewedXlsx};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('split-merged-tsv-csv', async (_e,{project,pak}) => {
  try {
    const base=pak.replace(/\.pak$/i,'');
    const mergedDir=path.join(project.workspace,'tsv_csv_merged',base);
    const untranslatedMapping=path.join(mergedDir,`${base}_untranslated_mapping.json`);
    const remainingMapping=path.join(mergedDir,`${base}_remaining_mapping.json`);
    const fullMapping=path.join(mergedDir,`${base}_merged_mapping.json`);
    const mappingJson=fs.existsSync(untranslatedMapping)?untranslatedMapping:(fs.existsSync(remainingMapping)?remainingMapping:fullMapping);
    const afterDir=path.join(project.workspace,'tsv_csv_after',base);
    if(!fs.existsSync(mappingJson)) throw new Error(`未找到 ID 映射：${mappingJson}。请先执行“合并未汉化 CSV”。`);
    const picked=await dialog.showOpenDialog(win,{title:'选择外部翻译后的合并 CSV',defaultPath:mergedDir,properties:['openFile'],filters:[{name:'CSV',extensions:['csv']}]});
    if(picked.canceled || !picked.filePaths.length) return {ok:false,canceled:true};
    const mergedCsv=picked.filePaths[0];
    const res=await runCli(['split-merged-tsv-csv',mergedCsv,mappingJson,afterDir]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,mergedCsv,mappingJson,outputDir:afterDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('import-tsv-csv', async (_e,{project,pak}) => {
  try {
    const item=pakItem(project,pak);
    if(!item || !item.extracted) throw new Error('当前项目没有可用的解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const beforeDir=path.join(project.workspace,'tsv_csv_before',base);
    const afterDir=path.join(project.workspace,'tsv_csv_after',base);
    const outDir=path.join(project.workspace,'tsv_after',base);
    if(!fs.existsSync(beforeDir)) throw new Error(`未找到导出目录：${beforeDir}`);
    if(!fs.existsSync(afterDir)) {
      throw new Error(`未找到译后 CSV 目录：${afterDir}。请先执行“本地模型翻译文本 CSV”。`);
    }
    const res=await runCli(['import-tsv-csv',item.extracted,afterDir,outDir]);
    if(!res.ok) return res;
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    let sync=null;
    let records=null;
    if(fs.existsSync(recordsPath)) {
      sync=await runCli(['apply-tsv-csv-records',recordsPath,afterDir,pak]);
      if(!sync.ok) return sync;
      records=readRecords(project.workspace);
    }
    return {ok:true,report:res.report,sync:sync?.report,records,csvBeforeDir:beforeDir,csvAfterDir:afterDir,outputDir:outDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('translate-tsv-csv', async (_e,{project,pak}) => {
  try {
    const base=pak.replace(/\.pak$/i,'');
    const beforeDir=path.join(project.workspace,'tsv_csv_before',base);
    const afterDir=path.join(project.workspace,'tsv_csv_after',base);
    const scriptPath=path.join(project.workspace,'translate.py');
    const appScriptPath=path.join(__dirname,'..','backend','translate.py');
    const fallbackScript=path.join('D:\\App\\Frida Android\\pak\\updatefs','translate.py');
    const translatePy=fs.existsSync(scriptPath) ? scriptPath : (fs.existsSync(appScriptPath) ? appScriptPath : fallbackScript);
    if(!fs.existsSync(beforeDir)) throw new Error(`未找到导出目录：${beforeDir}`);
    if(!fs.existsSync(translatePy)) throw new Error(`未找到翻译脚本。请把 translate.py 放到当前工作区：${scriptPath}，或使用内置脚本：${appScriptPath}，并提供 translate_string(text) 函数。`);
    const res=await runCli(['translate-tsv-csv',beforeDir,afterDir,translatePy,dbPath()]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,inputDir:beforeDir,outputDir:afterDir,script:translatePy};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('model-translate-tsv-csv', async (_e,{project,pak}) => {
  try {
    const base=pak.replace(/\.pak$/i,'');
    const beforeDir=path.join(project.workspace,'tsv_csv_before',base);
    const afterDir=path.join(project.workspace,'tsv_csv_after',base);
    const projectScript=path.join(project.workspace,'translate.py');
    const bundledScript=path.join(__dirname,'..','backend','translate.py');
    const translatePy=fs.existsSync(projectScript) ? projectScript : bundledScript;
    if(!fs.existsSync(beforeDir)) throw new Error(`未找到导出目录：${beforeDir}`);
    if(!fs.existsSync(translatePy)) throw new Error(`未找到翻译脚本：${translatePy}`);
    const res=await runCli(['model-translate-tsv-csv',beforeDir,afterDir,modelPath(),dbPath(),translatePy,'48']);
    if(!res.ok) return res;
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    let sync=null,records=null;
    if(fs.existsSync(recordsPath)) {
      sync=await runCli(['apply-tsv-csv-records',recordsPath,afterDir,pak]);
      if(!sync.ok) return sync;
      records=readRecords(project.workspace);
    }
    return {ok:true,report:res.report,sync:sync?.report,records,inputDir:beforeDir,outputDir:afterDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
async function buildOnePak(project,pak) {
  try {
    // Exporting the untranslated workbook scans the full records database and
    // writes an XLSX. It is an explicit user action, not a build prerequisite.
    const qualityWarning='';
    normalizeProjectPakSnapshots(project);
    const item=pakItem(project,pak);
    if(!item || !item.path || !item.extracted) throw new Error('当前项目没有可用的原始 PAK 或解包目录');
    const base=pak.replace(/\.pak$/i,'');
    const xlsxModifiedDir=path.join(project.workspace,'modified',base);
    const legacyModifiedDir=path.join(project.workspace,'tsv_after',base);
    const xlsxReady=fs.existsSync(path.join(xlsxModifiedDir,'_xlsx_localization_import_report.json'))||fs.existsSync(path.join(xlsxModifiedDir,'_records_materialize_report.json'));
    const manualModifiedReady=hasBuildableEntryFiles(xlsxModifiedDir);
    let modifiedDir=(xlsxReady||manualModifiedReady)?xlsxModifiedDir:legacyModifiedDir;
    let res;
    if(needsRecordMaterialize(project,modifiedDir)) {
      const recordsPath=path.join(project.workspace,'localization','text_records.json');
      const materialized=await runCli(['materialize-records',item.extracted,recordsPath,pak,modifiedDir]);
      if(!materialized.ok) return materialized;
      if(materialized.report?.no_changes){
        const output=path.join(project.workspace,'build',path.basename(item.path || pak));
        fs.mkdirSync(path.dirname(output),{recursive:true});
        fs.copyFileSync(item.path,output);
        const sha256=require('crypto').createHash('sha256').update(fs.readFileSync(output)).digest('hex');
        return {ok:true,output,report:{output,sha256,changed_entries:0,archive_size:fs.statSync(output).size,roundtrip:'exact-copy',no_changes:true}};
      }
      const skipped=materialized.report?.skipped || [];
      if(skipped.length) {
        const details=skipped.slice(0,8).map(entry=>`${entry.file || ''} / ${entry.id || ''}：${entry.reason || '格式保护校验失败'}`).join('\n');
        return {ok:false,error:`有 ${skipped.length} 条界面编辑未能写回资源，已停止构建，避免悄悄打包旧文本。\n\n${details}\n\n请完整保留原文中的 <c>、<enter>、%s 等标记后再构建。`,report:materialized.report};
      }
    }
    if(fs.existsSync(modifiedDir) && hasBuildableEntryFiles(modifiedDir)) {
      const output=path.join(project.workspace,'build',path.basename(item.path || pak));
      const referenceDir=rawReferenceDir(project,item,pak);
      res=await runCli(['build-pak',item.path,referenceDir,modifiedDir,output]);
    } else if(pak.toLowerCase()==='ui.pak') {
      res=await runCli(['build',project.workspace,pak]);
    } else {
      const recordsPath=path.join(project.workspace,'localization','text_records.json');
      if(!fs.existsSync(recordsPath)) throw new Error(`未找到回写目录：${modifiedDir}`);
      const materialized=await runCli(['materialize-records',item.extracted,recordsPath,pak,modifiedDir]);
      if(!materialized.ok) return materialized;
      const output=path.join(project.workspace,'build',path.basename(item.path || pak));
      if(materialized.report?.no_changes){
        fs.mkdirSync(path.dirname(output),{recursive:true});
        fs.copyFileSync(item.path,output);
        const sha256=require('crypto').createHash('sha256').update(fs.readFileSync(output)).digest('hex');
        return {ok:true,output,report:{output,sha256,changed_entries:0,archive_size:fs.statSync(output).size,roundtrip:'exact-copy',no_changes:true}};
      }
      const referenceDir=rawReferenceDir(project,item,pak);
      res=await runCli(['build-pak',item.path,referenceDir,modifiedDir,output]);
    }
    if(!res.ok) return res;
    return {ok:true,report:res.report || res.result,output:(res.report || res.result)?.output,warning:qualityWarning};
  } catch(e){ return {ok:false,error:e.message}; }
}

ipcMain.handle('build-pak', async (_e,{project,pak}) => buildOnePak(project,pak));
ipcMain.handle('build-paks', async (_e,{project,paks}) => {
  try {
    const names=[...new Set((paks&&paks.length?paks:(project?.paks||[]).map(item=>item.pak)).filter(Boolean))];
    if(!names.length) throw new Error('当前项目没有可构建的 PAK');
    const results=[];
    for(let index=0;index<names.length;index++) {
      win?.webContents.send('backend-progress',{event:'progress',phase:'build-paks',percent:Math.round(index*100/names.length),message:`正在构建 ${index+1}/${names.length}：${names[index]}`});
      const result=await buildOnePak(project,names[index]);
      results.push({pak:names[index],...result});
      if(!result.ok) return {ok:false,error:`${names[index]} 构建失败：${result.error||'未知错误'}`,results};
    }
    win?.webContents.send('backend-progress',{event:'progress',phase:'build-paks',percent:100,message:`全部 ${names.length} 个 PAK 构建完成`});
    return {ok:true,results,outputs:results.map(item=>item.output||item.report?.output)};
  } catch(e){ return {ok:false,error:e.message}; }
});

ipcMain.handle('export-untranslated-xlsx', async (_e,{project}) => {
  try {
    const paks=[...new Set((project?.paks||[]).map(item=>item.pak).filter(Boolean))];
    if(!project?.workspace || !paks.length) throw new Error('当前项目没有可用的 PAK');
    const exportDir=multiPakXlsxRoot(project.workspace);
    const xlsxDir=path.join(exportDir,'remaining_xlsx');
    const configDir=path.join(exportDir,'config');
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    const exportBase='all_paks_player_visible_remaining';
    const refreshArgs=['refresh-visible-records',recordsPath];
    for(const pak of paks){const item=pakItem(project,pak);if(item?.extracted)refreshArgs.push(pak,item.extracted)}
    const refreshed=await runCli(refreshArgs);
    if(!refreshed.ok) return refreshed;
    const res=await runCli(['export-xlsx-multi-remaining',recordsPath,xlsxDir,exportBase,configDir,...paks]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,refresh:refreshed.report,records:readRecords(project.workspace),xlsxPath:res.report?.xlsx||path.join(xlsxDir,`${exportBase}_localization.xlsx`),outputDir:xlsxDir,configDir};
  } catch(e){ return {ok:false,error:e.message}; }
});
let ollamaControlPath = null;
ipcMain.handle('ollama-control', async (_e, {stop,think}) => {
  if(!ollamaControlPath) return {ok:false,error:'没有正在运行的 Ollama 任务'};
  fs.writeFileSync(ollamaControlPath,JSON.stringify({stop:!!stop,think:!!think}));
  return {ok:true};
});
ipcMain.handle('ollama-translate', async (_e,{project,pak,maxBuckets,bucketSize,think}) => {
  if(ollamaControlPath) return {ok:false,error:'Ollama 任务已经运行'};
  try {
    if(!project || !project.workspace) throw new Error('请先打开项目');
    const fallbackPak=pak||project.paks?.map(item=>item.pak).find(Boolean);
    if(!fallbackPak) throw new Error('当前项目没有可用的 PAK');
    const base=fallbackPak.replace(/\.pak$/i,'');
    const defaultDir=path.join(playerVisibleXlsxRoot(project.workspace,base),'xlsx');
    const picked=await dialog.showOpenDialog(win,{title:'选择逐文件 XLSX 或多 PAK 未翻译 XLSX 文件夹',defaultPath:defaultDir,properties:['openDirectory']});
    if(picked.canceled || !picked.filePaths.length) return {ok:false,canceled:true};
    const folderPath=picked.filePaths[0];
    const adjacentConfig=path.join(path.dirname(folderPath),'config');
    const projectConfig=path.join(playerVisibleXlsxRoot(project.workspace,base),'config');
    const configDir=fs.existsSync(adjacentConfig)?adjacentConfig:(fs.existsSync(projectConfig)?projectConfig:folderPath);
    let multiTarget=null;
    for(const name of fs.readdirSync(folderPath).filter(name=>/_localization\.xlsx$/i.test(name)&&!name.endsWith('.bak')).sort()) {
      const workbook=path.join(folderPath,name);
      const mapping=path.join(configDir,`${path.basename(name,'.xlsx')}_mapping.json`);
      if(!fs.existsSync(mapping)) continue;
      try {
        const meta=JSON.parse(fs.readFileSync(mapping,'utf8').replace(/^\uFEFF/,''));
        if(meta.mode==='multi-pak-out-of-band-skeleton'&&Number(meta.version)===7) {
          multiTarget={workbook,mapping,meta};
          break;
        }
      } catch {}
    }
    ollamaControlPath=path.join(configDir,'ollama_control.json');
    fs.mkdirSync(configDir,{recursive:true});
    fs.writeFileSync(ollamaControlPath,JSON.stringify({stop:false,think:!!think}));
    const args=['ollama-translate-folder',project.workspace,multiTarget?'all-paks':fallbackPak,folderPath];
    const normalizedBucketSize=Number.isFinite(Number(bucketSize))&&Number(bucketSize)>0?Math.min(500,Math.max(1,Number(bucketSize))):25;
    args.push(String(normalizedBucketSize),ollamaControlPath,configDir);
    const res=await runCli(args);
    if(!res.ok) return res;
    const recordsPath=path.join(project.workspace,'localization','text_records.json');
    if(multiTarget) {
      const imported=await runCli(['apply-xlsx-multi-records',recordsPath,multiTarget.workbook,multiTarget.mapping]);
      if(!imported.ok) return imported;
      const syncs=[];
      let autoRejected=0;
      for(const pakName of imported.report?.paks||[]) {
        const sync=await syncRecordsWithAutoReject(project,pakName);
        syncs.push({pak:pakName,...sync.report,outputDir:sync.outputDir});
        autoRejected+=Number(sync.autoRejected||0);
        if(Number(sync.report?.skipped_count||0)>0) {
          return {ok:false,error:`${pakName} 自动恢复后仍有 ${sync.report.skipped_count} 条结构错误，已停止资源同步。`,report:res.report,importReport:imported.report,records:readRecords(project.workspace)};
        }
      }
      const aggregate={
        modified_records:syncs.reduce((n,x)=>n+Number(x.modified_records||0),0),
        changed_file_count:syncs.reduce((n,x)=>n+Number(x.changed_file_count||0),0),
        skipped_count:syncs.reduce((n,x)=>n+Number(x.skipped_count||0),0),
      };
      const exportDir=multiPakXlsxRoot(project.workspace);
      const remaining=await runCli(['export-xlsx-multi-remaining',recordsPath,path.join(exportDir,'remaining_xlsx'),'all_paks_player_visible_remaining',path.join(exportDir,'config'),...(imported.report?.paks||[])]);
      if(!remaining.ok) return {ok:false,error:`Ollama 译文已经安全导入，但重新生成残留 XLSX 失败：${remaining.error||'未知错误'}`,report:res.report,importReport:imported.report,records:readRecords(project.workspace)};
      skipNextPersistAfterOllama=true;
      return {ok:true,multiPak:true,report:res.report,stopped:!!res.report?.stopped,importReport:imported.report,records:readRecords(project.workspace),resourceSync:{report:aggregate},syncs,autoRejected,folder:folderPath,configFolder:configDir,remaining:remaining.report,remainingXlsx:remaining.report?.xlsx};
    }
    const imported=await runCli(['apply-xlsx-folder-records',recordsPath,folderPath,fallbackPak,configDir]);
    if(!imported.ok) return imported;
    if(Number(imported.report?.failed_files||0)>0) {
      return {ok:false,error:`Ollama 已保存部分成果，但导回只有 ${imported.report.imported_files||0}/${imported.report.total_files||0} 个文件成功。已停止资源同步。`,report:res.report,importReport:imported.report,records:readRecords(project.workspace)};
    }
    const sync=await syncRecordsWithAutoReject(project,fallbackPak);
    const importedChanges=Number(imported.report?.changed||0);
    const materialized=Number(sync?.report?.modified_records||0);
    const changedFiles=Number(sync?.report?.changed_file_count||0);
    const skipped=Number(sync?.report?.skipped_count||0);
    if(skipped>0 || (importedChanges>0 && (materialized===0 || changedFiles===0))) {
      return {ok:false,error:`Ollama 译文已保存，但资源回写未完整通过：译文变化 ${importedChanges} 条，生成资源 ${changedFiles} 个，跳过 ${skipped} 条。已阻止显示为完成。`,report:res.report,importReport:imported.report,resourceSync:sync,records:readRecords(project.workspace)};
    }
    skipNextPersistAfterOllama=true;
    return {
      ok:true,
      report:res.report,
      stopped:!!res.report?.stopped,
      importReport:imported.report,
      records:readRecords(project.workspace),
      resourceSync:sync,
      autoRejected:sync.autoRejected||0,
      folder:folderPath,
      configFolder:configDir,
    };
  } catch(e){ return {ok:false,error:e.message}; }
  finally { ollamaControlPath=null; }
});
ipcMain.handle('import-untranslated-xlsx', async (_e,{project,pak}) => {
  try {
    if(!project || !project.workspace) throw new Error('请先打开项目');
    const base=pak.replace(/\.pak$/i,'');
    const defaultDir=path.join(project.workspace,'untranslated_xlsx',base);
    const picked=await dialog.showOpenDialog(win,{title:`选择要导回 ${pak} 的译后 XLSX`,defaultPath:defaultDir,properties:['openFile'],filters:[{name:'Excel Workbook',extensions:['xlsx']}]});
    if(picked.canceled || !picked.filePaths.length) return {ok:false,canceled:true};
    const xlsxPath=picked.filePaths[0];
    const res=await runCli(['import-untranslated-xlsx',project.workspace,pak,xlsxPath]);
    if(!res.ok) return res;
    const sync=await syncRecordsToResources(project,pak);
    return {ok:true,report:res.report,records:readRecords(project.workspace),xlsx:xlsxPath,resourceSync:sync};
  } catch(e){ return {ok:false,error:e.message}; }
});
ipcMain.handle('safe-pc-merge', async (_e,{project,pak}) => {
  try {
    if(!project?.workspace) throw new Error('请先打开越南版项目');
    if(!pak || !/\.pak$/i.test(pak)) throw new Error('请先选择要迁移的 PAK');
    const cacheCandidates=[
      path.join(path.dirname(project.workspace),'_pc_reference','paks+'),
      path.join(path.dirname(path.dirname(project.workspace)),'_pc_reference','paks+'),
      path.join(path.dirname(__dirname),'pak','_pc_reference','paks+'),
    ];
    const pcRoot=cacheCandidates.find(candidate=>fs.existsSync(path.join(candidate,'localization','text_records.json')));
    if(!pcRoot){
      throw new Error(`国际版对象缓存不存在或尚未分析。\n已检查：\n${[...new Set(cacheCandidates)].join('\n')}\n请把完整国际版分析工作区放入 _pc_reference\\paks+。`);
    }
    const knownManual=[
      'D:\\文档\\MuMu共享文件夹\\Download\\已汉化\\0814_5325A29A.tsv',
      'D:\\文档\\MuMu共享文件夹\\Download\\已汉化\\0068_06C163CB.tsv',
    ].filter(p=>fs.existsSync(p));
    const cachedDir=path.join(project.workspace,'manual_zh_overrides','updatefs');
    const cached=fs.existsSync(cachedDir)?fs.readdirSync(cachedDir).filter(n=>/^(0814_5325A29A|0068_06C163CB)\.tsv$/i.test(n)).map(n=>path.join(cachedDir,n)):[];
    // Prefer the original supplied file when present, otherwise use the cached
    // copy.  Deduplicate by filename because the two paths represent the same
    // manual override and should not appear twice in the report.
    const manualByName=new Map();
    for(const candidate of [...cached,...knownManual]) manualByName.set(path.basename(candidate).toLowerCase(),candidate);
    const manual=[...manualByName.values()];
    const res=await runCli(['safe-pc-merge',pcRoot,project.workspace,dbPath(),pak,...manual]);
    if(!res.ok) return res;
    return {ok:true,report:res.report,records:readRecords(project.workspace)};
  } catch(e){ return {ok:false,error:e.message}; }
});

ipcMain.handle('push-github-pak', async (_e,{project,pak}) => {
  try {
    if(!project || !project.workspace) throw new Error('请先打开项目');
    if(!pak || !/\.pak$/i.test(pak)) throw new Error('请先选择一个具体的 PAK');
    const source=path.join(project.workspace,'build',path.basename(pak));
    if(!fs.existsSync(source)) throw new Error(`未找到已构建的 PAK：${source}\n请先点击“构建 PAK”。`);

    const repoDir=defaultPakRedirectRepo();
    const repoPakDir=path.join(repoDir,'pak');
    if(!fs.existsSync(path.join(repoDir,'.git'))) throw new Error(`未找到 Git 仓库：${repoDir}`);
    if(!fs.existsSync(repoPakDir)) throw new Error(`未找到 GitHub PAK 目录：${repoPakDir}`);

    const target=path.join(repoPakDir,path.basename(pak));
    fs.copyFileSync(source,target);
    const size=fs.statSync(target).size;
    const linkspak=path.join(repoPakDir,'linkspak.txt');
    updateLinkspakSize(linkspak,path.basename(pak),size);

    const branchRes=await runGit(repoDir,['rev-parse','--abbrev-ref','HEAD']);
    if(!branchRes.ok) throw new Error((branchRes.stderr || branchRes.stdout || '读取 Git 分支失败').trim());
    const branch=branchRes.stdout.trim() || 'main';

    const relPak=path.posix.join('pak',path.basename(pak));
    const relLink='pak/linkspak.txt';
    const status=await runGit(repoDir,['status','--porcelain','--',relPak,relLink]);
    if(!status.ok) throw new Error((status.stderr || status.stdout || '读取 Git 状态失败').trim());
    const createdCommit=Boolean(status.stdout.trim());
    if(createdCommit) {
      const add=await runGit(repoDir,['add',relPak,relLink]);
      if(!add.ok) throw new Error((add.stderr || add.stdout || 'git add 失败').trim());

      const commitMsg=`Update ${path.basename(pak)}`;
      const commit=await runGit(repoDir,['commit','-m',commitMsg]);
      if(!commit.ok) throw new Error((commit.stderr || commit.stdout || 'git commit 失败').trim());
    }

    const rev=await runGit(repoDir,['rev-parse','--short','HEAD']);
    const commitId=rev.ok ? rev.stdout.trim() : '';
    // A clean worktree may still contain commits that failed to reach GitHub earlier.
    // Push HEAD explicitly so a same-named tag cannot make the branch ref ambiguous.
    const push=await runGit(repoDir,['push','origin',`HEAD:refs/heads/${branch}`]);
    if(!push.ok) throw new Error((push.stderr || push.stdout || 'git push 失败').trim());

    const pushOutput=`${push.stdout || ''}\n${push.stderr || ''}`;
    const alreadyUpToDate=/up[- ]to[- ]date|everything up-to-date/i.test(pushOutput);
    return {ok:true,pushed:true,createdCommit,alreadyUpToDate,size,source,target,linkspak,branch,commit:commitId};
  } catch(e){ return {ok:false,error:e.message}; }
});
