import QtQuick
import QtQuick.Layouts
import Quickshell
import Quickshell.Io
import qs.Commons
import qs.Ui

Panel {
  id: root
  moduleName: "eg.unifydrive"
  ipcTarget: "eg.unifydrive"

  property var anchorItem: null
  property var hostWidget: null
  readonly property var barIdentity: hostWidget || root
  readonly property color foreground: bar ? bar.foreground : Color.foreground
  readonly property color urgent: bar ? bar.urgent : Color.urgent
  readonly property color dim: Qt.darker(foreground, 1.55)
  readonly property string fontFamily: bar ? bar.fontFamily : Style.font.family
  readonly property string helper: Qt.resolvedUrl("udrive.py").toString().replace("file://", "")
  readonly property string home: Quickshell.env("HOME")
  readonly property string rootPath: String(setting("rootPath", "/nvme11/my/data"))
  readonly property string comicsPath: String(setting("comicsPath", rootPath.replace(/\/+$/, "") + "/Comics"))
  readonly property string downloadDir: String(setting("downloadDir", "~/Downloads")).replace(/^~/, home)

  property string path: ""
  property var entries: []
  property string status: ""
  property bool failed: false
  property bool busy: listProc.running || xferProc.running || pickProc.running || loginProc.running || albumsProc.running || songsProc.running || mediaProc.running || vlibsProc.running || vlistProc.running || vitemsProc.running || uploadProc.running || mkdirProc.running || syncRunProc.running || searchProc.running || pinProc.running || photoProc.running
  property string pendingAction: ""

  // tabs + media
  property string tab: "files"            // "files" | "music"
  property var albums: []                 // what the list shows (after search)
  property var allAlbums: []
  property int albumCount: 0
  property string query: ""
  property string coverBase: ""           // http://127.0.0.1:<port>/<token> once the cover proxy is up
  property string albumId: ""             // set while viewing one album's songs
  property string albumName: ""
  property var songs: []
  // video tab
  property var vlibs: []
  property string vlib: "latest"
  property var vall: []
  property var vshown: []
  property string vquery: ""
  property string vcollId: ""
  property string vcollTitle: ""
  property string vcollCls: ""
  property var vitems: []
  readonly property bool typing: musicSearch.focused || videoSearch.focused || globalSearch.focused
  // comics tab
  property var cseries: []
  property string cpath: ""               // series folder being viewed ("" = the series list)
  property string cname: ""
  property var cchapters: []
  property var cprogress: ({})            // series path -> { chapter, page }
  property var now: ({ playing: false })
  property var vnow: ({ playing: false })  // the running video player (tracks, position)
  property var vprog: ({})                 // video path -> { pos, dur, done }
  property var recent: []                  // "Continue" items: newest unfinished video / comic / album
  property string albumCover: ""
  property var nas: ({ online: null })     // {online, pools[], temp, problems[]}; null = not known yet
  readonly property string nasSummary: {
    if (nas.online === null) return ""
    if (!nas.online) return "NAS unreachable"
    var p = nas.pools && nas.pools.length > 0 ? nas.pools[0] : null
    var t = p ? fmtSize(p.free) + " free of " + fmtSize(p.total) + " (" + Math.round(p.used * 100 / p.total) + "% used)" : ""
    if (nas.temp) t += "  ·  " + nas.temp + "°C"
    if (nas.problems && nas.problems.length > 0) t += "  ·  ⚠ " + nas.problems[0]
    return t
  }
  readonly property string nasIcon: nas.online === false ? "󰖑" : ((nas.problems && nas.problems.length > 0) ? "󰧠" : "󰅟")
  property bool queueOpen: false
  property var sync: ({ jobs: [], state: {}, timer: false })
  property string syncLocal: ""
  function loadSync() { if (!syncListProc.running) { syncListProc.command = ["python3", "-I", helper, "sync-list"]; syncListProc.running = true } }
  function syncPick() {
    uploadMenuOpen = false
    if (syncPickProc.running) return
    syncPickProc.command = ["zenity", "--file-selection", "--directory", "--title=Sync a folder to " + path]
    syncPickProc.running = true
  }
  function syncNow(name) {
    uploadMenuOpen = false
    if (syncRunProc.running) return
    status = "Syncing…"; failed = false
    syncRunProc.command = ["python3", "-I", helper, "sync-run"].concat(name ? [name] : [])
    syncRunProc.running = true
  }
  function syncTimer() {
    uploadMenuOpen = false
    syncTimerProc.command = ["python3", "-I", helper, "sync-timer", sync.timer ? "off" : "on"]
    syncTimerProc.running = true
  }
  function syncSummary() {
    var n = sync.jobs.length, last = 0, up = 0, bad = ""
    for (var k in sync.state) { var st = sync.state[k]; if (st.ts > last) last = st.ts; if (st.uploaded) up += st.uploaded; if (st.error) bad = st.error; else if (st.failed && st.failed.length) bad = st.failed.length + " failed" }
    var ago = last ? Math.max(1, Math.round((Date.now() / 1000 - last) / 60)) + " min ago" : "never run"
    return n + (n === 1 ? " folder" : " folders") + " · " + ago + (bad ? " · " + bad : "")
  }
  // ---- More tab: transfers, share links, NAS apps, settings ----
  property string moreSec: "transfers"
  property var tasks: []
  property int tasksActive: 0
  property var shares: []
  property var apps: []
  property var cfg: ({})
  property var agent: ({ running: false, live: [], alerts: [] })
  property string lastShare: ""
  property var settingSpecs: [
    { type: "agent", label: "Background agent" },
    { type: "photo", label: "Upload new photos automatically" },
    { type: "text", key: "photo_local", label: "Photo folder on this computer" },
    { type: "text", key: "photo_remote", label: "…uploaded to this NAS folder" },
    { type: "bool", key: "notify_sync", label: "Notify when uploads finish" },
    { type: "bool", key: "alerts", label: "NAS health alerts" },
    { type: "int", key: "alert_free_pct", label: "Free space alert (%)", min: 1, max: 50, step: 1 },
    { type: "int", key: "alert_temp", label: "Disk temperature alert (°C)", min: 35, max: 80, step: 5 },
    { type: "bool", key: "autoplay", label: "Autoplay the next episode" },
    { type: "int", key: "autoplay_delay", label: "Autoplay delay (seconds)", min: 0, max: 60, step: 2 },
    { type: "int", key: "comic_prefetch", label: "Comic chapters to preload", min: 0, max: 5, step: 1 },
    { type: "int", key: "comic_cache_mb", label: "Comic page cache (MB)", min: 512, max: 20480, step: 512 },
    { type: "bool", key: "share_password", label: "Share links need a password" },
    { type: "int", key: "share_days", label: "Share lifetime (days)", min: 1, max: 365, step: 1 },
    { type: "text", key: "capture_remote", label: "Screenshot folder on the NAS" },
    { type: "bool", key: "capture_link", label: "Screenshots copy a share link" }
  ]
  function py(args) { return ["python3", "-I", helper].concat(args) }
  function loadMore() {
    if (moreSec === "transfers") { if (!tasksProc.running) { tasksProc.command = py(["tasks"]); tasksProc.running = true } }
    else if (moreSec === "shares") { if (!sharesProc.running) { sharesProc.command = py(["share-list"]); sharesProc.running = true } }
    else if (moreSec === "apps") { if (apps.length === 0 && !appsProc.running) { appsProc.command = py(["apps"]); appsProc.running = true } }
    else { loadCfg(); if (!agentProc.running) { agentProc.command = py(["agent-status"]); agentProc.running = true } }
  }
  function loadCfg() { if (!cfgProc.running) { cfgProc.command = py(["settings"]); cfgProc.running = true } }
  function setSec(sec) { moreSec = sec; loadMore() }
  function taskAct(action, id) { if (taskActProc.running) return; taskActProc.command = py(["task-act", action, id]); taskActProc.running = true }
  function taskTitle(t) {
    var names = { remove: "Delete", copy: "Copy", move: "Move", compress: "Compress", decompress: "Extract", uncompress: "Extract" }
    var first = String(t.src && t.src.length > 0 ? t.src[0] : "").split("/").pop()
    return (names[t.op] || t.op) + "  " + first + (t.src && t.src.length > 1 ? "  +" + (t.src.length - 1) : "")
  }
  function taskLine(t) {
    var p = []
    if (t.state === "done") p.push("done"); else p.push(t.state + (t.progress > 0 ? " " + t.progress + "%" : ""))
    if (t.total > 0) p.push(fmtSize(t.done) + " of " + fmtSize(t.total))
    if (t.speed > 0) p.push(fmtSize(t.speed) + "/s")
    if (t.failed > 0) p.push(t.failed + " failed")
    if (t.updated > 0) p.push(new Date(t.updated * 1000).toLocaleString(Qt.locale(), "d MMM HH:mm"))
    return p.join("  ·  ")
  }
  function copyText(text) { Quickshell.execDetached(["wl-copy", text]) }
  function shareEntry(entry) {
    if (shareMakeProc.running) return
    status = "Creating share link…"; failed = false
    shareMakeProc.command = py(["share-create", join(path, entry.name)])
    shareMakeProc.running = true
  }
  function shareText(sh) { return sh.link + (sh.pass ? "\nPassword: " + sh.pass : "") }
  function expiresText(sh) {
    if (sh.expired) return "expired"
    var left = sh.created + sh.expire - Date.now() / 1000
    return left > 86400 ? "expires in " + Math.ceil(left / 86400) + " d" : "expires today"
  }
  function openApp(a) { Quickshell.execDetached(py(["webui", a.app])); close() }
  function setSetting(key, value) {
    if (cfgSetProc.running) { cfgPending = [key, value]; return }
    cfgSetProc.command = py(["settings", "set", key, String(value)])
    cfgSetProc.running = true
  }
  property var cfgPending: null
  function agentToggle(on) { if (!agentSvcProc.running) { agentSvcProc.command = py(["agent-service", on ? "on" : "off"]); agentSvcProc.running = true } }
  function photoToggle(on) { if (!agentSvcProc.running) { agentSvcProc.command = py(["photosync", on ? "on" : "off"]); agentSvcProc.running = true } }
  function captureNow(mode) {
    uploadMenuOpen = false
    close()
    Quickshell.execDetached(["sh", "-c", "sleep 0.4; exec python3 -I \"$0\" capture \"$1\"", helper, mode])
  }
  // video: next episodes queue + offline copies
  function queueAfter(i) {
    var out = []
    for (var k = i + 1; k < vitems.length && out.length < 40; k++) if (vitems[k].path) out.push(vitems[k].path)
    return out
  }
  function videoPinned(path) { return offline.videos !== undefined && offline.videos[path] !== undefined }
  function pinVideo(path, title) {
    if (vpinProc.running) return
    status = "Saving for offline… 0%"; failed = false
    vpinProc.command = py(["video-pin", path, title])
    vpinProc.running = true
  }
  function unpinVideo(path) {
    Quickshell.execDetached(py(["video-unpin", path]))
    var v = Object.assign({}, offline.videos); delete v[path]
    offline = { albums: offline.albums, comics: offline.comics, videos: v }
    status = "Offline copy removed"
  }
  property var photos: []                  // photo timeline, newest first; each item carries its chunk start
  property int photoTotal: 0
  property int photoNext: 0
  function thumbUrl(path, fmt) { return coverBase !== "" ? coverBase + "/thumb?f=" + (fmt || "small") + "&p=" + encodeURIComponent(path) : "" }
  function loadPhotos(start) {
    if (photoProc.running) return
    if (start === 0) { photos = []; photoNext = 0 }
    photoProc.chunkStart = start
    status = start === 0 ? "Loading photos…" : status; failed = false
    photoProc.command = ["python3", "-I", helper, "photos", String(start), "60"]
    photoProc.running = true
  }
  function openPhoto(p) { Quickshell.execDetached(["python3", "-I", helper, "photo-open", String(p.chunk), p.path]); close() }
  property var offline: ({ albums: {}, comics: {}, videos: {} })   // saved-for-offline items (local data)
  function albumOffline(id) { return offline.albums[id] !== undefined }
  function comicPinned(path) { return offline.comics[cpath] !== undefined && offline.comics[cpath].paths.indexOf(path) !== -1 }
  function loadOffline() { if (!offlineProc.running) { offlineProc.command = ["python3", "-I", helper, "offline-list"]; offlineProc.running = true } }
  function pinAlbum() {
    if (pinProc.running) return
    status = "Saving album for offline…"; failed = false
    pinProc.command = ["python3", "-I", helper, "album-pin", albumId, albumName, albumCover]
    pinProc.running = true
  }
  function unpinAlbum() {
    Quickshell.execDetached(["python3", "-I", helper, "album-unpin", albumId])
    var o = Object.assign({}, offline.albums); delete o[albumId]; offline = { albums: o, comics: offline.comics, videos: offline.videos }
    status = "Offline copy removed"
  }
  function pinComics() {
    if (pinProc.running) return
    var prog = cprogress[cpath]
    status = "Saving chapters for offline…"; failed = false
    pinProc.command = ["python3", "-I", helper, "comic-pin", cpath, "10", prog ? prog.chapter : ""]
    pinProc.running = true
  }
  function unpinComics() {
    Quickshell.execDetached(["python3", "-I", helper, "comic-unpin", cpath])
    var o = Object.assign({}, offline.comics); delete o[cpath]; offline = { albums: offline.albums, comics: o }
    status = "Offline chapters removed"
  }
  property string squery: ""               // global search
  property var sresults: []                // flat list: {kind:"header"|"album"|"video"|"comic"|"file", ...}
  property string sinfo: ""
  readonly property var mediaExt: ["mp4","mkv","mov","avi","webm","m4v","wmv","flv","mpg","mpeg","mp3","flac","m4a","wav","ogg","opus","aac","wma"]
  function isMedia(name) { var e = String(name).toLowerCase().split(".").pop(); return mediaExt.indexOf(e) !== -1 }

  function plain(v) { return String(v || "").replace(/[\u0000-\u001f\u007f]/g, " ") }
  function open() { if (path === "") path = rootPath; root.controller.show(); refresh(); playerAct("status"); vplayerAct("status"); loadRecent(); loadVideoProgress(); loadOffline(); loadSync(); loadCfg(); if (tab === "more") loadMore() }
  function close() { root.controller.hide() }
  function toggle() { root.opened ? close() : open() }
  function closeForPopoutSwitch() { close() }

  function fmtSize(n) {
    if (n < 1024) return n + " B"
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB"
    if (n < 1073741824) return (n / 1048576).toFixed(1) + " MB"
    if (n < 1099511627776) return (n / 1073741824).toFixed(2) + " GB"
    return (n / 1099511627776).toFixed(2) + " TB"
  }
  function join(dir, name) { return (dir === "/" ? "" : dir) + "/" + name }
  function parentPath() { var p = path.replace(/\/[^\/]*$/, ""); return p === "" ? "/" : p }
  function atRoot() { return path === "/" }

  function refresh(keepStatus) {
    if (tab === "search" || tab === "photos") return
    if (tab === "more") { loadMore(); return }
    if (listProc.running) return
    if (keepStatus !== true) { status = "Loading…"; failed = false }
    listProc.command = ["python3", "-I", helper, "ls", path]
    listProc.running = true
  }
  function enter(p) { path = p; refresh() }

  function download(entry) {
    if (xferProc.running) return
    status = "Downloading " + entry.name + "…"; failed = false; pendingAction = "download"
    xferProc.command = ["python3", "-I", helper, "get", join(path, entry.name), downloadDir]
    xferProc.running = true
  }
  property bool uploadMenuOpen: false
  property string pickKind: "files"
  function upload() { pickUpload("files") }
  function uploadFolder() { pickUpload("folders") }
  function pickUpload(kind) {
    uploadMenuOpen = false
    if (pickProc.running || uploadProc.running) return
    pickKind = kind
    var cmd = ["zenity", "--file-selection", "--multiple", "--separator=\n"]
    if (kind === "folders") cmd.push("--directory", "--title=Upload folders to UnifyDrive")
    else cmd.push("--title=Upload files to UnifyDrive")
    pickProc.command = cmd
    pickProc.running = true
  }
  function uploadFiles(files) {
    if (files.length === 0) return
    status = "Uploading…"; failed = false; pendingAction = "upload"
    uploadProc.command = ["python3", "-I", helper, "upload", path].concat(files)
    uploadProc.running = true
  }
  function newFolder() {
    uploadMenuOpen = false
    if (mkdirProc.running) return
    status = "Creating folder…"; failed = false
    mkdirProc.command = ["python3", "-I", helper, "mkdir", path]
    mkdirProc.running = true
  }
  function setTab(name) {
    tab = name
    if (name === "music" && allAlbums.length === 0) loadAlbums(false)
    if (name === "video" && vlibs.length === 0) loadVideoLibs()
    if (name === "comics") { if (cseries.length === 0) loadComics(); else loadComicProgress() }
    if (name === "search") Qt.callLater(function() { globalSearch.focusInput() })
    if (name === "photos" && photos.length === 0) loadPhotos(0)
    if (name === "more") loadMore()
    if ((name === "music" || name === "video" || name === "search" || name === "photos") && coverBase === "" && !coverProc.running) coverProc.running = true
  }
  function runSearch() {
    var q = squery.trim()
    if (q.length < 2) { sresults = []; sinfo = ""; return }
    if (searchProc.running) { searchTimer.restart(); return }
    sinfo = "Searching…"; failed = false
    searchProc.command = ["python3", "-I", helper, "search", q, rootPath, comicsPath]
    searchProc.running = true
  }
  function openResult(r) {
    if (r.kind === "album") { setTab("music"); openAlbum({ id: r.id, name: r.title, cover: r.cover }) }
    else if (r.kind === "video") { setTab("video"); openCollection({ id: r.id, title: r.title, cls: r.cls }) }
    else if (r.kind === "comic") { setTab("comics"); openSeries({ path: r.path, name: r.title }) }
    else if (r.kind === "file") {
      if (r.dir) { setTab("files"); enter(r.path) }
      else if (isMedia(r.title)) playFile({ name: r.title, _path: r.path })
      else { setTab("files"); enter(r.path.replace(/\/[^\/]*$/, "") || "/") }
    }
  }
  function loadComics() {
    if (cseriesProc.running) return
    status = "Loading comics…"; failed = false
    cseriesProc.command = ["python3", "-I", helper, "comic-series", comicsPath]
    cseriesProc.running = true
    loadComicProgress()
  }
  function loadComicProgress() { if (!cprogProc.running) cprogProc.running = true }
  function openSeries(s) {
    if (cchProc.running) return
    cpath = s.path; cname = s.name; cchapters = []
    status = "Loading…"; failed = false
    cchProc.command = ["python3", "-I", helper, "comic-chapters", s.path]
    cchProc.running = true
  }
  function closeSeries() { cpath = ""; cchapters = []; status = ""; loadComicProgress() }
  function readComic(series, chapter) {
    Quickshell.execDetached(["python3", "-I", helper, "comic-read", series, chapter || ""])
    close()
  }
  // chapters before the one you are on are counted as read
  function chapterRead(i) {
    var p = cprogress[cpath]
    if (!p) return false
    for (var k = 0; k < cchapters.length; k++) if (cchapters[k].path === p.chapter) return i < k
    return false
  }
  function chapterName(path) { return String(path || "").split("/").pop().replace(/\.[^.]+$/, "") }
  function loadVideoLibs() {
    if (vlibsProc.running) return
    status = "Loading videos…"; failed = false
    vlibsProc.command = ["python3", "-I", helper, "video-libs"]
    vlibsProc.running = true
  }
  function selectLib(id) { vlib = id; vall = []; vshown = []; vcollId = ""; vitems = []; loadVideoList() }
  function loadVideoList() {
    if (vlistProc.running) return
    status = "Loading…"; failed = false
    vlistProc.command = ["python3", "-I", helper, "video-list", vlib]
    vlistProc.running = true
  }
  function applyVideoFilter() {
    var q = vquery.trim().toLowerCase()
    if (q === "") { vshown = vall; return }
    var words = q.split(/\s+/)
    vshown = vall.filter(function(v) {
      var hay = (v.title + " " + v.genres + " " + v.year).toLowerCase()
      for (var i = 0; i < words.length; i++) if (hay.indexOf(words[i]) === -1) return false
      return true
    })
  }
  function openCollection(c) {
    if (vitemsProc.running) return
    vcollId = c.id; vcollTitle = c.title; vcollCls = c.cls; vitems = []
    status = "Loading…"; failed = false
    vitemsProc.command = ["python3", "-I", helper, "video-items", c.id, c.cls]
    vitemsProc.running = true
  }
  function closeCollection() { vcollId = ""; vitems = []; status = "" }
  function playVideo(path, title, fromStart, queue) {
    status = "Opening " + plain(title) + "…"; failed = false; pendingAction = "video"
    mediaProc.command = ["python3", "-I", helper, "play", path].concat(fromStart ? ["--from-start"] : []).concat(queue && queue.length > 0 ? ["--queue=" + JSON.stringify(queue)] : [])
    mediaProc.running = true
  }
  function loadVideoProgress() { if (!vprogProc.running) { vprogProc.command = ["python3", "-I", helper, "video-progress"]; vprogProc.running = true } }
  function loadRecent() { if (!recentProc.running) { recentProc.command = ["python3", "-I", helper, "continue"]; recentProc.running = true } }
  function vplayerAct(action) {
    if (vplayerProc.running) return
    vplayerProc.command = ["python3", "-I", helper, "vplayer", action]
    vplayerProc.running = true
  }
  function langName(code) {
    var m = { eng: "English", en: "English", jpn: "Japanese", ja: "Japanese", spa: "Spanish", es: "Spanish", fre: "French", fra: "French", ger: "German", deu: "German", ita: "Italian", por: "Portuguese", rus: "Russian", chi: "Chinese", zho: "Chinese", kor: "Korean", ara: "Arabic", und: "" }
    return m[String(code).toLowerCase()] !== undefined ? m[String(code).toLowerCase()] : String(code)
  }
  function trackLabel(t) {
    if (!t) return "Off"
    var n = [langName(t.lang), t.title].filter(function(x) { return x }).join(" ")
    return (n || ("Track " + t.id)) + (t.external ? " (file)" : "")
  }
  function trackList(type) { return (vnow.tracks || []).filter(function(t) { return t.type === type }) }
  function currentTrack(type) { var l = trackList(type); for (var i = 0; i < l.length; i++) if (l[i].selected) return l[i]; return null }
  function cycleTrack(type) {
    var l = trackList(type); if (l.length === 0) return
    var cur = currentTrack(type), idx = -1
    for (var i = 0; i < l.length; i++) if (cur && l[i].id === cur.id) idx = i
    var next = idx + 1
    if (next >= l.length) { if (type === "sub") { vplayerAct("sid:no"); return } next = 0 }
    vplayerAct((type === "sub" ? "sid:" : "aid:") + l[next].id)
  }
  function resumeText(path) {
    var v = vprog[path]
    if (!v) return ""
    return v.done ? "watched" : (v.pos > 15 ? "resume " + fmtTime(v.pos) : "")
  }
  function openRecent(r) {
    if (r.kind === "video") playVideo(r.path, r.title, false)
    else if (r.kind === "comic") readComic(r.series, r.chapter)
    else if (r.kind === "music") { setTab("music"); openAlbum({ id: r.id, name: r.title, cover: r.cover }) }
  }
  function fmtDur(s) {
    if (!s) return ""
    var h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60)
    return h > 0 ? h + "h " + (m < 10 ? "0" : "") + m + "m" : m + " min"
  }
  function loadAlbums(refresh) {
    if (albumsProc.running) return
    status = refresh ? "Refreshing albums…" : "Loading albums…"; failed = false
    albumsProc.command = ["python3", "-I", helper, "albums-all"].concat(refresh ? ["--refresh"] : [])
    albumsProc.running = true
  }
  function applyFilter() {
    var q = query.trim().toLowerCase()
    if (q === "") { albums = allAlbums; return }
    var words = q.split(/\s+/)
    albums = allAlbums.filter(function(a) {
      var hay = (a.name + " " + a.artist).toLowerCase()
      for (var i = 0; i < words.length; i++) if (hay.indexOf(words[i]) === -1) return false
      return true
    })
  }
  function coverUrl(a) { return (coverBase !== "" && a.cover) ? coverBase + "/cover?p=" + encodeURIComponent(a.cover) : "" }
  function openAlbum(a) {
    albumId = a.id; albumName = a.name; albumCover = a.cover || ""; songs = []
    status = "Loading songs…"; failed = false
    songsProc.command = ["python3", "-I", helper, "songs", a.id]
    songsProc.running = true
  }
  function closeAlbum() { albumId = ""; songs = []; status = "" }
  function playAlbum(startIndex) {
    status = "Starting…"; failed = false; pendingAction = "music"
    mediaProc.command = ["python3", "-I", helper, "play-album", albumId, String(startIndex), albumName, albumCover]
    mediaProc.running = true
  }
  function playFile(entry) {
    var ext = String(entry.name).toLowerCase().split(".").pop()
    var audio = ["mp3","flac","m4a","wav","ogg","opus","aac","wma"].indexOf(ext) !== -1
    status = "Streaming " + entry.name + "…"; failed = false; pendingAction = audio ? "stream" : "video"
    mediaProc.command = ["python3", "-I", helper, "play", entry._path ? entry._path : join(path, entry.name)]
    mediaProc.running = true
  }
  function playerAct(action) {
    if (playerProc.running) return
    playerProc.command = ["python3", "-I", helper, "player", action]
    playerProc.running = true
  }
  function fmtTime(s) {
    if (s === undefined || s === null || isNaN(s)) return "–:––"
    var m = Math.floor(s / 60), r = Math.floor(s % 60)
    return m + ":" + (r < 10 ? "0" : "") + r
  }

  function openWebUi() {
    // Signed in with the same session as the panel (udrive.py injects the cookies); a Hyprland rule floats the window.
    Quickshell.execDetached(["python3", "-I", helper, "webui"])
    close()
  }

  function login() {
    if (loginProc.running) return
    status = "Logging in… answer the dialogs"; failed = false
    loginProc.command = ["python3", "-I", helper, "login"]
    loginProc.running = true
  }

  Process {
    id: loginProc
    stdout: StdioCollector { id: loginOut; waitForEnd: true }
    stderr: StdioCollector { id: loginErr; waitForEnd: true }
    onExited: function(code) {
      var out = String(loginOut.text).trim()
      if (code === 0 && out.indexOf('"ok": true') !== -1) { root.refresh(); return }
      root.failed = true
      root.status = root.plain(out || loginErr.text || "Login failed")
    }
  }

  IpcHandler {
    target: "eg.unifydrive.ui"
    function tab(name: string): string {
      if (name !== "files" && name !== "music" && name !== "video" && name !== "comics") return "unknown tab"
      root.open(); root.setTab(name); return "ok"
    }
    function uploadmenu(): string { root.open(); root.setTab("files"); root.uploadMenuOpen = true; return "ok" }
    function vsearch(text: string): string { root.open(); root.setTab("video"); root.vquery = text; root.applyVideoFilter(); return String(root.vshown.length) }
    function vlib(name: string): string {
      root.open(); root.setTab("video")
      for (var i = 0; i < root.vlibs.length; i++) if (root.vlibs[i].name.toLowerCase() === name.toLowerCase()) { root.selectLib(root.vlibs[i].id); return "ok" }
      return "no such library"
    }
    function vopen(title: string): string {
      for (var i = 0; i < root.vall.length; i++) if (root.vall[i].title.toLowerCase().indexOf(title.toLowerCase()) !== -1) { root.openCollection(root.vall[i]); return root.vall[i].title }
      return "not found"
    }
    function comicopen(title: string): string {
      for (var i = 0; i < root.cseries.length; i++) if (root.cseries[i].name.toLowerCase().indexOf(title.toLowerCase()) !== -1) { root.open(); root.setTab("comics"); root.openSeries(root.cseries[i]); return root.cseries[i].name }
      return "not found"
    }
    function comicread(): string { root.readComic(root.cpath, root.cchapters.length > 0 ? root.cchapters[0].path : ""); return root.cpath }
    function webui(): string { root.openWebUi(); return "ok" }
    function dropfiles(paths: string): string { root.open(); root.setTab("files"); root.uploadFiles(paths.split("\n").filter(function(x) { return x !== "" })); return "ok" }
    function queue(): string { root.open(); root.queueOpen = !root.queueOpen; return String(root.queueOpen) }
    function photos(): string { root.open(); root.setTab("photos"); return "ok" }
    function more(section: string): string { root.open(); root.setTab("more"); root.setSec(section === "" ? "transfers" : section); return root.moreSec }
    function share(remotePath: string): string { root.open(); var i = remotePath.lastIndexOf("/"); root.path = remotePath.substring(0, i) || "/"; root.shareEntry({ name: remotePath.substring(i + 1) }); return "ok" }
    function capture(mode: string): string { root.captureNow(mode === "screen" ? "screen" : "region"); return "ok" }
    function find(text: string): string { root.open(); root.setTab("search"); root.squery = text; root.runSearch(); return "ok" }
    function search(text: string): string { root.setTab("music"); root.query = text; root.applyFilter(); return String(root.albums.length) }
  }

  Process {
    id: albumsProc
    stdout: StdioCollector { id: albumsOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(albumsOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        root.albumCount = d.count; root.allAlbums = d.list
        root.applyFilter(); root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read albums" }
    }
  }
  Process {
    id: coverProc
    command: ["python3", "-I", root.helper, "cover-proxy"]
    stdout: SplitParser {
      onRead: function(line) {
        try { var o = JSON.parse(line); root.coverBase = "http://127.0.0.1:" + o.port + "/" + o.token } catch (e) {}
      }
    }
    onExited: root.coverBase = ""
  }
  Process {
    id: cseriesProc
    stdout: SplitParser {
      onRead: function(line) {
        var o
        try { o = JSON.parse(line) } catch (e) { return }
        if (o.error) { root.failed = true; root.status = root.plain(o.error); return }
        if (o.done) return
        var list = root.cseries.slice(), at = -1
        for (var i = 0; i < list.length; i++) if (list[i].path === o.path) at = i
        if (at >= 0) list[at] = Object.assign({}, list[at], o); else list.push(o)
        root.cseries = list
      }
    }
    onExited: function(code) {
      if (root.status === "Loading comics…") root.status = root.cseries.length === 0 ? "No comics found in " + root.comicsPath : ""
    }
  }
  Process {
    id: cchProc
    stdout: StdioCollector { id: cchOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(cchOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); root.cpath = ""; return }
        root.cchapters = d; root.status = d.length === 0 ? "No chapters (.cbz) in this folder" : ""
      } catch (e) { root.failed = true; root.status = "Could not read chapters"; root.cpath = "" }
    }
  }
  Process {
    id: cprogProc
    command: ["python3", "-I", root.helper, "comic-progress"]
    stdout: StdioCollector { id: cprogOut; waitForEnd: true }
    onExited: function(code) { try { root.cprogress = JSON.parse(String(cprogOut.text).trim()) } catch (e) {} }
  }
  Process {
    id: vlibsProc
    stdout: StdioCollector { id: vlibsOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(vlibsOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        root.vlibs = d; root.status = ""; root.selectLib(root.vlib)
      } catch (e) { root.failed = true; root.status = "Could not read video libraries" }
    }
  }
  Process {
    id: vlistProc
    stdout: StdioCollector { id: vlistOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(vlistOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        root.vall = d; root.applyVideoFilter(); root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read the video list" }
    }
  }
  Process {
    id: vitemsProc
    stdout: StdioCollector { id: vitemsOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(vitemsOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); root.vcollId = ""; return }
        if (d.length === 0) { root.status = "No playable files"; root.vcollId = ""; return }
        if (d.length === 1) { root.vcollId = ""; root.playVideo(d[0].path, root.vcollTitle); return }  // movie / single video: just play
        root.vitems = d; root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read episodes"; root.vcollId = "" }
    }
  }
  Process {
    id: songsProc
    stdout: StdioCollector { id: songsOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(songsOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        root.songs = d; root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read songs" }
    }
  }
  Process {
    id: mediaProc
    stdout: StdioCollector { id: mediaOut; waitForEnd: true }
    onExited: function(code) {
      var out = String(mediaOut.text).trim()
      if (code !== 0 || out.indexOf('"error"') !== -1) { root.failed = true; root.status = root.plain(out || "Could not start playback"); return }
      root.status = root.pendingAction === "music" ? "" : "Opened in mpv"
      root.playerAct("status")
      // A video window opens underneath this full-screen overlay and never gets keyboard focus while it is up
      // (no pause / seek / close, and the panel is still there when the video ends). Get out of the way.
      if (root.pendingAction === "video") root.close()
    }
  }
  Process {
    id: playerProc
    stdout: StdioCollector { id: playerOut; waitForEnd: true }
    onExited: function(code) {
      try { root.now = JSON.parse(String(playerOut.text).trim()) } catch (e) { root.now = { playing: false } }
    }
  }
  Timer {
    interval: 2000; repeat: true
    running: root.opened && root.now.playing === true
    onTriggered: root.playerAct("status")
  }
  Process {
    id: vplayerProc
    stdout: StdioCollector { id: vplayerOut; waitForEnd: true }
    onExited: function(code) {
      try { root.vnow = JSON.parse(String(vplayerOut.text).trim()) } catch (e) { root.vnow = { playing: false } }
    }
  }
  Timer {
    interval: 2000; repeat: true
    running: root.opened && root.vnow.playing === true
    onTriggered: root.vplayerAct("status")
  }
  Process {
    id: nasProc
    command: ["python3", "-I", root.helper, "nas-status"]
    stdout: StdioCollector { id: nasOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(nasOut.text).trim())
        root.nas = d.error ? { online: false, error: d.error } : d
      } catch (e) { root.nas = { online: false } }
    }
  }
  Timer { interval: 180000; repeat: true; running: true; triggeredOnStart: true; onTriggered: if (!nasProc.running) nasProc.running = true }
  // ---- More tab processes ----
  Process {
    id: tasksProc
    stdout: StdioCollector { id: tasksOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(tasksOut.text).trim()); if (!d.error) { root.tasks = d.tasks; root.tasksActive = d.active } else { root.failed = true; root.status = root.plain(d.error) } } catch (e) {} }
  }
  Process { id: taskActProc; onExited: root.loadMore() }
  Process {
    id: sharesProc
    stdout: StdioCollector { id: sharesOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(sharesOut.text).trim()); if (!d.error) root.shares = d.shares } catch (e) {} }
  }
  Process {
    id: shareMakeProc
    stdout: StdioCollector { id: shareMakeOut; waitForEnd: true }
    onExited: {
      try {
        var d = JSON.parse(String(shareMakeOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        root.copyText(root.shareText(d))
        root.lastShare = d.link
        root.status = "Share link copied" + (d.pass ? "  ·  password " + d.pass : "") + "  ·  " + d.days + " days"
        if (root.tab === "more" && root.moreSec === "shares") root.loadMore()
      } catch (e) { root.failed = true; root.status = "Could not create the share link" }
    }
  }
  Process { id: shareDelProc; onExited: root.loadMore() }
  Process {
    id: appsProc
    stdout: StdioCollector { id: appsOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(appsOut.text).trim()); if (!d.error) root.apps = d.apps } catch (e) {} }
  }
  Process {
    id: cfgProc
    stdout: StdioCollector { id: cfgOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(cfgOut.text).trim()); if (!d.error) root.cfg = d } catch (e) {} }
  }
  Process {
    id: cfgSetProc
    stdout: StdioCollector { id: cfgSetOut; waitForEnd: true }
    onExited: {
      try { var d = JSON.parse(String(cfgSetOut.text).trim()); if (!d.error) root.cfg = d } catch (e) {}
      if (root.cfgPending) { var p = root.cfgPending; root.cfgPending = null; root.setSetting(p[0], p[1]) }
    }
  }
  Process {
    id: agentProc
    stdout: StdioCollector { id: agentOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(agentOut.text).trim()); if (!d.error) root.agent = d } catch (e) {} }
  }
  Process {
    id: agentSvcProc
    stdout: StdioCollector { id: agentSvcOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(agentSvcOut.text).trim()); if (!d.error) root.agent = d } catch (e) {} root.loadCfg() }
  }
  Process {
    id: vpinProc
    stdout: SplitParser {
      onRead: function(line) {
        try {
          var o = JSON.parse(line)
          if (o.pin !== undefined) root.status = "Saving for offline… " + o.pin + "%"
          else if (o.error) { root.failed = true; root.status = root.plain(o.error) }
        } catch (e) {}
      }
    }
    onExited: function(code) { if (code === 0) root.status = "Saved for offline viewing"; root.loadOffline() }
  }
  Timer {
    interval: 3000; repeat: true
    running: root.opened && root.tab === "more" && (root.moreSec === "transfers" || root.moreSec === "settings")
    onTriggered: root.loadMore()
  }
  Process {
    id: offlineProc
    stdout: StdioCollector { id: offlineOut; waitForEnd: true }
    onExited: function(code) { try { root.offline = JSON.parse(String(offlineOut.text).trim()) } catch (e) {} }
  }
  Process {
    id: pinProc
    stdout: SplitParser {
      onRead: function(line) {
        try {
          var o = JSON.parse(line)
          if (o.pin !== undefined) root.status = "Saving offline " + o.pin + " of " + o.of + (o.title ? "  ·  " + root.plain(o.title) : (o.chapter ? "  ·  " + root.plain(o.chapter) : ""))
          else if (o.error) { root.failed = true; root.status = root.plain(o.error) }
        } catch (e) {}
      }
    }
    onExited: function(code) {
      if (!root.failed) root.status = code === 0 ? "Saved for offline use" : "Could not save offline"
      root.failed = code !== 0
      root.loadOffline()
    }
  }
  Process {
    id: photoProc
    property int chunkStart: 0
    stdout: StdioCollector { id: photoOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(photoOut.text).trim())
        if (d.error) { root.failed = true; root.status = root.plain(d.error); return }
        var at = photoProc.chunkStart
        var add = d.list.map(function(p) { return Object.assign({ chunk: at }, p) })
        root.photos = root.photos.concat(add)
        root.photoTotal = d.total; root.photoNext = d.next
        if (root.status === "Loading photos…") root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read the photo list" }
    }
  }
  Process {
    id: syncListProc
    stdout: StdioCollector { id: syncListOut; waitForEnd: true }
    onExited: function(code) { try { root.sync = JSON.parse(String(syncListOut.text).trim()) } catch (e) {} }
  }
  Process {
    id: syncPickProc
    stdout: StdioCollector { id: syncPickOut; waitForEnd: true }
    onExited: function(code) {
      var dir = String(syncPickOut.text).trim()
      if (code !== 0 || dir === "") return
      root.syncLocal = dir
      var name = dir.replace(/\/+$/, "").split("/").pop()
      syncAddProc.command = ["python3", "-I", root.helper, "sync-add", dir, root.join(root.path, name), name]
      syncAddProc.running = true
    }
  }
  Process {
    id: syncAddProc
    stdout: StdioCollector { id: syncAddOut; waitForEnd: true }
    onExited: function(code) {
      var out = String(syncAddOut.text).trim()
      if (code !== 0 || out.indexOf('"error"') !== -1) { root.failed = true; root.status = root.plain(out || "Could not add the sync folder"); return }
      root.loadSync()
      root.syncNow(root.syncLocal.replace(/\/+$/, "").split("/").pop())
    }
  }
  Process {
    id: syncRunProc
    stdout: StdioCollector { id: syncRunOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var r = JSON.parse(String(syncRunOut.text).trim())
        if (r.error) { root.failed = true; root.status = root.plain(r.error) }
        else {
          var up = 0, bad = 0
          for (var k in r) { up += r[k].uploaded || 0; if (r[k].error) bad++; else bad += (r[k].failed || []).length }
          root.failed = bad > 0
          root.status = "Sync done: " + up + " uploaded" + (bad > 0 ? ", " + bad + " problem(s)" : "")
        }
      } catch (e) { root.failed = true; root.status = "Sync failed" }
      root.loadSync()
      root.refresh(true)
    }
  }
  Process {
    id: syncTimerProc
    stdout: StdioCollector { id: syncTimerOut; waitForEnd: true }
    onExited: function(code) {
      try { root.sync = JSON.parse(String(syncTimerOut.text).trim()); root.status = root.sync.timer ? "Auto-sync is on" : "Auto-sync is off" } catch (e) {}
    }
  }
  Timer { id: searchTimer; interval: 450; onTriggered: root.runSearch() }
  Process {
    id: searchProc
    stdout: StdioCollector { id: searchOut; waitForEnd: true }
    onExited: function(code) {
      try {
        var d = JSON.parse(String(searchOut.text).trim())
        if (d.error) { root.failed = true; root.sinfo = root.plain(d.error); return }
        var flat = [], add = function(title, list) { if (list.length > 0) { flat.push({ kind: "header", title: title }); flat = flat.concat(list) } }
        add("COMICS (" + d.comics.length + ")", d.comics)
        add("VIDEOS (" + d.videos.length + ")", d.videos)
        add("ALBUMS (" + d.albums.length + ")", d.albums)
        add("FILES (" + (d.files_total > d.files.length ? d.files.length + " of " + d.files_total : d.files.length) + ")", d.files)
        root.sresults = flat
        root.sinfo = flat.length === 0 ? "No matches" : ""
      } catch (e) { root.sinfo = "Search failed" }
    }
  }
  Process {
    id: vprogProc
    stdout: StdioCollector { id: vprogOut; waitForEnd: true }
    onExited: function(code) { try { root.vprog = JSON.parse(String(vprogOut.text).trim()) } catch (e) {} }
  }
  Process {
    id: recentProc
    stdout: StdioCollector { id: recentOut; waitForEnd: true }
    onExited: function(code) { try { root.recent = JSON.parse(String(recentOut.text).trim()) } catch (e) { root.recent = [] } }
  }

  Process {
    id: listProc
    stdout: StdioCollector { id: listOut; waitForEnd: true }
    onExited: function(code) {
      var txt = String(listOut.text).trim()
      try {
        var d = JSON.parse(txt)
        if (d.error) { root.failed = true; root.status = root.plain(d.error); root.entries = []; return }
        d.sort(function(a, b) { return a.dir === b.dir ? a.name.localeCompare(b.name) : (a.dir ? -1 : 1) })
        root.entries = d; if (root.status === "Loading…") root.status = ""
      } catch (e) { root.failed = true; root.status = "Could not read listing"; root.entries = [] }
    }
  }
  Process {
    id: pickProc
    stdout: StdioCollector { id: pickOut; waitForEnd: true }
    onExited: function(code) {
      if (code !== 0) return
      var files = String(pickOut.text).split("\n").map(function(s) { return s.trim() }).filter(function(s) { return s !== "" })
      root.uploadFiles(files)
    }
  }
  Process {
    id: uploadProc
    stdout: StdioCollector { id: uploadOut; waitForEnd: true }
    stderr: SplitParser {
      onRead: function(line) {
        try {
          var o = JSON.parse(line)
          if (o.file !== undefined)
            root.status = "Uploading " + root.plain(o.file) + "  " + o.percent + "%" + (o.of > 1 ? "  (" + o.index + " of " + o.of + ")" : "")
        } catch (e) {}
      }
    }
    onExited: function(code) {
      var out = String(uploadOut.text).trim()
      var r = null
      try { r = JSON.parse(out) } catch (e) {}
      if (!r || code !== 0 || r.error) { root.failed = true; root.status = root.plain((r && r.error) || out || "Upload failed"); root.refresh(); return }
      var bits = [r.uploaded + " uploaded"]
      if (r.skipped) bits.push(r.skipped + " skipped")
      if (r.failed && r.failed.length) { root.failed = true; bits.push(r.failed.length + " failed: " + r.failed[0].file + " – " + r.failed[0].error) }
      root.status = root.plain(bits.join(", "))
      root.refresh(true)
    }
  }
  Process {
    id: mkdirProc
    stdout: StdioCollector { id: mkdirOut; waitForEnd: true }
    onExited: function(code) {
      var out = String(mkdirOut.text).trim()
      if (code !== 0 && out === "") { root.status = ""; return }  // cancelled
      if (out.indexOf('"error"') !== -1) { root.failed = true; root.status = root.plain(out); return }
      root.refresh()
    }
  }
  Process {
    id: xferProc
    stdout: StdioCollector { id: xferOut; waitForEnd: true }
    stderr: StdioCollector { id: xferErr; waitForEnd: true }
    onExited: function(code) {
      var out = String(xferOut.text).trim()
      var bad = code !== 0 || out.indexOf('"error"') !== -1
      root.failed = bad
      if (bad) { root.status = root.plain(out || xferErr.text || "Transfer failed"); return }
      root.status = "Saved to " + root.downloadDir
    }
  }

  KeyboardPanel {
    id: panel
    anchorItem: root.anchorItem
    owner: root.barIdentity
    bar: root.bar
    open: root.opened
    centerOnBar: false
    focusTarget: keyCatcher
    contentWidth: panel.fittedContentWidth(Style.space(380))
    contentHeight: panel.fittedContentHeight(Style.space(520), Style.space(560))

    PanelKeyCatcher {
      id: keyCatcher
      anchors.fill: parent
      onCloseRequested: root.close()
      onTabRequested: function(direction) { root.switchPanel(direction) }
      onTextKey: function(text) {
        if (root.typing) return
        if (text === "r" || text === "R") root.refresh()
        else if ((text === "u" || text === "U") && root.tab === "files") root.upload()
        else if (text === "b" || text === "B") { if (root.tab === "files") { if (!root.atRoot()) root.enter(root.parentPath()) } else if (root.tab === "music") root.closeAlbum(); else if (root.tab === "comics") root.closeSeries(); else root.closeCollection() }
      }

      // Drop files or folders from a file manager anywhere on the panel to upload them to the folder shown in Files.
      DropArea {
        id: dropArea
        anchors.fill: parent
        z: 50
        keys: ["text/uri-list"]
        onDropped: function(drop) {
          var files = []
          for (var i = 0; i < drop.urls.length; i++) {
            var u = String(drop.urls[i])
            if (u.indexOf("file://") === 0) files.push(decodeURIComponent(u.substring(7)))
          }
          drop.accept()
          root.uploadFiles(files)
        }
        Rectangle {
          anchors.fill: parent; visible: dropArea.containsDrag
          radius: Style.space(10); color: Qt.rgba(0, 0, 0, 0.55)
          border.width: 2; border.color: Color.accent
          Text {
            anchors.centerIn: parent; horizontalAlignment: Text.AlignHCenter
            text: "󰁝\nDrop to upload to\n" + root.path
            textFormat: Text.PlainText; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body
          }
        }
      }

      ColumnLayout {
        anchors.fill: parent
        spacing: Style.space(8)

        // tabs
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)
          Repeater {
            model: [ { id: "files", label: "󰉋", name: "Files" }, { id: "music", label: "󰝚", name: "Music" }, { id: "video", label: "󰕧", name: "Video" }, { id: "comics", label: "󰂺", name: "Comics" }, { id: "search", label: "󰍉", name: "Find" }, { id: "photos", label: "󰋩", name: "Photos" }, { id: "more", label: "󰒓", name: "More" } ]
            delegate: Item {
              Layout.fillWidth: true
              Layout.preferredWidth: root.tab === modelData.id ? Style.space(92) : Style.space(36)
              implicitHeight: Style.space(28)
              Rectangle {
                anchors.fill: parent; radius: Style.space(6)
                color: root.tab === modelData.id ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent"
              }
              Text {
                anchors.centerIn: parent; text: root.tab === modelData.id ? modelData.label + "  " + modelData.name : modelData.label
                color: root.tab === modelData.id ? root.foreground : root.dim
                font.family: root.fontFamily; font.pixelSize: Style.font.body
              }
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.setTab(modelData.id) }
            }
          }
        }

        // toolbar (per tab)
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(8)
          Btn {
            glyph: "󰁞"; tip: "Up (b)"
            enabled: (root.tab === "search" || root.tab === "photos" || root.tab === "more") ? false : root.tab === "files" ? !root.atRoot() : (root.tab === "music" ? root.albumId !== "" : (root.tab === "comics" ? root.cpath !== "" : root.vcollId !== ""))
            onClicked: root.tab === "files" ? root.enter(root.parentPath()) : (root.tab === "music" ? root.closeAlbum() : (root.tab === "comics" ? root.closeSeries() : root.closeCollection()))
          }
          Text {
            Layout.fillWidth: true
            text: root.tab === "more" ? "More" : root.tab === "photos" ? (root.photoTotal > 0 ? root.photoTotal + " photos" : "Photos") : root.tab === "search" ? (root.sinfo !== "" ? root.sinfo : "Search everything") : root.tab === "comics" ? (root.cpath !== "" ? root.cname : "Comics (" + root.cseries.length + ")")
                  : root.tab === "video" ? (root.vcollId !== "" ? root.vcollTitle : (root.vquery.trim() !== "" ? root.vshown.length + " of " + root.vall.length + " videos" : root.vall.length + " videos"))
                  : root.tab === "files" ? root.path : (root.albumId !== "" ? root.albumName : (root.query.trim() !== "" ? root.albums.length + " of " + root.albumCount + " albums" : "Albums (" + root.albumCount + ")"))
            textFormat: Text.PlainText; elide: Text.ElideLeft
            color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body
          }
          Btn { glyph: "󰖟"; tip: "Open full UnifyDrive UI"; onClicked: root.openWebUi() }
          Btn { visible: root.tab === "files"; glyph: "󰁝"; tip: "Upload (u)"; onClicked: root.uploadMenuOpen = !root.uploadMenuOpen }
          Btn { glyph: "󰑐"; tip: "Refresh (r)"; onClicked: root.tab === "more" ? root.loadMore() : root.tab === "photos" ? root.loadPhotos(0) : root.tab === "search" ? root.runSearch() : root.tab === "files" ? root.refresh() : (root.tab === "comics" ? (root.cpath !== "" ? root.openSeries({ path: root.cpath, name: root.cname }) : (root.cseries = [], root.loadComics())) : root.tab === "video" ? (root.vcollId !== "" ? root.openCollection({ id: root.vcollId, title: root.vcollTitle, cls: root.vcollCls }) : root.loadVideoList()) : (root.albumId !== "" ? root.openAlbum({ id: root.albumId, name: root.albumName }) : root.loadAlbums(true))) }
        }

        Rectangle {
          visible: root.uploadMenuOpen && root.tab === "files"
          Layout.fillWidth: true
          implicitHeight: menuCol.implicitHeight + Style.space(8)
          radius: Style.space(8)
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
          Column {
            id: menuCol
            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            Repeater {
              model: [ { label: "Upload files…", act: "files" }, { label: "Upload folder…", act: "folders" }, { label: "New folder…", act: "mkdir" },
                       { label: "Sync a local folder to here…", act: "syncadd" },
                       { label: "Screenshot a region → NAS", act: "capregion" }, { label: "Screenshot the screen → NAS", act: "capscreen" } ]
                     .concat(root.sync.jobs.length > 0 ? [ { label: "Sync now  (" + root.syncSummary() + ")", act: "syncnow" },
                                                          { label: root.sync.timer ? "Auto-sync every 30 min: on (turn off)" : "Turn on auto-sync (every 30 min)", act: "synctimer" } ] : [])
              delegate: Item {
                width: menuCol.width; height: Style.space(30)
                Rectangle { anchors.fill: parent; anchors.margins: 2; radius: Style.space(5); color: mi.containsMouse ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent" }
                Text { anchors.left: parent.left; anchors.leftMargin: Style.space(12); anchors.verticalCenter: parent.verticalCenter; text: modelData.label; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                MouseArea {
                  id: mi; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                  onClicked: modelData.act === "files" ? root.upload() : modelData.act === "folders" ? root.uploadFolder() : modelData.act === "capregion" ? root.captureNow("region") : modelData.act === "capscreen" ? root.captureNow("screen") : modelData.act === "syncadd" ? root.syncPick() : modelData.act === "syncnow" ? root.syncNow("") : modelData.act === "synctimer" ? root.syncTimer() : root.newFolder()
                }
              }
            }
          }
        }

        Text {
          Layout.fillWidth: true
          visible: root.status !== ""
          text: root.status; textFormat: Text.PlainText; wrapMode: Text.Wrap
          color: root.failed ? root.urgent : root.dim
          font.family: root.fontFamily; font.pixelSize: Style.font.caption
        }

        Btn {
          visible: root.failed
          Layout.alignment: Qt.AlignHCenter
          glyph: "󰍂  Log in"
          implicitWidth: Style.space(110)
          onClicked: root.login()
        }

        // ---- Continue where you left off ----
        RowLayout {
          Layout.fillWidth: true
          spacing: Style.space(6)
          visible: root.recent.length > 0 && root.tab !== "search" && root.tab !== "more" && root.query.trim() === "" && root.vquery.trim() === ""
                   && root.albumId === "" && root.vcollId === "" && root.cpath === "" && !(root.tab === "files" && root.path !== root.rootPath)
          Text { text: "󰐊"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
          Repeater {
            model: root.recent.slice(0, 3)
            delegate: Rectangle {
              Layout.fillWidth: true
              Layout.preferredWidth: 1
              implicitHeight: Style.space(26)
              radius: Style.space(6)
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, rma.containsMouse ? 0.18 : 0.10)
              Text {
                anchors.fill: parent; anchors.leftMargin: Style.space(6); anchors.rightMargin: Style.space(6)
                verticalAlignment: Text.AlignVCenter
                text: (modelData.kind === "video" ? "󰕧 " : modelData.kind === "comic" ? "󰂺 " : "󰝚 ") + modelData.title
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              MouseArea {
                id: rma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                onClicked: root.openRecent(modelData)
              }
            }
          }
        }

        // ---- Photos ----
        GridView {
          id: photoGrid
          visible: root.tab === "photos"
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.photos
          cellWidth: Math.floor(width / 3); cellHeight: cellWidth
          cacheBuffer: 600
          onAtYEndChanged: if (atYEnd && root.photoNext > 0 && root.photoNext < root.photoTotal) root.loadPhotos(root.photoNext)
          delegate: Item {
            width: photoGrid.cellWidth; height: photoGrid.cellHeight
            Rectangle {
              anchors.fill: parent; anchors.margins: 2; radius: Style.space(4); clip: true
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
              Image {
                anchors.fill: parent
                source: root.thumbUrl(modelData.path, "small")
                asynchronous: true; cache: true; fillMode: Image.PreserveAspectCrop
                sourceSize.width: 260; sourceSize.height: 260
              }
              Text {
                visible: modelData.video
                anchors.centerIn: parent; text: "󰐊"; color: "#ffffff"
                font.family: root.fontFamily; font.pixelSize: Style.font.icon * 2
              }
              Rectangle { anchors.fill: parent; color: "white"; opacity: pma.containsMouse ? 0.12 : 0 }
              MouseArea { id: pma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openPhoto(modelData) }
            }
          }
        }

        // ---- Search (everything) ----
        SearchField {
          id: globalSearch
          visible: root.tab === "search"
          placeholder: "Search files, albums, videos, comics"
          value: root.squery
          onEdited: function(v) { root.squery = v; searchTimer.restart() }
        }
        ListView {
          visible: root.tab === "search"
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.sresults
          cacheBuffer: 400
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: sra.containsMouse && modelData.kind !== "header"
            implicitHeight: modelData.kind === "header" ? Style.space(26) : Style.space(40)
            MouseArea { id: sra; anchors.fill: parent; enabled: modelData.kind !== "header"; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openResult(modelData) }
            Text {
              visible: modelData.kind === "header"
              anchors.verticalCenter: parent.verticalCenter; anchors.left: parent.left; anchors.leftMargin: Style.space(10)
              text: modelData.title; textFormat: Text.PlainText
              color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: true
            }
            RowLayout {
              visible: modelData.kind !== "header"
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
              Rectangle {
                Layout.preferredWidth: Style.space(30); Layout.preferredHeight: Style.space(30)
                radius: Style.space(4); clip: true
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                Text {
                  anchors.centerIn: parent; visible: srimg.status !== Image.Ready
                  text: modelData.kind === "album" ? "󰝚" : modelData.kind === "video" ? "󰕧" : modelData.kind === "comic" ? "󰂺" : (modelData.dir ? "󰉋" : (root.isMedia(modelData.title || "") ? "󰈟" : "󰈔"))
                  color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon
                }
                Image {
                  id: srimg
                  anchors.fill: parent
                  source: (modelData.kind === "album" || modelData.kind === "video") ? root.coverUrl({ cover: modelData.cover }) : ""
                  asynchronous: true; cache: true; fillMode: Image.PreserveAspectCrop
                  sourceSize.width: 64; sourceSize.height: 64
                }
              }
              ColumnLayout {
                Layout.fillWidth: true; spacing: 0
                Text { Layout.fillWidth: true; text: modelData.title || ""; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                Text {
                  Layout.fillWidth: true
                  text: modelData.kind === "file" ? (modelData.path.replace(/\/[^\/]*$/, "") + (modelData.dir ? "" : "  ·  " + root.fmtSize(modelData.size))) : (modelData.sub || "")
                  textFormat: Text.PlainText; elide: Text.ElideLeft; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }

        // ---- Files ----
        ListView {
          visible: root.tab === "files"
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.entries
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: ma.containsMouse
            implicitHeight: Style.space(34)
            readonly property bool media: !modelData.dir && root.isMedia(modelData.name)
            MouseArea {
              id: ma
              anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
              onClicked: modelData.dir ? root.enter(root.join(root.path, modelData.name))
                         : (media ? root.playFile(modelData) : root.download(modelData))
            }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10)
              spacing: Style.space(8)
              Text { text: modelData.dir ? "󰉋" : (media ? "󰐊" : "󰈔"); color: media ? Color.accent : root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
              Text {
                Layout.fillWidth: true
                text: modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body
              }
              Text {
                text: modelData.dir ? "" : root.fmtSize(modelData.size)
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              Item {
                implicitWidth: Style.space(24); implicitHeight: Style.space(24)
                Text { anchors.centerIn: parent; text: "󰒗"; color: shm.containsMouse ? Color.accent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                MouseArea { id: shm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.shareEntry(modelData) }
              }
              Item {
                visible: !modelData.dir
                implicitWidth: Style.space(24); implicitHeight: Style.space(24)
                Text { anchors.centerIn: parent; text: "󰇚"; color: dl.containsMouse ? Color.accent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                MouseArea { id: dl; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.download(modelData) }
              }
            }
          }
        }

        // ---- More: transfers, shares, apps, settings ----
        RowLayout {
          visible: root.tab === "more"
          Layout.fillWidth: true
          spacing: Style.space(6)
          Repeater {
            model: [ { id: "transfers", label: "󰓡 Transfers" }, { id: "shares", label: "󰒗 Shares" }, { id: "apps", label: "󰀻 Apps" }, { id: "settings", label: "󰒓 Settings" } ]
            delegate: Rectangle {
              Layout.fillWidth: true; Layout.preferredWidth: 1
              implicitHeight: Style.space(26); radius: Style.space(6)
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, root.moreSec === modelData.id ? 0.16 : (secm.containsMouse ? 0.10 : 0.05))
              Text { anchors.centerIn: parent; text: modelData.label + (modelData.id === "transfers" && root.tasksActive > 0 ? " " + root.tasksActive : "")
                     color: root.moreSec === modelData.id ? root.foreground : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              MouseArea { id: secm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.setSec(modelData.id) }
            }
          }
        }

        // transfers
        Text {
          Layout.fillWidth: true
          visible: root.tab === "more" && root.moreSec === "transfers" && (root.agent.syncing || root.agent.running)
          text: root.agent.syncing ? "󰓡  Uploading " + root.agent.syncing + "…" : "󰓡  Live upload idle  ·  " + (root.agent.uploaded || 0) + " files sent"
          textFormat: Text.PlainText; elide: Text.ElideRight
          color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
        }
        RowLayout {
          visible: root.tab === "more" && root.moreSec === "transfers"
          Layout.fillWidth: true; spacing: Style.space(6)
          Repeater {
            model: [ { label: "󰄀  Screenshot region → NAS", mode: "region" }, { label: "󰄀  Screen → NAS", mode: "screen" } ]
            delegate: Rectangle {
              Layout.fillWidth: true; Layout.preferredWidth: 1
              implicitHeight: Style.space(26); radius: Style.space(6)
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, capm.containsMouse ? 0.18 : 0.10)
              Text { anchors.fill: parent; anchors.leftMargin: Style.space(6); anchors.rightMargin: Style.space(6); horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
                     text: modelData.label; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              MouseArea { id: capm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.captureNow(modelData.mode) }
            }
          }
        }
        ListView {
          visible: root.tab === "more" && root.moreSec === "transfers"
          Layout.fillWidth: true; Layout.fillHeight: true
          clip: true
          model: root.tasks
          delegate: Item {
            width: ListView.view.width; height: Style.space(46)
            readonly property bool live: modelData.state !== "done" && modelData.state !== "fail" && modelData.state !== "failed" && modelData.state !== "cancel"
            readonly property bool paused: String(modelData.state).indexOf("pause") !== -1
            ColumnLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(6); spacing: Style.space(2)
              Item { Layout.fillHeight: true }
              Text { Layout.fillWidth: true; text: root.taskTitle(modelData); textFormat: Text.PlainText; elide: Text.ElideMiddle
                     color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Text { Layout.fillWidth: true; text: root.taskLine(modelData); textFormat: Text.PlainText; elide: Text.ElideRight
                     color: modelData.failed > 0 ? root.urgent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              Item { Layout.fillHeight: true }
            }
            Row {
              anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: Style.space(4)
              Btn { visible: live; glyph: paused ? "󰐊" : "󰏤"; tip: paused ? "Resume" : "Pause"; onClicked: root.taskAct(paused ? "continue" : "pause", modelData.id) }
              Btn { glyph: live ? "󰅖" : "󰆴"; tip: live ? "Cancel" : "Clear from the list"; onClicked: root.taskAct("del", modelData.id) }
            }
          }
          Text { anchors.centerIn: parent; visible: root.tasks.length === 0; text: "No transfers on the NAS"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.body }
        }

        // shares
        ListView {
          visible: root.tab === "more" && root.moreSec === "shares"
          Layout.fillWidth: true; Layout.fillHeight: true
          clip: true
          model: root.shares
          delegate: Item {
            width: ListView.view.width; height: Style.space(46)
            ColumnLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(60); spacing: Style.space(2)
              Item { Layout.fillHeight: true }
              Text { Layout.fillWidth: true; text: modelData.name || "(share)"; textFormat: Text.PlainText; elide: Text.ElideMiddle
                     color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Text { Layout.fillWidth: true; text: root.expiresText(modelData) + (modelData.pass ? "  ·  password " + modelData.pass : "  ·  no password") + (modelData.count > 1 ? "  ·  " + modelData.count + " items" : "")
                     textFormat: Text.PlainText; elide: Text.ElideRight; color: modelData.expired ? root.urgent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              Item { Layout.fillHeight: true }
            }
            Row {
              anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; anchors.rightMargin: Style.space(4)
              Btn { glyph: "󰆏"; tip: "Copy link and password"; onClicked: { root.copyText(root.shareText(modelData)); root.status = "Share link copied"; root.failed = false } }
              Btn { glyph: "󰆴"; tip: "Stop sharing"; onClicked: { shareDelProc.command = root.py(["share-delete", modelData.id]); shareDelProc.running = true } }
            }
          }
          Text { anchors.centerIn: parent; width: parent.width - Style.space(30); horizontalAlignment: Text.AlignHCenter; wrapMode: Text.Wrap; visible: root.shares.length === 0
                 text: "No share links yet.\nUse the 󰒗 button on a file in Files."; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.body }
        }

        // apps
        ListView {
          visible: root.tab === "more" && root.moreSec === "apps"
          Layout.fillWidth: true; Layout.fillHeight: true
          clip: true
          model: root.apps
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: apm.containsMouse
            implicitHeight: Style.space(32)
            MouseArea { id: apm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openApp(modelData) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); spacing: Style.space(8)
              Text { text: "󰀻"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
              Text { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Text { text: modelData.docker ? "docker" : (modelData.admin ? "admin" : ""); color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            }
          }
          Text { anchors.centerIn: parent; visible: root.apps.length === 0; text: "Loading apps…"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.body }
        }

        // settings
        ListView {
          visible: root.tab === "more" && root.moreSec === "settings"
          Layout.fillWidth: true; Layout.fillHeight: true
          clip: true
          model: root.settingSpecs
          delegate: Item {
            id: srow
            width: ListView.view.width
            height: Style.space(modelData.type === "text" ? 54 : 34)
            readonly property var sp: modelData
            readonly property var val: sp.type === "agent" ? root.agent.running === true : sp.type === "photo" ? (root.agent.live || []).indexOf("Photos") !== -1 : root.cfg[sp.key]
            readonly property bool isSwitch: sp.type === "bool" || sp.type === "agent" || sp.type === "photo"
            Text {
              anchors.left: parent.left; anchors.leftMargin: Style.space(10)
              anchors.right: srow.isSwitch ? sw.left : (sp.type === "int" ? stepper.left : parent.right); anchors.rightMargin: Style.space(8)
              y: sp.type === "text" ? Style.space(4) : (parent.height - height) / 2
              text: sp.label; textFormat: Text.PlainText; elide: Text.ElideRight
              color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body
            }
            Rectangle {
              id: sw
              visible: srow.isSwitch
              anchors.right: parent.right; anchors.rightMargin: Style.space(10); anchors.verticalCenter: parent.verticalCenter
              width: Style.space(38); height: Style.space(20); radius: height / 2
              color: srow.val ? Color.accent : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.22)
              Rectangle { width: parent.height - 6; height: width; radius: width / 2; y: 3; x: srow.val ? parent.width - width - 3 : 3; color: "#ffffff"; Behavior on x { NumberAnimation { duration: 110 } } }
              MouseArea {
                anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                onClicked: sp.type === "agent" ? root.agentToggle(!srow.val) : sp.type === "photo" ? root.photoToggle(!srow.val) : root.setSetting(sp.key, !srow.val)
              }
            }
            Row {
              id: stepper
              visible: sp.type === "int"
              anchors.right: parent.right; anchors.rightMargin: Style.space(6); anchors.verticalCenter: parent.verticalCenter
              spacing: Style.space(2)
              Btn { glyph: "−"; tip: "Less"; onClicked: root.setSetting(sp.key, Math.max(sp.min, (root.cfg[sp.key] || 0) - sp.step)) }
              Text { width: Style.space(44); horizontalAlignment: Text.AlignHCenter; anchors.verticalCenter: parent.verticalCenter; text: String(root.cfg[sp.key] === undefined ? "" : root.cfg[sp.key])
                     color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Btn { glyph: "+"; tip: "More"; onClicked: root.setSetting(sp.key, Math.min(sp.max, (root.cfg[sp.key] || 0) + sp.step)) }
            }
            Rectangle {
              visible: sp.type === "text"
              anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
              anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); anchors.bottomMargin: Style.space(4)
              height: Style.space(24); radius: Style.space(5)
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
              border.width: ti.activeFocus ? 1 : 0; border.color: Color.accent
              TextInput {
                id: ti
                anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(8)
                verticalAlignment: TextInput.AlignVCenter; clip: true
                color: root.foreground; selectionColor: Color.accent
                font.family: root.fontFamily; font.pixelSize: Style.font.body
                text: sp.type === "text" && root.cfg[sp.key] !== undefined ? String(root.cfg[sp.key]) : ""
                onEditingFinished: if (sp.type === "text" && text !== String(root.cfg[sp.key])) root.setSetting(sp.key, text)
              }
            }
          }
        }
        Text {
          Layout.fillWidth: true
          visible: root.tab === "more" && root.moreSec === "settings"
          text: (root.agent.running ? "Agent running" : "Agent stopped") + (root.agent.running && root.agent.last_health ? "  ·  health check " + Math.max(1, Math.round((Date.now() / 1000 - root.agent.last_health) / 60)) + " min ago" : "")
                + (root.agent.error ? "  ·  " + root.agent.error : "")
          textFormat: Text.PlainText; elide: Text.ElideRight
          color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
        }

        // ---- Music: search ----
        SearchField {
          id: musicSearch
          visible: root.tab === "music" && root.albumId === ""
          placeholder: "Search albums or artists"
          value: root.query
          onEdited: function(v) { root.query = v; root.applyFilter() }
        }

        // ---- Music: albums ----
        ListView {
          visible: root.tab === "music" && root.albumId === ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.albums
          cacheBuffer: 400
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: ama.containsMouse
            implicitHeight: Style.space(48)
            MouseArea { id: ama; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openAlbum(modelData) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
              Rectangle {
                Layout.preferredWidth: Style.space(38); Layout.preferredHeight: Style.space(38)
                radius: Style.space(4); clip: true
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                Text { anchors.centerIn: parent; text: "󰝚"; visible: cover.status !== Image.Ready; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                Image {
                  id: cover
                  anchors.fill: parent
                  source: root.coverUrl(modelData)
                  asynchronous: true; cache: true
                  fillMode: Image.PreserveAspectCrop
                  sourceSize.width: 96; sourceSize.height: 96
                }
              }
              ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(1)
                Text { Layout.fillWidth: true; text: modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                Text { Layout.fillWidth: true; text: modelData.artist + (modelData.tracks ? "  ·  " + modelData.tracks + (String(modelData.tracks) === "1" ? " track" : " tracks") : ""); textFormat: Text.PlainText; elide: Text.ElideRight; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              }
            }
          }
        }

        // ---- Music: songs of one album ----
        ListView {
          visible: root.tab === "music" && root.albumId !== ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.songs
          header: Item {
            width: ListView.view.width; implicitHeight: Style.space(34)
            Btn { id: playAlbumBtn; anchors.verticalCenter: parent.verticalCenter; implicitWidth: Style.space(120); glyph: "󰐊  Play album"; onClicked: root.playAlbum(0) }
            Btn {
              anchors.verticalCenter: parent.verticalCenter; anchors.left: playAlbumBtn.right; anchors.leftMargin: Style.space(8)
              implicitWidth: Style.space(130)
              enabled: !pinProc.running
              glyph: root.albumOffline(root.albumId) ? "󰄬  Offline ✕" : "󰇚  Save offline"
              onClicked: root.albumOffline(root.albumId) ? root.unpinAlbum() : root.pinAlbum()
            }
          }
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: sma.containsMouse
            implicitHeight: Style.space(34)
            MouseArea { id: sma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.playAlbum(index) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); spacing: Style.space(8)
              Text { text: String(index + 1); color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; Layout.preferredWidth: Style.space(22) }
              Text { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Text { text: root.fmtTime(modelData.ms / 1000); color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            }
          }
        }

        // ---- Video: libraries ----
        RowLayout {
          visible: root.tab === "video" && root.vcollId === ""
          Layout.fillWidth: true
          spacing: Style.space(6)
          Repeater {
            model: root.vlibs
            delegate: Item {
              Layout.fillWidth: true
              implicitHeight: Style.space(26)
              Rectangle {
                anchors.fill: parent; radius: Style.space(13)
                color: root.vlib === modelData.id ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.18) : Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.06)
              }
              Text { textFormat: Text.PlainText;
                anchors.centerIn: parent; text: modelData.name; elide: Text.ElideRight; width: parent.width - Style.space(8); horizontalAlignment: Text.AlignHCenter
                color: root.vlib === modelData.id ? root.foreground : root.dim
                font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.selectLib(modelData.id) }
            }
          }
        }

        SearchField {
          id: videoSearch
          visible: root.tab === "video" && root.vcollId === ""
          placeholder: "Search videos"
          value: root.vquery
          onEdited: function(v) { root.vquery = v; root.applyVideoFilter() }
        }

        // ---- Video: titles ----
        ListView {
          visible: root.tab === "video" && root.vcollId === ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.vshown
          cacheBuffer: 400
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: vma.containsMouse
            implicitHeight: Style.space(62)
            MouseArea { id: vma; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openCollection(modelData) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
              Rectangle {
                Layout.preferredWidth: Style.space(36); Layout.preferredHeight: Style.space(52)
                radius: Style.space(4); clip: true
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                Text { anchors.centerIn: parent; text: "󰕧"; visible: poster.status !== Image.Ready; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                Image {
                  id: poster
                  anchors.fill: parent
                  source: root.coverUrl(modelData)
                  asynchronous: true; cache: true
                  fillMode: Image.PreserveAspectCrop
                  sourceSize.width: 108; sourceSize.height: 156
                }
              }
              ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(1)
                Text { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                Text {
                  Layout.fillWidth: true
                  text: [modelData.year ? String(modelData.year) : "", Number(modelData.score) > 0 ? "★ " + modelData.score : "", modelData.genres].filter(function(s) { return s }).join("  ·  ")
                  textFormat: Text.PlainText; elide: Text.ElideRight
                  color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
              }
            }
          }
        }

        // ---- Video: episodes ----
        ListView {
          visible: root.tab === "video" && root.vcollId !== ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.vitems
          cacheBuffer: 400
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: ema.containsMouse
            implicitHeight: Style.space(56)
            MouseArea { id: ema; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.playVideo(modelData.path, modelData.name || modelData.ep || modelData.file, false, root.queueAfter(index)) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
              Rectangle {
                Layout.preferredWidth: Style.space(72); Layout.preferredHeight: Style.space(40)
                radius: Style.space(4); clip: true
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                Text { anchors.centerIn: parent; text: "󰐊"; visible: still.status !== Image.Ready; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                Image {
                  id: still
                  anchors.fill: parent
                  source: root.coverUrl({ cover: modelData.thumb })
                  asynchronous: true; cache: true
                  fillMode: Image.PreserveAspectCrop
                  sourceSize.width: 216; sourceSize.height: 120
                }
              }
              ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(1)
                Text {
                  Layout.fillWidth: true
                  text: [modelData.ep, modelData.name].filter(function(s) { return s }).join("  –  ") || modelData.file
                  textFormat: Text.PlainText; elide: Text.ElideRight
                  color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body
                }
                Text {
                  Layout.fillWidth: true
                  text: [root.fmtDur(modelData.duration), modelData.size ? root.fmtSize(modelData.size) : "", root.resumeText(modelData.path)].filter(function(s) { return s }).join("  ·  ")
                  textFormat: Text.PlainText; elide: Text.ElideRight
                  color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
              }
              Btn {
                visible: !!modelData.path
                glyph: root.videoPinned(modelData.path) ? "✓" : "󰇚"; tip: root.videoPinned(modelData.path) ? "Saved offline (click to remove)" : "Save for offline viewing"
                onClicked: root.videoPinned(modelData.path) ? root.unpinVideo(modelData.path) : root.pinVideo(modelData.path, modelData.name || modelData.ep || modelData.file)
              }
              Btn {
                visible: root.vprog[modelData.path] !== undefined
                glyph: "󰑙"; tip: "Play from the start"
                onClicked: root.playVideo(modelData.path, modelData.name || modelData.ep || modelData.file, true)
              }
            }
          }
        }

        // ---- Comics: series ----
        ListView {
          visible: root.tab === "comics" && root.cpath === ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.cseries
          cacheBuffer: 400
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: csa.containsMouse
            implicitHeight: Style.space(62)
            readonly property var prog: root.cprogress[modelData.path]
            MouseArea { id: csa; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.openSeries(modelData) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(10); spacing: Style.space(10)
              Rectangle {
                Layout.preferredWidth: Style.space(36); Layout.preferredHeight: Style.space(52)
                radius: Style.space(4); clip: true
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
                Text { anchors.centerIn: parent; text: "󰂺"; visible: ccover.status !== Image.Ready; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
                Image {
                  id: ccover
                  anchors.fill: parent
                  source: modelData.cover ? "file://" + modelData.cover : ""
                  asynchronous: true; cache: true
                  fillMode: Image.PreserveAspectCrop; verticalAlignment: Image.AlignTop
                  sourceSize.width: 108; sourceSize.height: 156
                }
              }
              ColumnLayout {
                Layout.fillWidth: true; spacing: Style.space(1)
                Text { Layout.fillWidth: true; text: modelData.name; textFormat: Text.PlainText; elide: Text.ElideRight; color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
                Text {
                  Layout.fillWidth: true
                  text: modelData.chapters + (modelData.chapters === 1 ? " chapter" : " chapters") + (prog ? "  ·  last read: " + root.chapterName(prog.chapter) : "")
                  textFormat: Text.PlainText; elide: Text.ElideRight
                  color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
                }
              }
              Btn { visible: !!prog; glyph: "󰐊"; tip: "Continue reading"; onClicked: root.readComic(modelData.path, prog.chapter) }
            }
          }
        }

        // ---- Comics: chapters of one series ----
        ListView {
          visible: root.tab === "comics" && root.cpath !== ""
          Layout.fillWidth: true
          Layout.fillHeight: true
          clip: true
          model: root.cchapters
          cacheBuffer: 400
          header: Item {
            width: ListView.view.width; implicitHeight: Style.space(68)
            Btn {
              id: contBtn
              anchors.top: parent.top; anchors.topMargin: Style.space(3); implicitWidth: Style.space(190)
              visible: root.cchapters.length > 0
              readonly property var prog: root.cprogress[root.cpath]
              glyph: prog ? "󰐊  Continue: " + root.chapterName(prog.chapter) : "󰐊  Start reading"
              onClicked: root.readComic(root.cpath, prog ? prog.chapter : root.cchapters[0].path)
            }
            Btn {
              anchors.top: contBtn.bottom; anchors.topMargin: Style.space(2); anchors.left: parent.left
              visible: root.cchapters.length > 0
              implicitWidth: Style.space(190)
              enabled: !pinProc.running
              glyph: root.offline.comics[root.cpath] !== undefined ? "󰄬  Offline (" + root.offline.comics[root.cpath].chapters + ") ✕" : "󰇚  Next 10 offline"
              onClicked: root.offline.comics[root.cpath] !== undefined ? root.unpinComics() : root.pinComics()
            }
          }
          delegate: CursorSurface {
            width: ListView.view.width
            foreground: root.foreground
            hasCursor: cha.containsMouse
            implicitHeight: Style.space(34)
            readonly property bool last: root.cprogress[root.cpath] !== undefined && root.cprogress[root.cpath].chapter === modelData.path
            readonly property bool read: root.chapterRead(index)
            MouseArea { id: cha; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.readComic(root.cpath, modelData.path) }
            RowLayout {
              anchors.fill: parent; anchors.leftMargin: Style.space(10); anchors.rightMargin: Style.space(10); spacing: Style.space(8)
              Text { text: last ? "󰐊" : read ? "󰄬" : "󰂺"; color: last ? Color.accent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
              Text { visible: root.comicPinned(modelData.path); text: "󰇚"; color: Color.accent; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              Text { Layout.fillWidth: true; text: modelData.label + (last ? "  ·  page " + (root.cprogress[root.cpath].page + 1) : ""); textFormat: Text.PlainText; elide: Text.ElideRight; color: read ? root.dim : root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body }
              Text { text: modelData.group; textFormat: Text.PlainText; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
              Text { text: root.fmtSize(modelData.size); color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            }
          }
        }

        Text {
          Layout.fillWidth: true
          visible: root.tab === "files" && root.nasSummary !== ""
          text: "󰋊  " + root.nasSummary; textFormat: Text.PlainText; elide: Text.ElideRight
          color: root.nas.online === false || (root.nas.problems && root.nas.problems.length > 0) ? root.urgent : root.dim
          font.family: root.fontFamily; font.pixelSize: Style.font.caption
        }

        // ---- Now playing (video): seek, pause, subtitle and audio track ----
        Rectangle {
          Layout.fillWidth: true
          visible: root.vnow.playing === true
          implicitHeight: vcol.implicitHeight + Style.space(12)
          radius: Style.space(8)
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
          ColumnLayout {
            id: vcol
            anchors.left: parent.left; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
            anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(6)
            spacing: Style.space(4)
            RowLayout {
              Layout.fillWidth: true; spacing: Style.space(6)
              Text {
                Layout.fillWidth: true
                text: root.vnow.title || "…"
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body; font.bold: true
              }
              Text {
                text: root.fmtTime(root.vnow.pos) + " / " + root.fmtTime(root.vnow.duration)
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
            }
            Rectangle {
              Layout.fillWidth: true; implicitHeight: Style.space(8); radius: height / 2
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)
              Rectangle {
                height: parent.height; radius: height / 2; color: Color.accent
                width: parent.width * Math.max(0, Math.min(1, (root.vnow.pos || 0) / Math.max(1, root.vnow.duration || 1)))
              }
              MouseArea {
                anchors.fill: parent; anchors.topMargin: -6; anchors.bottomMargin: -6; cursorShape: Qt.PointingHandCursor
                onClicked: function(m) { root.vplayerAct("seek:" + Math.round(m.x / width * (root.vnow.duration || 0))) }
              }
            }
            RowLayout {
              Layout.fillWidth: true; spacing: Style.space(4)
              Btn { glyph: "󰴪"; tip: "Back 10 s"; onClicked: root.vplayerAct("skip:-10") }
              Btn { glyph: root.vnow.paused ? "󰐊" : "󰏤"; tip: "Play / pause"; onClicked: root.vplayerAct("pause") }
              Btn { glyph: "󰴫"; tip: "Forward 10 s"; onClicked: root.vplayerAct("skip:10") }
              Btn { glyph: (root.vnow.volume || 0) === 0 ? "󰝟" : "󰕾"; tip: "Volume (click to step)"; onClicked: root.vplayerAct("volume:" + (((root.vnow.volume || 0) >= 100) ? 0 : Math.min(100, Math.round((root.vnow.volume || 0) / 25) * 25 + 25))) }
              Item { Layout.fillWidth: true }
              Rectangle {
                visible: root.trackList("sub").length > 0
                implicitWidth: ccText.implicitWidth + Style.space(14); implicitHeight: Style.space(24); radius: Style.space(6)
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, ccm.containsMouse ? 0.20 : 0.12)
                Text { id: ccText; anchors.centerIn: parent; text: "󰗊  " + root.trackLabel(root.currentTrack("sub")); textFormat: Text.PlainText
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                MouseArea { id: ccm; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.cycleTrack("sub") }
              }
              Rectangle {
                visible: root.trackList("audio").length > 1
                implicitWidth: auText.implicitWidth + Style.space(14); implicitHeight: Style.space(24); radius: Style.space(6)
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, aum.containsMouse ? 0.20 : 0.12)
                Text { id: auText; anchors.centerIn: parent; text: "󰓃  " + root.trackLabel(root.currentTrack("audio")); textFormat: Text.PlainText
                       color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
                MouseArea { id: aum; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor; onClicked: root.cycleTrack("audio") }
              }
              Btn { visible: root.vnow.queued > 0; glyph: "󰒭"; tip: "Next episode now"; onClicked: root.vplayerAct("next") }
              Btn { glyph: "󰓛"; tip: "Stop (also cancels autoplay)"; onClicked: root.vplayerAct("stop") }
            }
            Text {
              Layout.fillWidth: true
              visible: root.vnow.queued > 0 && root.cfg.autoplay !== false
              text: "Up next: " + root.vnow.next
              textFormat: Text.PlainText; elide: Text.ElideRight
              color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
            }
          }
        }

        // ---- Now playing (music): tap the title for the queue and volume ----
        Rectangle {
          Layout.fillWidth: true
          visible: root.now.playing === true && root.queueOpen
          implicitHeight: Style.space(190)
          radius: Style.space(8)
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
          ColumnLayout {
            anchors.fill: parent; anchors.margins: Style.space(8); spacing: Style.space(6)
            RowLayout {
              Layout.fillWidth: true; spacing: Style.space(8)
              Text { text: root.now.volume === 0 ? "󰝟" : "󰕾"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
              Rectangle {
                id: volBar
                Layout.fillWidth: true; implicitHeight: Style.space(8); radius: height / 2
                color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.16)
                Rectangle { height: parent.height; radius: height / 2; color: Color.accent; width: parent.width * Math.max(0, Math.min(1, (root.now.volume || 0) / 100)) }
                MouseArea {
                  anchors.fill: parent; anchors.topMargin: -7; anchors.bottomMargin: -7; cursorShape: Qt.PointingHandCursor
                  onClicked: function(m) { root.playerAct("volume:" + Math.round(m.x / width * 100)) }
                }
              }
              Text { text: Math.round(root.now.volume || 0) + "%"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption }
            }
            ListView {
              Layout.fillWidth: true; Layout.fillHeight: true
              clip: true
              model: root.now.queue || []
              currentIndex: root.now.index === undefined ? -1 : root.now.index
              onCurrentIndexChanged: if (currentIndex >= 0) positionViewAtIndex(currentIndex, ListView.Contain)
              delegate: Item {
                width: ListView.view.width; height: Style.space(26)
                readonly property bool cur: index === root.now.index
                readonly property bool ready: index < (root.now.loaded || 0)
                Rectangle { anchors.fill: parent; radius: Style.space(4); color: qa.containsMouse && ready ? Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.14) : "transparent" }
                RowLayout {
                  anchors.fill: parent; anchors.leftMargin: Style.space(6); anchors.rightMargin: Style.space(6); spacing: Style.space(6)
                  Text { text: cur ? "󰐊" : String(index + 1); color: cur ? Color.accent : root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption; Layout.preferredWidth: Style.space(18) }
                  Text { Layout.fillWidth: true; text: modelData.title; textFormat: Text.PlainText; elide: Text.ElideRight; color: cur ? root.foreground : (ready ? root.foreground : root.dim); font.family: root.fontFamily; font.pixelSize: Style.font.caption; font.bold: cur }
                }
                MouseArea { id: qa; anchors.fill: parent; hoverEnabled: true; enabled: ready; cursorShape: Qt.PointingHandCursor; onClicked: root.playerAct("jump:" + index) }
              }
            }
          }
        }
        Rectangle {
          Layout.fillWidth: true
          visible: root.now.playing === true
          implicitHeight: Style.space(58)
          radius: Style.space(8)
          color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
          RowLayout {
            anchors.fill: parent; anchors.leftMargin: Style.space(8); anchors.rightMargin: Style.space(6); spacing: Style.space(8)
            Rectangle {
              Layout.preferredWidth: Style.space(42); Layout.preferredHeight: Style.space(42)
              radius: Style.space(4); clip: true
              color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
              Text { anchors.centerIn: parent; text: "󰝚"; visible: npCover.status !== Image.Ready; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
              Image {
                id: npCover
                anchors.fill: parent
                source: root.coverUrl(root.now)
                asynchronous: true
                fillMode: Image.PreserveAspectCrop
                sourceSize.width: 96; sourceSize.height: 96
              }
            }
            ColumnLayout {
              Layout.fillWidth: true; spacing: 0
              Text {
                Layout.fillWidth: true
                text: (root.queueOpen ? "󰅀 " : "󰅂 ") + (root.now.title || "…")
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.foreground; font.family: root.fontFamily; font.pixelSize: Style.font.body; font.bold: true
                MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: root.queueOpen = !root.queueOpen }
              }
              Text {
                Layout.fillWidth: true
                text: [root.now.artist, root.now.album].filter(function(s) { return s }).join("  ·  ")
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
              Text {
                Layout.fillWidth: true
                text: root.fmtTime(root.now.pos) + " / " + root.fmtTime(root.now.duration)
                      + "  ·  " + ((root.now.index === undefined || root.now.index === null) ? "…" : root.now.index + 1) + " of " + (root.now.total || "?")
                textFormat: Text.PlainText; elide: Text.ElideRight
                color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.caption
              }
            }
            Btn { glyph: "󰒮"; tip: "Previous"; onClicked: root.playerAct("prev") }
            Btn { glyph: root.now.paused ? "󰐊" : "󰏤"; tip: "Play / pause"; onClicked: root.playerAct("pause") }
            Btn { glyph: "󰒭"; tip: "Next"; onClicked: root.playerAct("next") }
            Btn { glyph: "󰓛"; tip: "Stop"; onClicked: root.playerAct("stop") }
          }
        }
      }
    }
  }

  component SearchField: Rectangle {
    id: sf
    property string placeholder: ""
    property string value: ""
    readonly property bool focused: input.activeFocus
    function focusInput() { input.forceActiveFocus() }
    signal edited(string value)
    Layout.fillWidth: true
    implicitHeight: Style.space(30)
    radius: Style.space(6)
    color: Qt.rgba(root.foreground.r, root.foreground.g, root.foreground.b, 0.10)
    border.width: input.activeFocus ? 1 : 0
    border.color: Color.accent
    Binding { target: input; property: "text"; value: sf.value; when: !input.activeFocus }
    Text { id: glyph; anchors.left: parent.left; anchors.leftMargin: Style.space(10); anchors.verticalCenter: parent.verticalCenter; text: "󰍉"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
    TextInput {
      id: input
      anchors.left: glyph.right; anchors.right: clearBtn.left
      anchors.leftMargin: Style.space(8); anchors.verticalCenter: parent.verticalCenter
      color: root.foreground; selectionColor: Color.accent
      font.family: root.fontFamily; font.pixelSize: Style.font.body
      clip: true
      onTextChanged: if (activeFocus || text !== sf.value) sf.edited(text)
      Keys.onEscapePressed: function(event) { if (text !== "") { text = ""; event.accepted = true } else event.accepted = false }
      Text { visible: parent.text === "" && !parent.activeFocus; text: sf.placeholder; color: root.dim; font: parent.font }
    }
    MouseArea { anchors.fill: parent; z: -1; cursorShape: Qt.IBeamCursor; onClicked: input.forceActiveFocus() }
    Item {
      id: clearBtn
      anchors.right: parent.right; anchors.rightMargin: Style.space(6); anchors.verticalCenter: parent.verticalCenter
      width: input.text !== "" ? Style.space(22) : 0; height: Style.space(22); visible: input.text !== ""
      Text { anchors.centerIn: parent; text: "󰅖"; color: root.dim; font.family: root.fontFamily; font.pixelSize: Style.font.icon }
      MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: { input.text = ""; sf.edited("") } }
    }
  }

  component Btn: Item {
    id: btn
    property string glyph: ""
    property string tip: ""
    signal clicked()
    implicitWidth: Style.space(28); implicitHeight: Style.space(28)
    opacity: enabled ? 1 : 0.35
    Text {
      anchors.centerIn: parent; text: btn.glyph
      width: Math.min(implicitWidth, parent.width); elide: Text.ElideRight
      color: bma.containsMouse ? Color.accent : root.foreground
      font.family: root.fontFamily; font.pixelSize: Style.font.icon
    }
    MouseArea { id: bma; anchors.fill: parent; hoverEnabled: true; enabled: btn.enabled; cursorShape: Qt.PointingHandCursor; onClicked: btn.clicked() }
  }
}
