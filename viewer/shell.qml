// UnifyDrive photo viewer: a standalone Quickshell window (started by `udrive.py photo-open`), like the comic reader.
// Walks the NAS photo timeline (newest first), loading it in chunks in both directions.
//
// Env: UD_HELPER (path of udrive.py), UD_START (timeline offset of the chunk that holds the photo), UD_PATH (the photo).
// Keys: Right/Space/j next, Left/Backspace/k previous, s slideshow, i info, Enter play (videos), Home/End, q/Esc close.
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
  id: app

  readonly property string helper: Quickshell.env("UD_HELPER")
  readonly property int startOffset: parseInt(Quickshell.env("UD_START") || "0")
  readonly property string wantPath: Quickshell.env("UD_PATH")
  readonly property int chunk: 60

  property var items: []              // loaded photos in timeline order
  property int lo: 0                  // timeline offset of the first loaded chunk
  property int hi: 0                  // timeline offset just after the last loaded chunk
  property int total: 0
  property int idx: 0
  property bool loadingList: false
  property var pending: null          // {start, prepend}
  property var local: ({})            // remote path -> cached local file
  property string shown: ""           // file:// url on screen
  property bool slideshow: false
  property bool info: true
  property string message: "Opening…"
  property string fetching: ""

  readonly property var cur: items.length > 0 ? items[Math.min(idx, items.length - 1)] : null

  function loadChunk(start, prepend) {
    if (loadingList || start < 0 || (!prepend && total > 0 && start >= total)) return
    loadingList = true
    pending = { start: start, prepend: prepend }
    listProc.command = ["python3", "-I", helper, "photos", String(start), String(prepend ? Math.min(chunk, lo - start) : chunk)]
    listProc.running = true
  }

  function go(d) {
    if (items.length === 0) return
    var n = idx + d
    if (n < 0) { if (lo > 0) loadChunk(Math.max(0, lo - chunk), true); return }
    if (n >= items.length) { if (hi < total) loadChunk(hi, false); return }
    idx = n
    show()
  }

  function show() {
    var it = cur
    if (!it) return
    if (it.video) { shown = ""; message = "Video: press Enter to play"; maybeMore(); return }
    var f = local[it.path]
    if (f) { shown = "file://" + f; message = "" }
    else { shown = ""; message = "Loading…"; fetch(it.path) }
    maybeMore()
    // warm the neighbours
    var nx = items[idx + 1], pv = items[idx - 1]
    if (nx && !nx.video && !local[nx.path]) prefetch(nx.path)
    else if (pv && !pv.video && !local[pv.path]) prefetch(pv.path)
  }

  function maybeMore() {
    if (idx > items.length - 6 && hi < total) loadChunk(hi, false)
    else if (idx < 5 && lo > 0) loadChunk(Math.max(0, lo - chunk), true)
  }

  function fetch(path) {
    if (getProc.running) { getProc.queued = path; return }
    fetching = path
    getProc.command = ["python3", "-I", helper, "photo-get", path]
    getProc.running = true
  }
  function prefetch(path) {
    if (preProc.running) return
    preProc.target = path
    preProc.command = ["python3", "-I", helper, "photo-get", path]
    preProc.running = true
  }

  function remember(path, file) {
    var l = Object.assign({}, local)
    l[path] = file
    local = l
  }

  function fmtDate(ts) {
    if (!ts) return ""
    return new Date(ts * 1000).toLocaleString(Qt.locale(), "ddd d MMM yyyy  HH:mm")
  }

  Process {
    id: listProc
    stdout: StdioCollector { id: listOut; waitForEnd: true }
    onExited: {
      app.loadingList = false
      var p = app.pending
      try {
        var d = JSON.parse(String(listOut.text))
        if (d.error) { app.message = d.error; return }
        app.total = d.total
        if (p.prepend) {
          app.items = d.list.concat(app.items)
          app.idx += d.list.length
          app.lo = p.start
        } else {
          if (app.items.length === 0) {
            app.items = d.list; app.lo = p.start
            var at = -1
            for (var i = 0; i < d.list.length; i++) if (d.list[i].path === app.wantPath) at = i
            app.idx = at >= 0 ? at : 0
          } else app.items = app.items.concat(d.list)
          app.hi = d.next
        }
        if (app.items.length > 0 && app.shown === "" && !(app.cur && app.cur.video)) app.show()
        else if (app.items.length > 0) app.show()
      } catch (e) { app.message = "Could not read the photo list" }
    }
  }

  Process {
    id: getProc
    property string queued: ""
    stdout: StdioCollector { id: getOut; waitForEnd: true }
    onExited: {
      var path = app.fetching
      try {
        var d = JSON.parse(String(getOut.text))
        if (d.error) { app.message = "Could not load: " + d.error }
        else {
          app.remember(path, d.path)
          if (app.cur && app.cur.path === path) { app.shown = "file://" + d.path; app.message = "" }
        }
      } catch (e) { app.message = "Could not load this photo" }
      if (queued !== "") { var q = queued; queued = ""; if (!app.local[q]) app.fetch(q) }
      else if (app.cur && !app.local[app.cur.path] && !app.cur.video && app.cur.path !== path) app.fetch(app.cur.path)
    }
  }

  Process {
    id: preProc
    property string target: ""
    stdout: StdioCollector { id: preOut; waitForEnd: true }
    onExited: { try { var d = JSON.parse(String(preOut.text)); if (!d.error) app.remember(target, d.path) } catch (e) {} }
  }

  Timer {
    interval: 4000; repeat: true; running: app.slideshow
    onTriggered: { if (app.idx >= app.items.length - 1 && app.hi >= app.total) app.slideshow = false; else app.go(1) }
  }

  Component.onCompleted: loadChunk(startOffset, false)

  IpcHandler {
    target: "eg.unifydrive.viewer"
    function next(): string { app.go(1); return "ok" }
    function prev(): string { app.go(-1); return "ok" }
    function state(): string {
      return JSON.stringify({ idx: app.idx, count: app.items.length, lo: app.lo, hi: app.hi, total: app.total,
                              name: app.cur ? app.cur.name : "", shown: app.shown, msg: app.message, slideshow: app.slideshow })
    }
  }

  FloatingWindow {
    id: win
    title: "UnifyDrive Photos"
    implicitWidth: 1280
    implicitHeight: 860
    color: "#0b0b0b"
    onClosed: Qt.quit()

    FocusScope {
      anchors.fill: parent
      focus: true
      Component.onCompleted: forceActiveFocus()

      Keys.onPressed: function(ev) {
        ev.accepted = true
        switch (ev.key) {
        case Qt.Key_Right: case Qt.Key_Space: case Qt.Key_J: case Qt.Key_N: app.go(1); break
        case Qt.Key_Left: case Qt.Key_Backspace: case Qt.Key_K: case Qt.Key_P: app.go(-1); break
        case Qt.Key_S: app.slideshow = !app.slideshow; break
        case Qt.Key_I: app.info = !app.info; break
        case Qt.Key_Home: app.idx = 0; app.show(); break
        case Qt.Key_End: app.idx = Math.max(0, app.items.length - 1); app.show(); break
        case Qt.Key_Return: case Qt.Key_Enter:
          if (app.cur && app.cur.video) Quickshell.execDetached(["python3", "-I", app.helper, "play", app.cur.path])
          break
        case Qt.Key_Q: case Qt.Key_Escape: Qt.quit(); break
        default: ev.accepted = false
        }
      }

      WheelHandler {
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        property real last: 0
        onWheel: function(ev) {
          var now = Date.now()
          if (now - last < 160 || ev.angleDelta.y === 0) return
          last = now
          app.go(ev.angleDelta.y < 0 ? 1 : -1)
        }
      }

      Image {
        id: img
        anchors.fill: parent
        anchors.margins: 6
        source: app.shown
        asynchronous: true
        cache: false
        fillMode: Image.PreserveAspectFit
        smooth: true
        mipmap: true
        sourceSize.width: Math.round(width * Screen.devicePixelRatio)
        sourceSize.height: Math.round(height * Screen.devicePixelRatio)
      }

      Text {
        anchors.centerIn: parent
        visible: app.shown === "" || img.status === Image.Loading
        horizontalAlignment: Text.AlignHCenter
        color: "#9a9a9a"; font.pixelSize: 18
        text: app.message !== "" ? app.message : "…"
      }

      Rectangle {
        visible: app.info && app.cur !== null
        anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: parent.bottom
        height: 34
        color: "#c8000000"
        Text {
          anchors.left: parent.left; anchors.leftMargin: 14; anchors.verticalCenter: parent.verticalCenter
          anchors.right: hint.left; anchors.rightMargin: 14
          elide: Text.ElideRight
          color: "#e6e6e6"; font.pixelSize: 14
          text: app.cur ? app.cur.name + "   ·   " + app.fmtDate(app.cur.ts)
                          + (app.cur.w > 0 ? "   ·   " + app.cur.w + "×" + app.cur.h : "")
                          + "   ·   " + (app.lo + app.idx + 1) + " / " + app.total : ""
        }
        Text {
          id: hint
          anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
          color: "#9a9a9a"; font.pixelSize: 12
          text: (app.slideshow ? "▶ slideshow  " : "") + "←/→ browse   s slideshow   i info   q quit"
        }
      }
    }
  }
}
