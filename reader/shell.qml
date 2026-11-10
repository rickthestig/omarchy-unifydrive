// UnifyDrive comic reader: a standalone Quickshell window (started by `udrive.py comic-read`), so a stall or crash
// here can never take the bar down. Continuous vertical scroll across chapters, the way webtoons are meant to be read.
//
// Env: UD_HELPER (path of udrive.py), UD_SERIES (series folder on the NAS), UD_CHAPTER (optional chapter file to open).
// Keys: j/k or arrows scroll, Space/PageDown/PageUp page, n/p next/previous chapter, +/- page width, 0 reset width,
//       g/G top/bottom of the loaded pages, h hide/show the top bar, r reload this chapter, q/Esc close,
//       m one-page (manga) mode, d reading direction (left-to-right / right-to-left), f fit the window width.
import QtQuick
import Quickshell
import Quickshell.Io

ShellRoot {
  id: app

  readonly property string helper: Quickshell.env("UD_HELPER")
  readonly property string series: Quickshell.env("UD_SERIES")
  readonly property string wantChapter: Quickshell.env("UD_CHAPTER")
  readonly property string seriesName: series.replace(/\/+$/, "").split("/").pop()

  property var chapters: []
  property var saved: null
  property int cur: 0                 // model index of the page at the top of the window
  property int fetchCh: -1            // chapter being streamed right now
  property int fetchTotal: 0
  property bool fetchDone: true       // the last stream finished without losing a page
  property bool fetchFailed: false
  property var nextFetch: null        // {ch, page} to start once the running stream has stopped
  property var jump: null             // {ch, page} to scroll to as soon as that page exists
  property string message: ""
  property int colMax: 900
  property bool hud: true
  property bool pageMode: false       // one page at a time, fitted to the window
  property bool rtl: false            // page mode: right-to-left (manga) reading direction
  property real lastWheel: 0
  readonly property int prefetchN: parseInt(Quickshell.env("UD_PREFETCH") || "0")
  property var prefetchDone: ({})     // chapter index -> true once asked for
  property var prefetchQueue: []
  property string lastSaved: ""

  readonly property color fg: "#e6e6e6"
  readonly property color dim: "#9a9a9a"

  // ---- loading ---------------------------------------------------------------------------------------
  function chapterIndexOf(path) {
    for (var i = 0; i < chapters.length; i++) if (chapters[i].path === path) return i
    return -1
  }

  function openChapter(ch, page) {
    jump = { ch: ch, page: page }
    nextFetch = { ch: ch, page: page, clear: true }
    if (fetchProc.running) fetchProc.running = false      // onExited starts nextFetch once the old stream is drained
    else startNext()
  }

  function startNext() {
    if (!nextFetch) return
    var n = nextFetch
    nextFetch = null
    if (n.clear) { pages.clear(); cur = 0 }
    var c = chapters[n.ch]
    fetchCh = n.ch; fetchTotal = 0; fetchDone = false; fetchFailed = false; message = ""
    fetchProc.command = ["python3", "-I", helper, "comic-fetch", c.path, String(c.size), String(c.mtime)]
    fetchProc.running = true
  }

  function firstIndexOf(ch) {
    for (var i = 0; i < pages.count; i++) if (pages.get(i).ch === ch) return i
    return -1
  }

  function gotoChapter(ch) {
    if (ch < 0 || ch >= chapters.length) return
    var i = firstIndexOf(ch)
    if (i >= 0) { jump = null; view.positionViewAtIndex(i, ListView.Beginning) }
    else openChapter(ch, 0)
  }

  function chapterNow() { return pages.count > 0 ? pages.get(Math.min(cur, pages.count - 1)).ch : 0 }

  // ---- scrolling -------------------------------------------------------------------------------------
  function scrollBy(dy) {
    var base = scrollAnim.running ? scrollAnim.to : view.contentY
    var top = view.originY
    var bottom = Math.max(top, view.originY + view.contentHeight - view.height)
    var target = Math.max(top, Math.min(base + dy, bottom))
    scrollAnim.stop()
    scrollAnim.from = view.contentY
    scrollAnim.to = target
    scrollAnim.restart()
  }

  function track() {
    if (pages.count === 0) return
    var i = view.indexAt(5, view.contentY + 40)
    if (i < 0) i = view.indexAt(5, view.contentY + 300)
    if (i >= 0) cur = i
    var last = pages.get(pages.count - 1)
    // Reading near the end of what is loaded: quietly start the next chapter so it is there when you scroll on.
    if (!fetchProc.running && !nextFetch && fetchDone && last.ch + 1 < chapters.length && pages.count - cur <= 14)
      { nextFetch = { ch: last.ch + 1, page: 0, clear: false }; startNext() }
    prefetchCheck()
  }

  // Once a good part of this chapter has been read, quietly download the next chapters into the page cache (zip-type
  // files only: RAR and PDF are fetched whole, which could collide with the reader's own download of the same file).
  function prefetchCheck() {
    if (prefetchN <= 0 || pages.count === 0) return
    var e = pages.get(Math.min(cur, pages.count - 1))
    if (e.total <= 0 || (e.idx + 1) / e.total < 0.3) return
    for (var k = 1; k <= prefetchN; k++) {
      var t = e.ch + k
      if (t >= chapters.length || prefetchDone[t]) continue
      var ext = String(chapters[t].path).toLowerCase().split(".").pop()
      var d = Object.assign({}, prefetchDone); d[t] = true; prefetchDone = d
      if (ext === "cbr" || ext === "rar" || ext === "pdf") continue
      prefetchQueue = prefetchQueue.concat([t])
    }
    if (!prefetchProc.running && prefetchQueue.length > 0) prefetchNext()
  }
  function prefetchNext() {
    if (prefetchQueue.length === 0) return
    var t = prefetchQueue[0]
    prefetchQueue = prefetchQueue.slice(1)
    if (fetchProc.running && fetchCh === t) { prefetchNext(); return }     // the reader is already streaming it
    var c = chapters[t]
    prefetchProc.command = ["python3", "-I", helper, "comic-fetch", c.path, String(c.size), String(c.mtime)]
    prefetchProc.running = true
  }

  Process {
    id: prefetchProc
    stdout: SplitParser { onRead: function(line) {} }
    onExited: app.prefetchNext()
  }

  function setPageMode(on) {
    var keep = cur
    pageMode = on
    savePrefs()
    Qt.callLater(function() { view.positionViewAtIndex(Math.min(keep, pages.count - 1), ListView.Beginning) })
  }

  function savePrefs() {
    Quickshell.execDetached(["python3", "-I", helper, "comic-pref", "pageMode", pageMode ? "1" : "0", "rtl", rtl ? "1" : "0"])
  }

  // one step "forward" in reading order: page mode moves a whole page, scroll mode moves a screenful
  function step(dir) {
    if (pageMode) scrollBy(dir * view.height)
    else scrollBy(dir * view.height * 0.9)
  }

  function saveProgress() {
    if (pages.count === 0 || progressProc.running) return
    var e = pages.get(Math.min(cur, pages.count - 1))
    var key = e.ch + ":" + e.idx
    if (key === lastSaved) return
    lastSaved = key
    progressProc.command = ["python3", "-I", helper, "comic-progress", series, chapters[e.ch].path, String(e.idx)]
    progressProc.running = true
  }

  function quit() {
    if (pages.count > 0) {
      var e = pages.get(Math.min(cur, pages.count - 1))
      Quickshell.execDetached(["python3", "-I", helper, "comic-progress", series, chapters[e.ch].path, String(e.idx)])
    }
    Qt.quit()
  }

  ListModel { id: pages }

  Process {
    id: progressGet
    command: ["python3", "-I", app.helper, "comic-progress"]
    running: true
    stdout: StdioCollector { id: progressOut; waitForEnd: true }
    onExited: {
      try { app.saved = JSON.parse(String(progressOut.text))[app.series] || null } catch (e) { app.saved = null }
      prefsProc.running = true
    }
  }

  Process {
    id: prefsProc
    command: ["python3", "-I", app.helper, "comic-pref"]
    stdout: StdioCollector { id: prefsOut; waitForEnd: true }
    onExited: {
      try { var o = JSON.parse(String(prefsOut.text)); app.pageMode = o.pageMode === "1"; app.rtl = o.rtl === "1" } catch (e) {}
      chaptersProc.running = true
    }
  }

  Process {
    id: chaptersProc
    command: ["python3", "-I", app.helper, "comic-chapters", app.series]
    stdout: StdioCollector { id: chaptersOut; waitForEnd: true }
    onExited: {
      try {
        var d = JSON.parse(String(chaptersOut.text))
        if (d.error) { app.message = d.error; return }
        app.chapters = d
      } catch (e) { app.message = "Could not read the chapter list"; return }
      if (app.chapters.length === 0) { app.message = "No chapters in this folder"; return }
      var start = app.chapterIndexOf(app.wantChapter || (app.saved ? app.saved.chapter : ""))
      if (start < 0) start = 0
      var page = (app.saved && app.saved.chapter === app.chapters[start].path) ? app.saved.page : 0
      app.openChapter(start, page)
    }
  }

  Process {
    id: fetchProc
    stdout: SplitParser {
      onRead: function(line) {
        var o
        try { o = JSON.parse(line) } catch (e) { return }
        if (o.loading !== undefined) { app.message = "Downloading this chapter… " + o.loading + "%  (RAR/PDF files are fetched whole)"; return }
        if (o.total !== undefined) { app.fetchTotal = o.total; app.message = ""; return }
        if (o.done) { app.fetchDone = o.complete !== false; return }
        if (o.i === undefined) { if (o.error) { app.message = o.error; app.fetchFailed = true }; return }
        if (o.error) app.fetchFailed = true
        pages.append({ ch: app.fetchCh, idx: o.i, total: app.fetchTotal, src: o.path ? "file://" + o.path : "",
                       pw: o.w, ph: o.h, err: o.error || "", isFirst: o.i === 0 })
        if (app.jump && app.jump.ch === app.fetchCh && o.i === app.jump.page) {
          var at = pages.count - 1
          app.jump = null
          Qt.callLater(function() { view.positionViewAtIndex(at, ListView.Beginning) })
        }
      }
    }
    onExited: {
      if (app.nextFetch) app.startNext()
      else if (!app.fetchDone) { app.fetchFailed = true; if (app.message === "") app.message = "Connection lost; press r to retry" }
    }
  }

  Process { id: progressProc }

  Timer { interval: 250; repeat: true; running: true; onTriggered: app.track() }
  Timer { interval: 4000; repeat: true; running: true; onTriggered: app.saveProgress() }

  IpcHandler {
    target: "eg.unifydrive.reader"
    function scroll(dy: int): string { app.scrollBy(dy); return "ok" }
    function chapter(i: int): string { app.gotoChapter(i); return "ok" }
    function state(): string {
      return JSON.stringify({ cur: app.cur, count: pages.count, chapter: app.chapterNow(), chapters: app.chapters.length,
                              y: Math.round(view.contentY), h: Math.round(view.contentHeight), fetching: fetchProc.running,
                              done: app.fetchDone, msg: app.message, pageMode: app.pageMode, rtl: app.rtl })
    }
  }

  FloatingWindow {
    id: win
    title: "UnifyDrive Reader"
    implicitWidth: 960
    implicitHeight: 1140
    color: "#101010"
    onClosed: app.quit()

    FocusScope {
      id: keys
      anchors.fill: parent
      focus: true
      Component.onCompleted: forceActiveFocus()

      Keys.onPressed: function(ev) {
        var step = 160
        var shift = (ev.modifiers & Qt.ShiftModifier) !== 0
        ev.accepted = true
        app.jump = null
        switch (ev.key) {
        case Qt.Key_J: case Qt.Key_Down: case Qt.Key_S: if (app.pageMode) app.step(1); else app.scrollBy(step); break
        case Qt.Key_K: case Qt.Key_Up: case Qt.Key_W: if (app.pageMode) app.step(-1); else app.scrollBy(-step); break
        case Qt.Key_Space: app.step(shift ? -1 : 1); break
        case Qt.Key_PageDown: app.step(1); break
        case Qt.Key_PageUp: app.step(-1); break
        case Qt.Key_M: app.setPageMode(!app.pageMode); break
        case Qt.Key_D: app.rtl = !app.rtl; app.savePrefs(); break
        case Qt.Key_F: app.colMax = app.colMax >= 2400 ? 900 : 2400; break
        case Qt.Key_G: if (shift) app.scrollBy(1e9); else app.scrollBy(-1e9); break
        case Qt.Key_Home: app.scrollBy(-1e9); break
        case Qt.Key_End: app.scrollBy(1e9); break
        case Qt.Key_Right: if (app.pageMode) app.step(app.rtl ? -1 : 1); else app.gotoChapter(app.chapterNow() + 1); break
        case Qt.Key_Left: if (app.pageMode) app.step(app.rtl ? 1 : -1); else app.gotoChapter(app.chapterNow() - 1); break
        case Qt.Key_N: case Qt.Key_BracketRight: app.gotoChapter(app.chapterNow() + 1); break
        case Qt.Key_P: case Qt.Key_BracketLeft: app.gotoChapter(app.chapterNow() - 1); break
        case Qt.Key_Plus: case Qt.Key_Equal: app.colMax = Math.min(2400, app.colMax + 100); break
        case Qt.Key_Minus: app.colMax = Math.max(300, app.colMax - 100); break
        case Qt.Key_0: app.colMax = 900; break
        case Qt.Key_H: app.hud = !app.hud; break
        case Qt.Key_R: if (app.chapters.length > 0) app.openChapter(app.chapterNow(), pages.count > 0 ? pages.get(Math.min(app.cur, pages.count - 1)).idx : 0); break
        case Qt.Key_Q: case Qt.Key_Escape: app.quit(); break
        default: ev.accepted = false
        }
      }

      ListView {
        id: view
        anchors.fill: parent
        anchors.topMargin: app.hud ? 30 : 0
        model: pages
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        cacheBuffer: height
        reuseItems: false
        // Show the chosen page width centred; keep the page you are on when the width changes.
        onWidthChanged: if (pages.count > 0) Qt.callLater(function() { view.positionViewAtIndex(app.cur, ListView.Beginning) })

        NumberAnimation { id: scrollAnim; target: view; property: "contentY"; duration: 140; easing.type: Easing.OutCubic }

        WheelHandler {
          acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
          onWheel: function(ev) {
            app.jump = null
            var dy = ev.pixelDelta.y !== 0 ? ev.pixelDelta.y : ev.angleDelta.y * 1.3
            if (app.pageMode) {                       // one page per wheel burst
              var now = Date.now()
              if (now - app.lastWheel > 180 && dy !== 0) app.step(dy < 0 ? 1 : -1)
              if (dy !== 0) app.lastWheel = now
              return
            }
            scrollAnim.stop()
            var top = view.originY, bottom = Math.max(top, view.originY + view.contentHeight - view.height)
            view.contentY = Math.max(top, Math.min(view.contentY - dy, bottom))
          }
        }

        delegate: Item {
          id: row
          width: ListView.view.width
          readonly property real colW: app.pageMode ? width : Math.min(width, app.colMax)
          readonly property real imgH: app.pageMode ? view.height
                                     : model.err !== "" ? 140
                                     : (model.pw > 0 && model.ph > 0 ? colW * model.ph / model.pw
                                        : (img.status === Image.Ready && img.implicitWidth > 0 ? colW * img.implicitHeight / img.implicitWidth : colW * 1.4))
          readonly property real bandH: (model.isFirst && !app.pageMode) ? 70 : 0
          height: bandH + imgH

          Column {
            visible: model.isFirst && !app.pageMode
            anchors.horizontalCenter: parent.horizontalCenter
            y: 14
            spacing: 2
            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              text: app.chapters[model.ch] ? app.chapters[model.ch].label : ""
              color: app.fg; font.pixelSize: 20; font.bold: true
            }
            Text {
              anchors.horizontalCenter: parent.horizontalCenter
              text: (app.chapters[model.ch] && app.chapters[model.ch].group ? app.chapters[model.ch].group + "  ·  " : "") + model.total + " pages"
              color: app.dim; font.pixelSize: 12
            }
          }

          Image {
            id: img
            visible: model.err === ""
            x: (row.width - row.colW) / 2
            y: row.bandH
            width: row.colW
            height: row.imgH
            source: model.src
            asynchronous: true
            cache: false
            fillMode: app.pageMode ? Image.PreserveAspectFit : Image.Stretch
            smooth: true
            mipmap: true
            // decode at the shown size (x pixel ratio), never taller than a GPU texture allows
            sourceSize.width: app.pageMode ? Math.round(row.colW * Screen.devicePixelRatio)
                              : model.pw > 0 && model.ph > 0
                                ? Math.round(Math.min(row.colW * Screen.devicePixelRatio, 16000 * model.pw / model.ph))
                                : Math.round(row.colW * Screen.devicePixelRatio)
            sourceSize.height: app.pageMode ? Math.round(row.imgH * Screen.devicePixelRatio) : 0
          }
          Rectangle {
            visible: model.err === "" && img.status !== Image.Ready
            x: img.x; y: img.y; width: img.width; height: img.height
            color: "#181818"
            Text { anchors.centerIn: parent; text: img.status === Image.Error ? "Could not decode this page" : "…"; color: app.dim; font.pixelSize: 14 }
          }
          Text {
            visible: model.err !== ""
            x: 0; y: row.bandH; width: row.width; height: row.imgH
            horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter
            wrapMode: Text.Wrap
            text: "Page " + (model.idx + 1) + " failed: " + model.err + "\nPress r to reload this chapter"
            color: "#ff8a80"; font.pixelSize: 14
          }
        }

        footer: Item {
          width: view.width
          height: 160
          Text {
            anchors.centerIn: parent
            horizontalAlignment: Text.AlignHCenter
            color: app.dim; font.pixelSize: 14
            text: pages.count === 0 ? ""
                  : (fetchProc.running || app.nextFetch ? "Loading…"
                     : (pages.get(pages.count - 1).ch + 1 >= app.chapters.length ? "End of the last chapter\n\nq to close" : "Scroll on for the next chapter…"))
          }
        }
      }

      Text {
        anchors.centerIn: parent
        visible: pages.count === 0
        horizontalAlignment: Text.AlignHCenter
        color: app.message !== "" ? "#ff8a80" : app.dim
        font.pixelSize: 16
        wrapMode: Text.Wrap
        width: parent.width - 80
        text: app.message !== "" ? app.message : "Opening " + app.seriesName + "…"
      }

      Rectangle {
        visible: app.hud
        anchors.top: parent.top; anchors.left: parent.left; anchors.right: parent.right
        height: 30
        color: "#d9000000"
        Text {
          anchors.left: parent.left; anchors.leftMargin: 12; anchors.verticalCenter: parent.verticalCenter
          anchors.right: hint.left; anchors.rightMargin: 12
          elide: Text.ElideRight
          color: app.fg; font.pixelSize: 13
          text: {
            var parts = [app.seriesName]
            if (pages.count > 0) {
              var e = pages.get(Math.min(app.cur, pages.count - 1))
              parts.push(app.chapters[e.ch] ? app.chapters[e.ch].label : "")
              parts.push((e.idx + 1) + " / " + e.total)
            }
            if (fetchProc.running) parts.push("loading…")
            if (app.fetchFailed && !fetchProc.running) parts.push(app.message || "some pages failed")
            return parts.join("   ·   ")
          }
        }
        Text {
          id: hint
          anchors.right: parent.right; anchors.rightMargin: 12; anchors.verticalCenter: parent.verticalCenter
          color: app.dim; font.pixelSize: 12
          text: app.pageMode ? "←/→ page" + (app.rtl ? " (RTL)" : "") + "   d direction   m scroll mode   n/p chapter   h bar   q quit" : "j/k scroll   n/p chapter   +/- f width   m page mode   h bar   q quit"
        }
      }
    }
  }
}
