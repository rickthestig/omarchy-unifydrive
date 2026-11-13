#!/usr/bin/env python3
"""UnifyDrive (UT2) client over the ud.link web API.

Usage:
  udrive.py ls <remote_dir>
  udrive.py get <remote_file> [local_dir]
  udrive.py put <local_file> <remote_dir>
  udrive.py upload [--on-conflict=ask|keep|overwrite|skip] <remote_dir> <files-or-folders...>
  udrive.py mkdir <remote_dir> [name]   |   rm <remote_path...>  (to the recycle bin)
  udrive.py check
  udrive.py login [--auto] [--forget]   (--auto: saved credentials only, no prompts)
  udrive.py logout [--forget]   (end the session; --forget also wipes saved credentials)
  udrive.py stream-url <remote_file>   (resolve a stream URL: the localhost proxy's, or the NAS's own for video)
  udrive.py play <remote_file> [mpv args]
  udrive.py albums-all [--refresh] | cover-proxy | audio-proxy (the daemon behind lossless music) | albums [page] | songs <album_id> | play-album <album_id> [start]
  udrive.py player pause|next|prev|stop|status|volume:N|jump:N|seek:S
  udrive.py vplayer status|pause|stop|seek:S|skip:±S|sid:N|aid:N   |   video-progress [remote_path...]
    udrive.py continue                    (what to pick up again: video / comic / album, local data only)
  (play <video> resumes where it stopped; add --from-start to restart)
  udrive.py video-libs | video-list <lib_id|latest> | video-items <collection_id> <lib_id>
  udrive.py search <text> [root] [comics_root]   (files via the NAS index + albums, videos, comics)
  udrive.py album-pin <id> [name cover] | album-unpin <id> | comic-pin <series> [count [from_chapter]] | comic-unpin <series> | offline-list
  udrive.py photos [start [num]] | photo-get <remote_path> | photo-open <chunk_start> [path]   (the NAS photo timeline + viewer)
  udrive.py sync-add <local> <remote> [name] | sync-remove <name> | sync-list | sync-run [name] [--dry] | sync-timer on|off
  udrive.py nas-status                  (pool capacity, health, disk temperature)
  udrive.py tasks | task-act pause|continue|del <id...>          (the NAS copy/move/delete job queue)
  udrive.py share-create [--days=N] [--nopass] <remote paths...> | share-list | share-delete <id...>   (share links)
  udrive.py apps | webui [app]          (the NAS web apps; open one in the signed-in window)
  udrive.py video-pin <path> [name] | video-unpin <path>         (save a video for offline viewing)
  udrive.py play <video> [--queue=<json list of following videos>]   (autoplay the next one; vplayer next|cancelnext)
  udrive.py settings [set <key> <value>] | agent-status | agent-service on|off | photosync on|off | alerts-check
  udrive.py capture [region|screen] [--link|--nolink]            (screenshot -> NAS, link or path on the clipboard)
  udrive.py webui                       (open the web UI signed in as this session)
  udrive.py comic-series <root> | comic-chapters <series> | comic-fetch <cbz> <size> [mtime]   (comics, ranged reads)
  udrive.py comic-read <series> [chapter_path]   (open the reader window) | comic-progress [series chapter page]
  udrive.py status

All commands print JSON on stdout. The session lives in
~/.config/unifydrive/session.json (mode 600).
"""
import base64, hashlib, http.client, io, json, mimetypes, os, re, shutil, stat, struct, subprocess, sys, threading, time, random, uuid as uuidlib, zipfile, zlib
import urllib.error, urllib.parse as up, urllib.request as ur

SESSION = os.path.expanduser("~/.config/unifydrive/session.json")
CHUNK = 2 * 1024 * 1024


class ExpiredSession(RuntimeError):
    pass


class Drive:
    def __init__(self):
        self.s = json.load(open(SESSION))
        self.host = self.s["host"]
        self.cookie = up.quote(self.s["cookie_raw"], safe="=;&%+/:@(),!*' ")
        have = {c.split("=", 1)[0].strip() for c in self.cookie.split(";")}
        extra = {"device_id": self.s["device_id"], "version": self.s["version"], "plat": "web",
                 "device": up.quote(self.s["device"], safe="")}
        self.cookie += "".join(f"; {k}={v}" for k, v in extra.items() if k not in have)

    def _common(self):
        s = self.s
        return {"plat": "web", "version": s["version"], "device_id": s["device_id"],
                "device": s["device"], "_l": "en_us", "token": s["token"], "nasid": s["nasid"]}

    def _req(self, url, data=None, headers=None, method=None):
        h = {"Cookie": self.cookie, "User-Agent": self.s.get("ua") or "Mozilla/5.0"}
        h.update(headers or {})
        return ur.urlopen(ur.Request(url, data=data, headers=h, method=method), timeout=60)

    def _rnd(self):
        return f"rnd={int(time.time()*1000)}_{random.randint(0, 9999)}&webagent=v2"

    def post(self, endpoint, **fields):
        body = up.urlencode({**fields, **self._common()}).encode()
        r = self._req(f"{self.host}{endpoint}?&{self._rnd()}", body,
                      {"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(r.read())

    def _post_raw(self, endpoint, pairs):
        """POST with repeated form fields (the API wants arrays as `paths[]=a&paths[]=b`)."""
        body = up.urlencode(list(pairs) + list(self._common().items())).encode()
        r = self._req(f"{self.host}{endpoint}?&{self._rnd()}", body,
                      {"Content-Type": "application/x-www-form-urlencoded"})
        return json.loads(r.read())

    def mkdir(self, parent, name):
        """Create a folder; returns its path (an existing folder of that name is fine)."""
        r = self.post("/v2/file/newdir", parent=parent, name=name, rename=0)
        if str(r.get("code")) in ("200", "N001301", "N001322"):
            return (r.get("data") or {}).get("path") or parent.rstrip("/") + "/" + name
        raise RuntimeError(f"could not create folder {name}: {r.get('msg')}")

    def remove(self, paths):
        """Move items to the UT2 recycle bin."""
        r = self._post_raw("/v2/file/remove", [("paths[]", p) for p in paths])
        if str(r.get("code")) != "200":
            raise RuntimeError(f"remove refused: {r.get('msg')}")
        return {"removed": len(paths)}

    def logout(self):
        """End the NAS session the way the web UI does (POST /action/new, action=auth_logout)."""
        r = self.post("/action/new", action="auth_logout")
        return str(r.get("code")) == "200"

    def run(self, fn, *args):
        """Run an operation; if the session was rejected, log in again silently (saved credentials) and retry once."""
        try:
            return fn(*args)
        except (urllib.error.HTTPError, ExpiredSession) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code not in (400, 401, 403):
                raise
        login(interactive=False)
        self.__init__()
        return fn(*args)

    def ls(self, path):
        body = up.urlencode({"start": 0, "num": 1000, "sortby": "name", "order": "asc", "path": path,
                             "with_fields": "ext,type,is_sys", "show_hidden": 0, "dup": 1}).encode()
        r = self._req(f"{self.host}/v2/file/list/stream", body,
                      {"Content-Type": "application/x-www-form-urlencoded"})
        raw = r.read()
        try:
            items = json.loads(raw if raw[:1] == b"[" else base64.b64decode(raw))
        except Exception:
            raise ExpiredSession("list failed: " + raw[:200].decode("utf8", "replace"))
        if not isinstance(items, list):
            raise ExpiredSession("list failed: " + str(items)[:200])
        return [{"name": i["n"], "dir": bool(i["di"]), "size": i["s"], "mtime": i["mt"]} for i in items]

    def _download_url(self, path):
        # No `cbu=1`: with it the relay silently cuts every download off at 10 MiB, and it also ignores Range.
        return f"{self.host}/v2/file/download?" + up.urlencode(
            {"webagent": "v2", "request_purpose": 5, "path": path, "device_type": "web"})

    def get(self, path, dest_dir="."):
        r = self._req(self._download_url(path))
        name = os.path.basename(path)
        out = os.path.join(dest_dir, name)
        if "json" in (r.headers.get("Content-Type") or ""):
            raise RuntimeError("download failed: " + r.read()[:200].decode("utf8", "replace"))
        total = None
        cr = r.headers.get("Content-Range") or ""
        if "/" in cr and cr.rsplit("/", 1)[1].isdigit():
            total = int(cr.rsplit("/", 1)[1])
        elif (r.headers.get("Content-Length") or "").isdigit():
            total = int(r.headers["Content-Length"])
        with open(out, "wb") as f:
            while True:
                b = r.read(1 << 20)
                if not b:
                    break
                f.write(b)
        got = os.path.getsize(out)
        if total is not None and got != total:
            raise RuntimeError(f"download incomplete: got {got} of {total} bytes")
        return {"path": out, "size": got}

    def read_range(self, path, start, end, size):
        """Bytes [start, end] (inclusive) of a remote file of known `size`. (The server does not clamp `end`.)"""
        end = min(end, size - 1)
        if start > end:
            return b""
        r = self._req(self._download_url(path), headers={"Range": f"bytes={start}-{end}"})
        if "json" in (r.headers.get("Content-Type") or ""):
            raise RuntimeError("read failed: " + r.read()[:200].decode("utf8", "replace"))
        if not (r.headers.get("Content-Range") or "").startswith(f"{start}-"):
            raise RuntimeError("server ignored the Range request")
        data = r.read(end - start + 1)  # open-ended replies keep streaming; take only what was asked for
        if len(data) != end - start + 1:
            raise RuntimeError(f"short read: got {len(data)} of {end - start + 1} bytes")
        return data

    def put(self, local, remote_dir, rename=0, position=(1, 1)):
        size = os.path.getsize(local)
        mtime = int(os.path.getmtime(local))
        rpath = remote_dir.rstrip("/") + "/" + os.path.basename(local)
        uid = hashlib.sha1(f"{rpath}{size}{mtime}".encode()).hexdigest()
        s = self.s
        seek, last = 0, None
        with open(local, "rb") as f:
            while True:
                chunk = f.read(CHUNK)
                if not chunk and seek:
                    break
                h = {"Content-Type": "application/octet-stream", "app": "file",
                     "path": up.quote(rpath, safe=""), "size": str(size), "uuid": uid, "seek": str(seek),
                     "crtime": "", "modify_time": str(mtime), "rename": str(rename), "token": s["token"],
                     "plat": "web", "nasid": s["nasid"], "version": s["version"], "device_id": s["device_id"],
                     "device": up.quote(s["device"], safe=""), "remote-port": s["remote_port"],
                     "request-purpose": "4", "device-type": "web", "split": "1"}
                url = f"{self.host}/v2/file/upload?remote_port={s['remote_port']}&{self._rnd()}"
                last = json.loads(self._req(url, chunk, h, "POST").read())
                if str(last.get("code")) != "200":
                    raise RuntimeError(f"upload failed at {seek}: {last}" +
                                       (" N001301" if str(last.get("code")) == "N001301" else ""))
                seek += len(chunk)
                print(json.dumps({"file": os.path.basename(local), "index": position[0], "of": position[1],
                                  "percent": min(100, int(seek * 100 / max(size, 1)))}), file=sys.stderr, flush=True)
                if seek >= size:
                    break
        return last.get("data") or {"path": rpath}


# --------------------------------------------------------------------------
# Login (email + emailed code -> device -> NAS username/password)
# --------------------------------------------------------------------------
DISPATCH = "https://dispatch.ud.link"
WEB = "https://www.ud.link"
UA = "Mozilla/5.0 (X11; Linux x86_64; rv:158.0) Gecko/20100101 Firefox/158.0"
PROFILE = os.path.expanduser("~/.config/unifydrive/profile.json")  # email/username only, no secrets
VERSION = "2.3.2026101001"
DEVICE = "PC (Firefox/158.0)"


def _rsa(text, b64key):
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key, load_pem_public_key
    if "BEGIN" in b64key:
        key = load_pem_public_key(b64key.encode())
    else:
        key = load_der_public_key(base64.b64decode(b64key))
    return base64.b64encode(key.encrypt(text.encode(), padding.PKCS1v15())).decode()


def _aes(data, key, iv, decrypt=False):
    from cryptography.hazmat.primitives import padding as sympad
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    c = Cipher(algorithms.AES(key), modes.CBC(iv))
    if decrypt:
        d = c.decryptor(); raw = d.update(data) + d.finalize()
        u = sympad.PKCS7(128).unpadder(); return u.update(raw) + u.finalize()
    e = c.encryptor(); p = sympad.PKCS7(128).padder()
    return e.update(p.update(data) + p.finalize()) + e.finalize()


def _call(url, fields=None, cookie="", get=False):
    h = {"User-Agent": UA}
    if cookie:
        h["Cookie"] = cookie
    if get:
        req = ur.Request(url, headers=h)
    else:
        h["Content-Type"] = "application/x-www-form-urlencoded"
        req = ur.Request(url, up.urlencode(fields).encode(), h)
    d = json.loads(ur.urlopen(req, timeout=30).read())
    if str(d.get("code")) != "200":
        raise RuntimeError(d.get("msg") or d.get("message") or str(d))
    return d.get("data") or {}


def _rnd():
    return f"rnd={int(time.time()*1000)}_{random.randint(0, 9999)}&webagent=v2"


def _ask(prompt, hidden=False, default=""):
    """Prompt via zenity when a display is available, else on the terminal."""
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY"):
        cmd = ["zenity", "--entry", "--title=UnifyDrive", f"--text={prompt}", f"--entry-text={default}"]
        if hidden:
            cmd.append("--hide-text")
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError("login cancelled")
        return r.stdout.strip()
    import getpass
    return (getpass.getpass if hidden else input)(prompt + " ")


class NeedsInteraction(RuntimeError):
    """Raised by a non-interactive login when a captcha / emailed code is required."""


def _secret(kind, value=None, clear=False):
    """Keyring access (libsecret). Returns the stored value, or '' when unavailable."""
    base = ["secret-tool"]
    attrs = ["service", "unifydrive", "kind", kind]
    try:
        if clear:
            subprocess.run(base + ["clear"] + attrs, capture_output=True)
            return ""
        if value is not None:
            subprocess.run(base + ["store", f"--label=UnifyDrive {kind}"] + attrs, input=value, text=True,
                           check=True, capture_output=True)
            return value
        r = subprocess.run(base + ["lookup"] + attrs, capture_output=True, text=True)
        return r.stdout if r.returncode == 0 else ""
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""


def end_session():
    """Log the stored NAS session out on the server (best effort) and delete the local session file.

    Saved credentials are kept so the next login can be automatic.
    """
    if not os.path.exists(SESSION):
        return {"server": "no session", "local": "none"}
    server = "ok"
    try:
        if not Drive().logout():
            server = "refused"
    except Exception as e:  # expired / unreachable: nothing left to end server-side
        server = f"skipped ({type(e).__name__})"
    os.remove(SESSION)
    return {"server": server, "local": "removed"}


def _profile():
    return json.load(open(PROFILE)) if os.path.exists(PROFILE) else {}


def _save_profile(prof):
    os.makedirs(os.path.dirname(PROFILE), exist_ok=True)
    fd = os.open(PROFILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(prof, f)


def _cloud_devices(token, base):
    """Device list for a cloud token, or None when the token is no longer accepted."""
    try:
        return _call(f"{DISPATCH}/v2/qc/list?&{_rnd()}", {"token": token, **{x: base[x] for x in ("plat", "version", "device_id", "device", "_l")}})["list"]
    except Exception:
        return None


def _cloud_login(email, base, key):
    """Interactive: captcha -> emailed code -> cloud token."""
    cuuid = str(uuidlib.uuid4())
    cap = _call(f"{DISPATCH}/v2/captcha/{cuuid}?{_rnd()}", get=True)
    img = os.path.join(os.environ.get("XDG_RUNTIME_DIR", "/tmp"), "unifydrive-captcha.png")
    fd = os.open(img, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(base64.b64decode(cap["image"]))
    subprocess.Popen(["xdg-open", img], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    code = _ask("Captcha shown in the image window")
    _call(f"{DISPATCH}/v2/vcode/send/by/email?&{_rnd()}",
          {"key_id": key["key_id"], "email": _rsa(email, key["key"]), "uuid": cuuid, "captcha_code": code,
           "check": 1, **base, "token": "undefined"})
    vcode = _ask(f"6-digit code emailed to {email}")
    tok = _call(f"{DISPATCH}/v2/vcode/login?&{_rnd()}",
                {"email": _rsa(email, key["key"]), "vcode": vcode, "device": DEVICE, "plat": "web",
                 "version": VERSION, "device_id": base["device_id"], "_l": "en_us", "token": "undefined"})["token"]
    try:
        os.remove(img)
    except OSError:
        pass
    return tok


def login(interactive=True, email=None, username=None, forget=False):
    """Log in to the NAS and write the session file.

    Reuses the saved cloud token and NAS credentials, so the captcha / emailed code is only needed the
    first time (or when the cloud token stops being accepted). With interactive=False it raises
    NeedsInteraction instead of prompting for the captcha.
    """
    if forget:
        for k in ("cloud_token", "password"):
            _secret(k, clear=True)
        if os.path.exists(PROFILE):
            os.remove(PROFILE)
        return {"ok": True, "forgot": True}

    end_session()  # never leave the previous session open behind a new login
    prof = _profile()
    email = email or prof.get("email")
    if not email:
        if not interactive:
            raise NeedsInteraction("no account saved yet - run login once")
        email = _ask("UnifyDrive account email")
    device_id = prof.get("device_id") or uuidlib.uuid4().hex
    base = {"plat": "web", "version": VERSION, "device_id": device_id, "device": DEVICE, "_l": "en_us"}
    key = _call(f"{DISPATCH}/v2/ud/key/pub?signme=1&{_rnd()}", get=True)

    # 1. cloud token: saved one if still valid, otherwise captcha + emailed code
    tok = _secret("cloud_token").strip()
    devs = _cloud_devices(tok, base) if tok else None
    if devs is None:
        if not interactive:
            raise NeedsInteraction("captcha / emailed code required")
        tok = _cloud_login(email, base, key)
        _secret("cloud_token", tok)
        devs = _cloud_devices(tok, base)
        if devs is None:
            raise RuntimeError("cloud login succeeded but the device list was refused")
    devs = [d for d in devs if d.get("state") == 10 and d.get("activated") == 1]
    if not devs:
        raise RuntimeError("no online devices on this account")
    dev = next((d for d in devs if d["nasid"] == prof.get("nasid")), None)
    if dev is None:
        dev = devs[0]
        if len(devs) > 1:
            if not interactive:
                raise NeedsInteraction("device choice required")
            pick = _ask("Device number: " + ", ".join(f"{i}={d['model']} {d['nasid']}" for i, d in enumerate(devs)), default="0")
            dev = devs[int(pick)]
    nasid = dev["nasid"]

    # 2. AES agreement -> nas_id / uuid cookies
    ruid = str(uuidlib.uuid4())
    h = hashlib.sha256(f"{ruid}_{random.random()}{int(time.time()*1000)}".encode()).hexdigest()
    ag = _call(f"{DISPATCH}/v2/ud/agree?&{_rnd()}", {"uuid": ruid, "rid": _rsa(h, key["key"]), "key_id": key["key_id"],
               **base, "token": "undefined", "nasid": nasid})
    x = base64.b64decode(ag["rid"])
    mix = bytearray(2 * len(h))
    for i, ch in enumerate(h):
        mix[2 * i], mix[2 * i + 1] = ord(ch), x[i]
    pk = hashlib.sha256(bytes(mix)).digest()
    aeskey, iv = pk[:32], pk[:16]
    _aes(base64.b64decode(ag["agree_info"]), aeskey, iv, decrypt=True)
    nas_id_cookie = base64.b64encode(_aes(nasid.encode(), aeskey, iv)).decode()
    _call(f"{DISPATCH}/v2/ud/check?&{_rnd()}", {"plain": nasid, "cipher": nas_id_cookie, "uuid": ruid,
          **base, "token": "undefined", "nasid": nasid})
    cookie = "; ".join(["_l=en_us", f"version={VERSION}", f"device_id={device_id}", f"cloudEmail={email}",
                        f"cloudToken={tok}", "devicePdt=t2", "deviceMode=ut2", f"nas_id={nas_id_cookie}", f"uuid={ruid}"])

    # 3. NAS login with the saved (or prompted) username / password
    nk = _call(f"{WEB}/key/pub?signme=1&{_rnd()}", cookie=cookie, get=True)
    username = username or prof.get("username")
    password = _secret("password")
    asked = False
    if not username or not password:
        if not interactive:
            raise NeedsInteraction("NAS username / password not saved yet")
        username = username or _ask("NAS username")
        password = password or _ask("NAS password", hidden=True)
        asked = True
    res = _call(f"{WEB}/auth/login?&{_rnd()}",
                {"username": _rsa(username, nk["key"]), "password": _rsa(password, nk["key"]),
                 "plat": "web", "version": VERSION, "device_id": device_id, "device": DEVICE, "_l": "en_us",
                 "token": "undefined", "nasid": nasid}, cookie=cookie)
    if asked:  # only persist credentials once they have actually worked
        if not _secret("password", password):
            raise RuntimeError("login worked but the password could not be saved (is the keyring unlocked?)")

    s = {"cookie_raw": f"nas_id={nas_id_cookie}; uuid={ruid}; zenithtoken={res['token']}", "token": res["token"],
         "nasid": nasid, "device_id": device_id, "device": DEVICE, "version": VERSION, "host": WEB,
         "remote_port": "8050", "ua": UA}
    os.makedirs(os.path.dirname(SESSION), exist_ok=True)
    fd = os.open(SESSION, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(s, f)
    _save_profile({"email": email, "username": username, "device_id": device_id, "nasid": nasid})
    return {"ok": True, "user": res.get("username"), "nas": nasid}

class Conflict(RuntimeError):
    pass


def _conflict_choice(name, remembered):
    """Ask once per clash (zenity); the *(all)* answers are remembered for the rest of the batch."""
    if remembered.get("all"):
        return remembered["all"]
    if not (os.environ.get("WAYLAND_DISPLAY") or os.environ.get("DISPLAY")):
        return "skip"
    opts = ["Keep both", "Overwrite", "Skip", "Keep both (all)", "Overwrite (all)", "Skip (all)"]
    r = subprocess.run(["zenity", "--list", "--title=UnifyDrive upload", f"--text=\"{name}\" already exists",
                        "--column=What to do", "--hide-header", "--height=330", *opts], capture_output=True, text=True)
    pick = r.stdout.strip() if r.returncode == 0 else "Skip (all)"  # cancelling stops asking
    action = {"Keep both": "keep", "Overwrite": "overwrite", "Skip": "skip"}.get(pick.replace(" (all)", ""), "skip")
    if pick.endswith("(all)") or r.returncode != 0:
        remembered["all"] = action
    return action


def upload_many(d, sources, remote_dir, policy="ask"):
    """Upload files and whole folders. policy: ask | keep | overwrite | skip. Progress goes to stderr as JSON lines."""
    jobs = []  # (local_file, remote_dir)
    for src in sources:
        src = os.path.abspath(src)
        if os.path.isdir(src):
            root = d.run(d.mkdir, remote_dir, os.path.basename(src))
            for cur, dirs, files in os.walk(src):
                dirs.sort()
                rel = os.path.relpath(cur, src)
                dest = root if rel == "." else root.rstrip("/") + "/" + rel.replace(os.sep, "/")
                if rel != ".":
                    parent, name = dest.rsplit("/", 1)
                    d.run(d.mkdir, parent, name)
                jobs += [(os.path.join(cur, f), dest) for f in sorted(files)]
        elif os.path.isfile(src):
            jobs.append((src, remote_dir))
        else:
            raise RuntimeError(f"not found: {src}")
    out = {"uploaded": 0, "skipped": 0, "failed": []}
    remembered = {}
    for i, (local, dest) in enumerate(jobs, 1):
        name = os.path.basename(local)
        print(json.dumps({"file": name, "index": i, "of": len(jobs), "percent": 0}), file=sys.stderr, flush=True)
        mode = {"keep": 1, "overwrite": 3}.get(policy, 0)
        try:
            for attempt in (0, 1):
                try:
                    d.run(d.put, local, dest, mode, (i, len(jobs)))
                    out["uploaded"] += 1
                    break
                except RuntimeError as e:
                    if "N001301" not in str(e) or attempt:
                        raise
                    choice = policy if policy != "ask" else _conflict_choice(name, remembered)
                    if choice == "skip":
                        out["skipped"] += 1
                        break
                    mode = {"keep": 1, "overwrite": 3}[choice]
        except Exception as e:
            out["failed"].append({"file": name, "error": str(e)[:160]})
    return out


# --------------------------------------------------------------------------
# One-way folder sync (local -> NAS). Never deletes anything on the NAS.
# --------------------------------------------------------------------------
SYNC_CFG = os.path.expanduser("~/.config/unifydrive/sync.json")
SYNC_SKIP_SUFFIX = (".part", ".tmp", ".crdownload", "~", ".swp")


def _sync_cfg():
    try:
        cfg = json.load(open(SYNC_CFG))
    except (OSError, ValueError):
        cfg = {}
    cfg.setdefault("jobs", [])
    cfg.setdefault("state", {})
    return cfg


def _save_sync(cfg):
    os.makedirs(os.path.dirname(SYNC_CFG), exist_ok=True)
    with open(SYNC_CFG + ".tmp", "w") as f:
        json.dump(cfg, f, indent=1)
    os.replace(SYNC_CFG + ".tmp", SYNC_CFG)


def sync_add(local, remote, name="", live=False):
    local = os.path.abspath(os.path.expanduser(local))
    if not os.path.isdir(local):
        raise RuntimeError(f"not a folder: {local}")
    cfg = _sync_cfg()
    name = name or os.path.basename(local.rstrip("/")) or "sync"
    cfg["jobs"] = [j for j in cfg["jobs"] if j["name"] != name] + [{"name": name, "local": local, "remote": remote.rstrip("/"), **({"live": True} if live else {})}]
    _save_sync(cfg)
    return {"added": name}


def sync_remove(name):
    cfg = _sync_cfg()
    cfg["jobs"] = [j for j in cfg["jobs"] if j["name"] != name]
    cfg["state"].pop(name, None)
    _save_sync(cfg)
    return {"removed": name}


def sync_list():
    cfg = _sync_cfg()
    timer = subprocess.run(["systemctl", "--user", "is-enabled", "unifydrive-sync.timer"], capture_output=True, text=True).stdout.strip()
    return {"jobs": cfg["jobs"], "state": cfg["state"], "timer": timer == "enabled"}


def _ensure_remote(d, path):
    parts = path.strip("/").split("/")
    for i in range(3, len(parts)):                    # /<pool>/<user>/<share> exist already
        d.run(d.mkdir, "/" + "/".join(parts[:i]), parts[i])


def sync_one(d, job, dry=False):
    local, remote = job["local"], job["remote"]
    if not os.path.isdir(local):
        raise RuntimeError(f"local folder is gone: {local}")
    if not dry:
        _ensure_remote(d, remote)
    out = {"uploaded": 0, "skipped": 0, "failed": [], "changed": 0, "recent": 0}
    now = time.time()
    made = {remote}
    for cur, dirs, files in os.walk(local):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        rel = os.path.relpath(cur, local)
        dest = remote if rel == "." else remote + "/" + rel.replace(os.sep, "/")
        todo = [f for f in sorted(files) if not f.startswith(".") and not f.endswith(SYNC_SKIP_SUFFIX)]
        if not todo:
            continue
        try:
            have = {e["name"]: e for e in d.run(d.ls, dest) if not e["dir"]}
        except Exception:
            have = {}
            if not dry and dest not in made:
                _ensure_remote(d, dest)
        made.add(dest)
        for f in todo:
            lp = os.path.join(cur, f)
            try:
                st = os.stat(lp)
            except OSError:
                continue
            if now - st.st_mtime < 8:                  # still being written
                out["skipped"] += 1
                out["recent"] += 1
                continue
            r = have.get(f)
            if r and r["size"] == st.st_size and abs(int(r["mtime"]) - int(st.st_mtime)) <= 2:
                out["skipped"] += 1
                continue
            if dry:
                out["uploaded"] += 1
                continue
            try:
                d.run(d.put, lp, dest, 3 if r else 0, (1, 1))
                out["uploaded"] += 1
                out["changed"] += 1 if r else 0
            except Exception as e:
                out["failed"].append({"file": os.path.relpath(lp, local), "error": str(e)[:160]})
    return out


def sync_run(d, name="", dry=False):
    cfg = _sync_cfg()
    jobs = [j for j in cfg["jobs"] if not name or j["name"] == name]
    if not jobs:
        raise RuntimeError("no sync folders configured" if not name else f"no such sync folder: {name}")
    res = {}
    for j in jobs:
        try:
            r = sync_one(d, j, dry)
            r["ts"] = int(time.time())
        except Exception as e:
            r = {"error": str(e)[:200], "ts": int(time.time())}
        res[j["name"]] = r
        if not dry:
            cfg = _sync_cfg()
            cfg["state"][j["name"]] = r
            _save_sync(cfg)
    return res


def sync_timer(mode):
    """Turn the 30-minute background sync on or off (a systemd user timer that runs `sync-run`)."""
    udir = os.path.expanduser("~/.config/systemd/user")
    if mode == "on":
        os.makedirs(udir, exist_ok=True)
        with open(os.path.join(udir, "unifydrive-sync.service"), "w") as f:
            f.write(f"[Unit]\nDescription=UnifyDrive folder sync\n\n[Service]\nType=oneshot\nNice=10\n"
                    f"ExecStart={sys.executable} -I {os.path.abspath(__file__)} sync-run\n")
        with open(os.path.join(udir, "unifydrive-sync.timer"), "w") as f:
            f.write("[Unit]\nDescription=UnifyDrive folder sync every 30 minutes\n\n[Timer]\nOnBootSec=3min\nOnUnitActiveSec=30min\n"
                    "Persistent=true\n\n[Install]\nWantedBy=timers.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "unifydrive-sync.timer"], check=True, capture_output=True)
    else:
        subprocess.run(["systemctl", "--user", "disable", "--now", "unifydrive-sync.timer"], capture_output=True)
    return sync_list()


VIDEO_EXT = {"mp4", "mkv", "mov", "avi", "webm", "m4v", "wmv", "flv", "ts", "mpg", "mpeg"}
AUDIO_EXT = {"mp3", "flac", "m4a", "wav", "ogg", "opus", "aac", "wma", "ape", "alac"}


def _kind(path):
    e = path.lower().rsplit(".", 1)[-1]
    return "audio" if e in AUDIO_EXT else "video" if e in VIDEO_EXT else None


def _get_json(d, endpoint, params):
    q = up.urlencode({**params, "webagent": "v2"})
    r = d._req(f"{d.host}{endpoint}?{q}", headers={"Accept": "application/json"})
    return json.loads(r.read())


def resolve_stream(d, path):
    """Ask the UT2 for a streamable URL: the original file over ranged HTTP for video and audio (audio falls back to an
    HLS transcode, and UD_AUDIO_TRANSCODE=1 / UD_VIDEO_TRANSCODE=1 ask for the transcodes)."""
    kind = _kind(path)
    if kind == "audio" and not os.environ.get("UD_AUDIO_TRANSCODE"):
        # The original file (a FLAC stays FLAC) through the local audio proxy; the HLS transcode below is the fallback.
        url = audio_direct_url(path)
        if url:
            return {"url": url, "kind": kind, "duration": None, "title": os.path.basename(path)}
    if kind == "video" and not os.environ.get("UD_VIDEO_TRANSCODE"):
        # The original file, over ranged HTTP: keeps every audio and subtitle track (the server's transcode /splay
        # copy silently drops text subtitles). Set UD_VIDEO_TRANSCODE=1 for the lighter transcoded stream instead.
        return {"url": d._download_url(path), "kind": kind, "duration": None, "title": os.path.basename(path)}
    if kind == "audio":
        r = _get_json(d, "/transcode/audio/play", {"file_path": path, "dest_fmt": "HQ", "app": "music"})
    elif kind == "video":
        r = _get_json(d, "/transcode/play", {"file_path": path, "play_env": 2, "video_codec": "h264,vp8,vp9,hevc",
                                             "audio_codec": "aac,mp4a,vorbis", "from": "zvideo"})
    else:
        raise RuntimeError("not a media file")
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"play refused: {r.get('code')} {r.get('msg')}")
    data = r["data"]
    url = hls_proxy_url(data["uri"])
    if not url:
        raise RuntimeError("could not start the local media proxy")
    return {"url": url, "kind": kind, "duration": data.get("duration"), "title": os.path.basename(path)}


# mpv never gets the NAS session cookie. Every network stream it opens is a localhost proxy URL (range proxy for video,
# the audio proxy for music and for the HLS transcodes), and the proxy adds the cookie itself, only on requests to the
# NAS. (mpv's http-header-fields go to EVERY host it contacts, so a contributed file that mpv reads as a playlist could
# otherwise send the cookie to any server.) The proxy URLs carry a bearer token, so they reach mpv through a 0600 file
# in a private directory, never on the command line where other local accounts can read them in `ps`.
MPV_NET = ["--ytdl=no", "--load-unsafe-playlists=no"]


def _private_dir(name):
    """A directory of ours (mode 0700, not a symlink) under the runtime dir, for files holding bearer URLs."""
    p = os.path.join(_runtime(), name)
    os.makedirs(p, mode=0o700, exist_ok=True)
    st = os.lstat(p)
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        raise RuntimeError(f"refusing to use {p}: not a private directory of this user")
    return p


def _url_file(url, name):
    """A 0600 playlist holding one stream URL, so the URL is not in mpv's argv."""
    if "\n" in url or "\r" in url:
        raise RuntimeError("bad stream URL")
    f = os.path.join(_private_dir("unifydrive-play"), name + ".m3u")
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write("#EXTM3U\n" + url + "\n")
    return f


def _is_proxy_url(url):
    return url.startswith("http://127.0.0.1:")


def mpv_conf(d=None):
    """mpv options for NAS playback: the user's optional extra config. (Holds no secrets.)"""
    try:   # earlier versions left the session cookie here
        os.remove(os.path.join(_runtime(), "unifydrive-mpv.conf"))
    except OSError:
        pass
    extra = os.path.expanduser("~/.config/unifydrive/mpv-extra.conf")  # optional user mpv options, one per line
    return [*MPV_NET, *([f"--include={extra}"] if os.path.exists(extra) else [])]


def play(d, path, extra_args=(), url=None, url_name=None):
    local = video_local(path) if _kind(path) == "video" else None
    if local:                                            # saved for offline: plain file, no network, no cookie
        info = {"url": local[0], "kind": "video", "duration": None, "title": os.path.basename(path)}
        cmd = ["mpv", "--force-window=yes", "--keep-open=no", f"--title={info['title']}",
               f"--force-media-title={os.path.splitext(info['title'])[0]}", "--slang=en,eng,english",
               *[f"--sub-file={x}" for x in local[1]], *extra_args, local[0]]
        return info, cmd
    info = resolve_stream(d, path) if not url else {"url": url, "kind": _kind(path), "duration": None,
                                                     "title": os.path.basename(path)}
    if not _is_proxy_url(info["url"]):
        raise RuntimeError("refusing to hand mpv a stream that needs the NAS session")
    cmd = ["mpv", "--force-window=yes", "--keep-open=no", f"--title={info['title']}",
           f"--force-media-title={os.path.splitext(info['title'])[0]}"]
    if info["kind"] == "video":
        # defaults first so ~/.config/unifydrive/mpv-extra.conf (via --include) can override them:
        # show English captions when the file has them, and buffer ahead to ride out bitrate peaks on the relay
        cmd += ["--slang=en,eng,english", "--cache=yes", "--demuxer-max-bytes=200MiB", "--demuxer-readahead-secs=90"]
    cmd += [*mpv_conf(), *extra_args, _url_file(info["url"], url_name or info["kind"] or "media")]
    if info["kind"] == "audio":
        cmd.insert(1, "--no-video")
    return info, cmd

# --------------------------------------------------------------------------
# Music library (the UT2's built-in music app) + player control
# --------------------------------------------------------------------------
def _runtime():
    return os.environ.get("XDG_RUNTIME_DIR", "/tmp")


SOCK = os.path.join(_runtime(), "unifydrive-mpv.sock")


def albums(d, page=1, size=100):
    r = _get_json(d, "/zmusic/api/v2/album/list", {"filter_type": "", "filter_value": "", "page": page,
                                                    "page_size": size, "sort_field": "album", "order": "asc"})
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"album list refused: {r.get('code')}")
    data = r["data"]
    return {"count": data.get("count", 0), "page": page,
            "list": [{"id": a["album_id"], "name": a["album"], "artist": a.get("artist", ""),
                      "tracks": a.get("count"), "duration": a.get("duration")} for a in data.get("list", [])]}


def songs(d, album_id):
    try:
        return _songs(d, album_id)
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        m = album_meta(album_id)
        if not m:
            raise
        return [{"id": "", "title": t["title"], "artist": t["artist"], "album": t["album"], "cover": t["cover"], "path": "", "ms": t.get("ms", 0)}
                for t in m["tracks"]]


def _songs(d, album_id):
    r = d.post("/zmusic/api/v2/song/list", page=1, page_size=500, filter_type="album", filter_value=album_id,
               sort_field="title", order="asc")
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"song list refused: {r.get('code')}")

    def order(s):
        def num(x):
            try:
                return int(str(x).split("/")[0])
            except ValueError:
                return 0
        return (num(s.get("song_disc")), num(s.get("song_track")))
    lst = sorted(r["data"].get("list", []), key=order)
    return [{"id": s["song_id"], "title": s.get("song_title") or s.get("song_name"), "artist": s.get("artist", ""),
             "album": s.get("album", ""), "cover": s.get("song_cover", ""),
             "path": s["song_path"], "ms": int(s.get("song_duration") or 0)} for s in lst]


def _ipc(cmd, timeout=3, sock=None):
    import socket
    s = socket.socket(socket.AF_UNIX)
    s.settimeout(timeout)
    s.connect(sock or SOCK)
    rid = random.randint(1, 1 << 30)
    try:
        s.sendall((json.dumps({"command": cmd, "request_id": rid}) + "\n").encode())
        buf = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                raise RuntimeError("player closed")
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if msg.get("request_id") == rid:  # skip async events
                    if msg.get("error") != "success":
                        raise RuntimeError(msg.get("error"))
                    return msg.get("data")
    finally:
        s.close()


def player(action):
    """Control the music mpv instance started by play-album."""
    try:
        if action == "pause":
            _ipc(["cycle", "pause"])
        elif action == "next":
            _ipc(["playlist-next"])
        elif action == "prev":
            _ipc(["playlist-prev"])
        elif action == "stop":
            _ipc(["quit"])
            return {"playing": False}
        elif action.startswith("seek"):
            _ipc(["seek", action.split(":", 1)[1], "absolute"])
        elif action.startswith("volume:"):
            _ipc(["set_property", "volume", max(0, min(130, int(action.split(":", 1)[1])))])
        elif action.startswith("jump:"):
            try:
                _ipc(["set_property", "playlist-pos", int(action.split(":", 1)[1])])
            except RuntimeError:        # that track has not been fed to mpv yet
                pass
        idx = _ipc(["get_property", "playlist-pos"])
        meta = _ipc(["get_property", "metadata"]) or {}
        cover = ""
        queue = []
        try:
            q = json.load(open(QUEUE))
            queue = [{"title": x.get("title", ""), "artist": x.get("artist", "")} for x in q]
            if idx is not None and 0 <= idx < len(q):
                cover = q[idx].get("cover", "")
        except (OSError, ValueError):
            pass
        loaded = _ipc(["get_property", "playlist-count"]) or 0
        return {"playing": True, "paused": _ipc(["get_property", "pause"]), "volume": _ipc(["get_property", "volume"]),
                "queue": queue, "loaded": loaded,
                "title": meta.get("title") or _ipc(["get_property", "media-title"]),
                "artist": meta.get("artist", ""), "album": meta.get("album", ""), "cover": cover,
                "pos": _ipc(["get_property", "time-pos"]), "duration": _ipc(["get_property", "duration"]),
                "index": idx, "total": _ipc(["get_property", "playlist-count"])}
    except (FileNotFoundError, ConnectionRefusedError, OSError, RuntimeError):
        return {"playing": False}


QUEUE = os.path.join(_runtime(), "unifydrive-queue.json")


def _edl_body(url, meta):
    """The EDL text (one global_tags line + the stream) that gives mpv, and MPRIS widgets, real title/artist/album."""
    def tag(k, v):
        v = str(v or "")
        return f"{k}=%{len(v.encode())}%{v}"
    tags = ",".join(tag(k, meta.get(k)) for k in ("title", "artist", "album") if meta.get(k))
    return f"!global_tags,{tags};%{len(url.encode())}%{url}"


NP_DIR = os.path.join(_runtime(), "unifydrive-np")


def cover_file(d, src):
    """Local copy of an album cover (shared cache with the cover proxy); None if it can't be fetched."""
    if not src or ".." in src or not src.startswith(("/zmusic_static/", "/zvideo_static/")):
        return None
    cdir = os.path.join(CACHE, "covers")
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    f = os.path.join(cdir, hashlib.sha1(src.encode()).hexdigest() + ".jpg")
    if not os.path.exists(f):
        try:
            data = d._req(d.host + src).read()
        except Exception:
            return None
        with open(f + ".tmp", "wb") as fh:
            fh.write(data)
        os.replace(f + ".tmp", f)
    return f


def _track_file(d, url, meta, idx):
    """Write the track as a local .edl next to its cover.jpg. mpv-mpris only looks for cover art beside LOCAL files
    (or YouTube links), so a bare edl:// URL never shows a cover in media widgets; this does."""
    sub = hashlib.sha1((meta.get("cover") or "none").encode()).hexdigest()[:12]
    dp = os.path.join(NP_DIR, sub)
    os.makedirs(dp, mode=0o700, exist_ok=True)
    cover = cover_file(d, meta.get("cover"))
    link = os.path.join(dp, "cover.jpg")
    if cover and not os.path.exists(link):
        shutil.copyfile(cover, link)
    f = os.path.join(dp, f"{idx:04d}.edl")
    if not _is_proxy_url(url):
        raise RuntimeError("refusing to hand mpv a stream that needs the NAS session")
    fd = os.open(f, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)   # holds a proxy URL with its bearer token
    with os.fdopen(fd, "w") as fh:
        fh.write("# mpv EDL v0\n" + _edl_body(url, meta) + "\n")
    return f


RECENT = os.path.expanduser("~/.config/unifydrive/recent.json")


def recent_set(kind, **fields):
    try:
        data = json.load(open(RECENT))
    except (OSError, ValueError):
        data = {}
    data[kind] = {**fields, "ts": int(time.time())}
    os.makedirs(os.path.dirname(RECENT), exist_ok=True)
    with open(RECENT + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(RECENT + ".tmp", RECENT)


def continue_items():
    """What to pick up again: the newest unfinished video, the newest comic, the last album. Local files only."""
    out = []
    vids = [(p, v) for p, v in video_progress().items() if not v.get("done") and v.get("pos", 0) > 15]
    if vids:
        p, v = max(vids, key=lambda kv: kv[1].get("ts", 0))
        out.append({"kind": "video", "path": p, "title": os.path.splitext(os.path.basename(p))[0], "pos": v["pos"],
                    "dur": v.get("dur", 0), "ts": v.get("ts", 0)})
    try:
        comics = json.load(open(COMIC_PROGRESS))
    except (OSError, ValueError):
        comics = {}
    if comics:
        sp, c = max(comics.items(), key=lambda kv: kv[1].get("ts", 0))
        out.append({"kind": "comic", "series": sp, "title": os.path.basename(sp), "chapter": c.get("chapter", ""),
                    "page": c.get("page", 0), "ts": c.get("ts", 0)})
    try:
        m = json.load(open(RECENT)).get("music")
    except (OSError, ValueError):
        m = None
    if m:
        out.append({"kind": "music", "id": m["id"], "title": m.get("name", ""), "cover": m.get("cover", ""), "ts": m["ts"]})
    return sorted(out, key=lambda x: -x["ts"])


def play_album(d, album_id, start=0, name="", cover=""):
    """Start the first track immediately; a detached feeder resolves and appends the rest."""
    if album_meta(album_id):
        return play_album_offline(album_id, start)
    tracks = songs(d, album_id)[start:]
    recent_set("music", id=album_id, name=name or (tracks[0]["album"] if tracks else ""), cover=cover or (tracks[0]["cover"] if tracks else ""))
    if not tracks:
        raise RuntimeError("album has no songs")
    player("stop")  # one music player at a time
    try:
        os.remove(SOCK)
    except OSError:
        pass
    shutil.rmtree(NP_DIR, ignore_errors=True)   # previous album's track files
    fd = os.open(QUEUE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump([{"title": x["title"], "artist": x["artist"], "album": x["album"], "cover": x["cover"]}
                   for x in tracks], f)
    first = resolve_stream(d, tracks[0]["path"])
    cmd = ["mpv", "--no-video", "--idle=no", "--force-window=no", f"--input-ipc-server={SOCK}",
           *mpv_conf(), "--title=UnifyDrive Music", _track_file(d, first["url"], tracks[0], 0)]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if os.path.exists(SOCK):
            break
        time.sleep(0.1)
    if len(tracks) > 1:
        subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "_feed"] +
                         [json.dumps({"path": x["path"], "title": x["title"], "artist": x["artist"],
                                      "album": x["album"], "cover": x["cover"], "i": n})
                          for n, x in enumerate(tracks[1:], 1)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"queued": len(tracks), "first": tracks[0]["title"]}


def feed(d, items):
    """Resolve remaining tracks a few at a time and append them to the running player, in order."""
    from concurrent.futures import ThreadPoolExecutor
    items = [json.loads(x) for x in items]
    with ThreadPoolExecutor(4) as ex:
        for meta, info in zip(items, ex.map(lambda m: resolve_stream(d, m["path"]), items)):
            try:
                _ipc(["loadfile", _track_file(d, info["url"], meta, meta["i"]), "append"])
            except Exception:
                return  # player was stopped


# --------------------------------------------------------------------------
# Video library (the UT2's built-in movies / TV / folder libraries)
# --------------------------------------------------------------------------
def _vok(r, what):
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"{what} refused: {r.get('code')} {r.get('msg')}")
    return r.get("data")


def video_libs(d):
    cls = _vok(d.post("/zvideo/classification/list"), "library list")
    libs = [{"id": "latest", "name": "Latest", "count": None}]
    libs += [{"id": c["id"], "name": c["name"], "count": c.get("collection_count")}
             for c in cls if c.get("is_enable", 1) and not c.get("web_hide")]
    return libs


def _vcard(x):
    return {"id": x["collection_id"], "cls": x["classification_id"], "title": x.get("title") or x.get("file_name", ""),
            "year": x.get("release_year") or 0, "score": x.get("score") or 0, "genres": x.get("genres", ""),
            "cover": x.get("cover", ""), "video_id": x.get("video_id", ""), "path": x.get("file_path", "")}


def video_list(d, lib):
    if lib == "latest":
        data = _vok(d.post("/zvideo/home/collection/latest", limit=60), "latest list")
    else:
        data = _vok(d.post("/zvideo/video/v2/list", classification_id=lib, start=1, limit=500), "video list")["list"]
    return [_vcard(x) for x in data]


RAW_EXT = (".dng", ".cr2", ".cr3", ".nef", ".arw", ".raf", ".orf", ".rw2")
PHOTO_DIR = os.path.expanduser("~/.cache/unifydrive/photos")
PHOTO_VIEWER_PID = os.path.join(_runtime(), "unifydrive-photos.pid")


def photo_list(d, start=0, num=60):
    """The NAS photo app's timeline, newest first. RAW originals are hidden (their .COVER.jpg companions stay).
    `next` is the offset to ask for next (it counts the hidden ones too); `total` is the unfiltered count."""
    r = d.post("/v2/album/feed/list", start=start, num=num)
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"photo list refused: {r.get('code')}")
    raw = r["data"].get("list", [])
    out = [{"id": x["id"], "name": x["name"], "path": x["path"], "w": x.get("width") or 0, "h": x.get("height") or 0,
            "ts": x.get("crtime") or 0, "video": x.get("ftype") == 102, "size": x.get("size") or 0}
           for x in raw if not x["name"].lower().endswith(RAW_EXT)]
    return {"total": int(r["data"].get("total") or 0), "list": out, "next": start + len(raw)}


def photo_get(d, path):
    """The original image in the local cache (1 GB cap, oldest evicted); returns its path."""
    os.makedirs(PHOTO_DIR, mode=0o700, exist_ok=True)
    f = os.path.join(PHOTO_DIR, hashlib.sha1(path.encode()).hexdigest()[:20] + os.path.splitext(path)[1].lower())
    if not os.path.exists(f):
        tmp = os.path.join(PHOTO_DIR, f"dl-{hashlib.sha1(path.encode()).hexdigest()[:12]}-{os.getpid()}")
        os.makedirs(tmp, exist_ok=True)
        try:
            r = d.run(d.get, path, tmp)
            os.replace(r["path"], f)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        rows = sorted(((os.path.getatime(os.path.join(PHOTO_DIR, n)), os.path.join(PHOTO_DIR, n)) for n in os.listdir(PHOTO_DIR)
                       if os.path.isfile(os.path.join(PHOTO_DIR, n))))
        total = sum(os.path.getsize(x[1]) for x in rows)
        for _, old in rows:
            if total <= 1 << 30 or old == f:
                break
            total -= os.path.getsize(old)
            os.remove(old)
    os.utime(f)
    return {"path": f}


def photo_open(start, path=""):
    """Open the viewer window (its own Quickshell instance) on the timeline chunk starting at `start`, showing `path`."""
    try:
        os.kill(int(open(PHOTO_VIEWER_PID).read()), 15)
        time.sleep(0.3)
    except (OSError, ValueError):
        pass
    env = dict(os.environ, UD_HELPER=os.path.abspath(__file__), UD_START=str(start), UD_PATH=path)
    viewer = os.path.join(os.path.dirname(os.path.abspath(__file__)), "viewer")
    log = open(os.path.join(_runtime(), "unifydrive-photos.log"), "w")
    pr = subprocess.Popen(["quickshell", "-p", viewer], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    with open(PHOTO_VIEWER_PID, "w") as fh:
        fh.write(str(pr.pid))
    return {"started": True, "pid": pr.pid}


def nas_status(d):
    """Pool capacity, health and disk temperature (one request); the bar shows it in the tooltip."""
    r = d.post("/zspool/info")
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"status refused: {r.get('code')}")
    pools, temps, bad = [], [], []
    cfg = settings_get()
    for p in r["data"].get("pool_list", []):
        pools.append({"name": p["name"], "total": p["total_size"], "free": p["free_size"], "used": p["usage_size"],
                      "status": p.get("status", "")})
        if p.get("status") not in ("ok", None):
            bad.append(f"pool {p['name']}: {p['status']}")
        try:
            free_pct = 100.0 * float(p["free_size"]) / float(p["total_size"])
            if free_pct < cfg["alert_free_pct"]:
                bad.append(f"pool {p['name']} is nearly full ({free_pct:.0f}% free)")
        except (KeyError, ValueError, ZeroDivisionError):
            pass
        for k in p.get("disk_list", []):
            temps.append(k.get("temp") or 0)
            if k.get("health") not in ("ok", None) or k.get("status") not in ("ok", None):
                bad.append(f"disk {k.get('model', '?')}: {k.get('health')}/{k.get('status')}")
    if temps and max(temps) >= cfg["alert_temp"]:
        bad.append(f"disk temperature {max(temps)}°C")
    return {"online": True, "pools": pools, "temp": max(temps) if temps else None, "problems": bad}


VIDEO_CACHE = os.path.expanduser("~/.cache/unifydrive/videos.json")


def videos_all(d, max_age=86400):
    """Every title of every video library, cached for a day (search runs over this)."""
    if os.path.exists(VIDEO_CACHE) and time.time() - os.path.getmtime(VIDEO_CACHE) < max_age:
        return json.load(open(VIDEO_CACHE))
    out = []
    for lib in video_libs(d):
        if lib["id"] != "latest":
            out += video_list(d, lib["id"])
    os.makedirs(os.path.dirname(VIDEO_CACHE), mode=0o700, exist_ok=True)
    with open(VIDEO_CACHE + ".tmp", "w") as f:
        json.dump(out, f)
    os.replace(VIDEO_CACHE + ".tmp", VIDEO_CACHE)
    return out


def file_search(d, query, root, limit=40):
    """The NAS's own file index (/file_search/file_search): matches anywhere under `root`, highlights stripped."""
    r = d.post("/file_search/file_search", name=query, path=root, start=0, num=limit)
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"search refused: {r.get('code')}")
    clean = lambda t: re.sub("[\x01\x02]", "", t or "")
    return [{"kind": "file", "title": clean(i["name"]), "path": clean(i["path"]), "dir": i.get("is_dir") == "1",
             "size": int(i.get("size") or 0)} for i in r["data"]["list"]], int(r["data"].get("total") or 0)


def search_all(d, query, root, comics_root):
    """One query over everything: albums and video titles (cached lists, matched locally), comic series, and files."""
    from concurrent.futures import ThreadPoolExecutor
    words = query.lower().split()
    match = lambda *parts: all(w in " ".join(parts).lower() for w in words)
    with ThreadPoolExecutor(4) as ex:
        ffiles = ex.submit(file_search, d, query, root)
        falb = ex.submit(albums_all, d)
        fvid = ex.submit(videos_all, d)
        fcom = ex.submit(d.ls, comics_root)
        out = {"albums": [], "videos": [], "comics": [], "files": [], "files_total": 0, "errors": []}
        for key, fut in (("albums", falb), ("videos", fvid), ("comics", fcom), ("files", ffiles)):
            try:
                v = fut.result()
            except Exception as e:
                out["errors"].append(f"{key}: {e}")
                continue
            if key == "albums":
                out[key] = [{"kind": "album", "id": a["id"], "title": a["name"], "sub": a.get("artist", ""), "cover": a.get("cover", "")}
                            for a in v["list"] if match(a["name"], a.get("artist", ""))][:30]
            elif key == "videos":
                out[key] = [{"kind": "video", "id": x["id"], "cls": x["cls"], "title": x["title"], "sub": str(x["year"] or ""),
                             "cover": x["cover"], "path": x["path"]} for x in v if match(x["title"])][:30]
            elif key == "comics":
                out[key] = [{"kind": "comic", "title": i["name"], "path": comics_root.rstrip("/") + "/" + i["name"]}
                            for i in v if i["dir"] and match(i["name"])][:30]
            else:
                out["files"], out["files_total"] = v
    return out


def video_items(d, collection_id, cls):
    """Playable files of a collection: episodes of a season, or the single file of a movie / folder video."""
    r = _vok(d.post("/zvideo/collection/video", collection_id=collection_id, classification_id=cls), "episode list")
    vids = r.get("video_list") or [v for v in (r.get("file_list") or {}).get("list", []) if v.get("file_path")]
    out = []
    for v in vids:
        n = int(v.get("episode") or 0)
        out.append({"ep": v.get("ep") or (f"Episode {n}" if n else ""), "n": n, "name": v.get("episode_name") or "",
                    "overview": v.get("episode_overview", ""), "path": v["file_path"],
                    "file": v.get("file_name") or os.path.basename(v["file_path"]),
                    "duration": int(float(v.get("duration") or 0)), "size": int(v.get("file_size") or 0),
                    "thumb": v.get("episode_path", "")})
    out.sort(key=lambda x: (x["n"] or 1 << 30, x["file"]))
    return out


# --------------------------------------------------------------------------
# Album cache (for instant local search) + cover proxy
# --------------------------------------------------------------------------
CACHE = os.path.expanduser("~/.cache/unifydrive")
ALBUM_CACHE = os.path.join(CACHE, "albums.json")


def albums_all(d, refresh=False, max_age=86400):
    """Every album, cached on disk for a day. One request per 100 albums, fetched in parallel."""
    if not refresh and os.path.exists(ALBUM_CACHE) and time.time() - os.path.getmtime(ALBUM_CACHE) < max_age:
        return json.load(open(ALBUM_CACHE))
    from concurrent.futures import ThreadPoolExecutor
    try:
        first = _album_page(d, 1)
    except (urllib.error.URLError, OSError, http.client.HTTPException):
        if os.path.exists(ALBUM_CACHE):
            return json.load(open(ALBUM_CACHE))            # offline: the last copy, however old
        raise
    pages = (first["count"] + 99) // 100
    with ThreadPoolExecutor(6) as ex:
        rest = list(ex.map(lambda n: _album_page(d, n)["list"], range(2, pages + 1)))
    out = {"count": first["count"], "list": first["list"] + [a for page in rest for a in page]}
    os.makedirs(CACHE, mode=0o700, exist_ok=True)
    tmp = ALBUM_CACHE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(out, f)
    os.replace(tmp, ALBUM_CACHE)
    return out


def _album_page(d, page, size=100):
    r = _get_json(d, "/zmusic/api/v2/album/list", {"filter_type": "", "filter_value": "", "page": page,
                                                    "page_size": size, "sort_field": "album", "order": "asc"})
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"album list refused: {r.get('code')}")
    data = r["data"]
    return {"count": data.get("count", 0),
            "list": [{"id": a["album_id"], "name": a["album"], "artist": a.get("artist", ""), "tracks": a.get("count"),
                      "cover": a.get("cover", "")} for a in data.get("list", [])]}


def cover_proxy():
    """Serve album covers on 127.0.0.1 (random port + secret path) so the panel can use plain image URLs."""
    import http.server, secrets, socketserver
    d = Drive()
    token = secrets.token_urlsafe(12)
    cdir = os.path.join(CACHE, "covers")
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    lock = __import__("threading").Lock()

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            nonlocal d
            u_ = up.urlparse(self.path)
            q = up.parse_qs(u_.query)
            src = (q.get("p") or [""])[0]
            is_thumb = u_.path == f"/{token}/thumb" and src.startswith("/") and ".." not in src
            if not is_thumb and (u_.path != f"/{token}/cover" or ".." in src or not src.startswith(("/zmusic_static/", "/zvideo_static/"))):
                self.send_error(404)
                return
            fetch = src
            if is_thumb:        # a file on the NAS: the server renders the thumbnail (small ~ 200 px, large ~ 1000 px)
                fmt = "large" if (q.get("f") or [""])[0] == "large" else "small"
                fetch = "/transcode/thumb?" + up.urlencode({"file_path": src, "dest_fmt": fmt, "request_purpose": 5, "device_type": "web"})
            f = os.path.join(cdir, hashlib.sha1(fetch.encode()).hexdigest() + ".jpg")
            if not os.path.exists(f):
                try:
                    try:
                        data = d._req(d.host + fetch).read()
                    except urllib.error.HTTPError as e:
                        if e.code not in (400, 401, 403):
                            raise
                        with lock:
                            login(interactive=False)
                            d = Drive()
                        data = d._req(d.host + fetch).read()
                except Exception:
                    self.send_error(502)
                    return
                tmp = f + ".tmp"
                with open(tmp, "wb") as fh:
                    fh.write(data)
                os.replace(tmp, f)
            body = open(f, "rb").read()
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400")
            self.end_headers()
            self.wfile.write(body)

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    srv = S(("127.0.0.1", 0), H)
    print(json.dumps({"port": srv.server_address[1], "token": token}), flush=True)
    srv.serve_forever()

# --------------------------------------------------------------------------
# Full web UI in a Chromium app window, pre-signed-in with the current session
# --------------------------------------------------------------------------
WEBUI_URL = WEB + "/home/"
WEBUI_PROFILE = os.path.expanduser("~/.local/share/unifydrive/chromium-profile")


def webui_cookies(d):
    """The cookies the web app's own JavaScript expects (it reads them, so they are not HttpOnly)."""
    prof = _profile()
    s = d.s
    base = {"plat": "web", "version": s["version"], "device_id": s["device_id"], "device": s["device"], "_l": "en_us"}
    ck = {c.split("=", 1)[0].strip(): c.split("=", 1)[1] for c in d.cookie.split(";")}
    tok = _secret("cloud_token").strip()
    cloud = _call(f"{DISPATCH}/v2/ud/key/pub?signme=1&{_rnd()}", get=True)
    nk = _call(f"{WEB}/key/pub?signme=1&{_rnd()}", cookie=d.cookie, get=True)
    me = d.post("/auth/token", **{})  # user record for this session
    user = me.get("data") or {}
    qcv = (d.post("/auth/qcv").get("data") or {}).get("qcv", "")
    color = ""
    devs = _cloud_devices(tok, base) if tok else None
    if devs:
        color = next((x.get("color", "") for x in devs if x.get("nasid") == s["nasid"]), "")
    vals = {
        "_l": "en_us", "version": s["version"], "device_id": s["device_id"], "cloudEmail": prof.get("email", ""),
        "cloudToken": tok, "deviceMode": "ut2", "isAllFlash": "1", "uuid": ck["uuid"], "qcname": "",
        "nickname": user.get("nickname", ""), "userid": str(user.get("id", 1)), "isLocal": "0", "qcv": qcv,
        "publicSwitch": "true", "devicePdt": "t2", "nas_id": ck["nas_id"], "nasIp": "", "nas_name": nk.get("name", ""),
        "sign": nk.get("sign", ""), "nasPubKey": nk.get("key", ""), "zenithtoken": ck["zenithtoken"],
        "username": user.get("username", prof.get("username", "")), "isMaster": str(user.get("is_master", 1)),
        "deviceColor": color, "plat": "web", "app": "file", "device": "PC", "cloudPubKey": cloud["key"],
        "cloudPubKeyId": cloud["key_id"],
    }
    # the web app's cookie library URL-encodes values (PEM keys contain newlines), keeping base64 punctuation
    enc = lambda v: up.quote(v, safe="+/=:@")
    return [{"name": k, "value": enc(v), "domain": "www.ud.link", "path": "/", "secure": True, "httpOnly": False}
            for k, v in vals.items() if v != ""]


class _CDP:
    """Minimal Chrome DevTools Protocol client over --remote-debugging-pipe (fd 3 in, fd 4 out)."""

    def __init__(self, wfd, rfd):
        self.w, self.r, self.buf, self.n = wfd, rfd, b"", 0

    def call(self, method, params=None, session=None, timeout=20):
        self.n += 1
        msg = {"id": self.n, "method": method, "params": params or {}}
        if session:
            msg["sessionId"] = session
        os.write(self.w, json.dumps(msg).encode() + b"\0")
        end = time.time() + timeout
        while time.time() < end:
            while b"\0" in self.buf:
                raw, self.buf = self.buf.split(b"\0", 1)
                try:
                    m = json.loads(raw)
                except ValueError:
                    continue
                if m.get("id") == self.n:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error'].get('message')}")
                    return m.get("result", {})
            import select
            if select.select([self.r], [], [], 0.5)[0]:
                chunk = os.read(self.r, 1 << 20)
                if not chunk:
                    raise RuntimeError("browser closed")
                self.buf += chunk
        raise RuntimeError(f"{method}: timed out")


def _profile_in_use():
    lock = os.path.join(WEBUI_PROFILE, "SingletonLock")
    try:
        pid = int(os.readlink(lock).rsplit("-", 1)[-1])
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        return False


def webui(d, app=""):
    """Open the UnifyDrive web UI signed in as the current session (optionally on one of its apps).
    Stays alive until the window is closed."""
    url = WEBUI_URL
    if app:
        url = WEB + APP_ROUTES.get(app, "/home/" + re.sub(r"[^A-Za-z0-9_]", "", app))
    os.makedirs(WEBUI_PROFILE, mode=0o700, exist_ok=True)
    base_cmd = ["chromium", f"--user-data-dir={WEBUI_PROFILE}", "--ozone-platform=wayland", "--no-first-run",
                "--no-default-browser-check", f"--app={url}"]
    if _profile_in_use():  # a window is already open on this profile: just ask it for another one
        subprocess.Popen(base_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        return {"opened": "existing browser"}
    cookies = webui_cookies(d)
    a_r, a_w = os.pipe()  # us -> browser (browser reads fd 3)
    b_r, b_w = os.pipe()  # browser -> us (browser writes fd 4)

    # Chromium wants the pipe on fds 3 (read) and 4 (write); let bash place them to avoid clashing with Python's own fds.
    proc = subprocess.Popen(["bash", "-c", f'exec "$@" 3<&{a_r} 4>&{b_w}', "chromium", *base_cmd, "--remote-debugging-pipe"],
                            pass_fds=(a_r, b_w), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            start_new_session=True)
    os.close(a_r)
    os.close(b_w)
    cdp = _CDP(a_w, b_r)
    cdp.call("Storage.setCookies", {"cookies": cookies})
    targets = cdp.call("Target.getTargets")["targetInfos"]
    page = next((x for x in targets if x["type"] == "page"), None)
    if page:
        sess = cdp.call("Target.attachToTarget", {"targetId": page["targetId"], "flatten": True})["sessionId"]
        cdp.call("Page.navigate", {"url": url}, session=sess)
    proc.wait()  # closing our end of the pipe would close the browser, so stay until it exits
    return {"closed": True}


# ---------------------------------------------------------------- comics (CBZ read with ranged requests)
# A chapter is a .cbz (a zip of page images). The download endpoint honours Range, so we read only the zip's
# directory from the tail and then each page on demand -- a 250 MB chapter shows its first page in seconds.
COMIC_CACHE = os.path.join(CACHE, "comics")
COMIC_PROGRESS = os.path.expanduser("~/.config/unifydrive/comics.json")
COMIC_EXT = (".cbz", ".zip", ".cbr", ".rar", ".pdf")
RAR_EXT = (".cbr", ".rar")
IMG_EXT = (".jpg", ".jpeg", ".png", ".webp", ".gif")
READER_PID = os.path.join(_runtime(), "unifydrive-reader.pid")
READER_LOG = os.path.join(_runtime(), "unifydrive-reader.log")


def _natkey(s):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", s)]


class RemoteFile(io.RawIOBase):
    """Seekable read-only view of a remote file; only used so zipfile can read the central directory."""
    TAIL = 512 * 1024

    def __init__(self, d, path, size):
        self.d, self.path, self.size, self.pos, self.blk = d, path, size, 0, (0, b"")

    def seekable(self): return True
    def readable(self): return True
    def tell(self): return self.pos

    def seek(self, off, whence=0):
        self.pos = off if whence == 0 else (self.pos + off if whence == 1 else self.size + off)
        self.pos = max(0, self.pos)
        return self.pos

    def readinto(self, b):
        n = min(len(b), self.size - self.pos)
        if n <= 0:
            return 0
        s, data = self.blk
        if not (s <= self.pos and self.pos + n <= s + len(data)):
            start = self.pos
            if self.pos + n > self.size - self.TAIL:        # near the end: take the whole tail in one request
                start = max(0, min(self.pos, self.size - self.TAIL))
                end = self.size - 1
            else:
                end = self.pos + max(n, 65536) - 1
            data = self.d.run(self.d.read_range, self.path, start, end, self.size)
            s = start
            self.blk = (s, data)
        b[:n] = data[self.pos - s:self.pos - s + n]
        self.pos += n
        return n


def image_size(b):
    """(width, height) from the first bytes of a PNG / JPEG / WebP / GIF, or (0, 0)."""
    try:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return struct.unpack(">II", b[16:24])
        if b[:6] in (b"GIF87a", b"GIF89a"):
            return struct.unpack("<HH", b[6:10])
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            k = b[12:16]
            if k == b"VP8 ":
                return struct.unpack("<HH", b[26:30])[0] & 0x3FFF, struct.unpack("<HH", b[26:30])[1] & 0x3FFF
            if k == b"VP8L":
                v = struct.unpack("<I", b[21:25])[0]
                return (v & 0x3FFF) + 1, ((v >> 14) & 0x3FFF) + 1
            if k == b"VP8X":
                return int.from_bytes(b[24:27], "little") + 1, int.from_bytes(b[27:30], "little") + 1
        if b[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(b):
                if b[i] != 0xFF:
                    i += 1
                    continue
                m = b[i + 1]
                if m == 0xFF:
                    i += 1
                    continue
                if m in (0xD8, 0x01) or 0xD0 <= m <= 0xD7:
                    i += 2
                    continue
                if m in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", b[i + 5:i + 9])
                    return w, h
                i += 2 + struct.unpack(">H", b[i + 2:i + 4])[0]
    except Exception:
        pass
    return 0, 0


def comic_index(d, path, size):
    z = zipfile.ZipFile(RemoteFile(d, path, size))
    infos = [i for i in z.infolist()
             if not i.is_dir() and i.filename.lower().endswith(IMG_EXT)
             and "__MACOSX" not in i.filename and not os.path.basename(i.filename).startswith(".")]
    infos.sort(key=lambda i: _natkey(i.filename))
    return infos


def comic_member(d, path, size, info):
    """One page: a single ranged request for its local header + data, then inflate and check the CRC."""
    if info.flag_bits & 1:
        raise RuntimeError("encrypted archive")
    o = info.header_offset
    buf = d.read_range(path, o, o + 30 + 4096 + info.compress_size, size)
    if buf[:4] != b"PK\x03\x04":
        raise RuntimeError("bad zip entry header")
    n, m = struct.unpack("<HH", buf[26:30])
    start = 30 + n + m
    if start + info.compress_size > len(buf):
        buf = d.read_range(path, o, o + start + info.compress_size - 1, size)
    raw = buf[start:start + info.compress_size]
    if info.compress_type == 0:
        out = raw
    elif info.compress_type == 8:
        out = zlib.decompress(raw, -15)
    else:
        raise RuntimeError(f"unsupported zip compression {info.compress_type}")
    if zlib.crc32(out) & 0xFFFFFFFF != info.CRC:
        raise RuntimeError("page failed its checksum")
    return out


_relogin_lock, _generation = threading.Lock(), [0]


def _resilient(d, fn, *args):
    """Run a network step from a worker thread: retry flaky reads, re-login once (together) if the session lapsed."""
    err = None
    for attempt in range(4):
        gen = _generation[0]
        try:
            return fn(*args)
        except urllib.error.HTTPError as e:
            err = e
            if e.code in (400, 401, 403):
                with _relogin_lock:
                    if _generation[0] == gen:
                        login(interactive=False)
                        d.__init__()
                        _generation[0] += 1
                continue
            if e.code < 500:
                raise
        except (urllib.error.URLError, OSError, http.client.HTTPException, RuntimeError, zlib.error, zipfile.BadZipFile) as e:
            err = e
        time.sleep(0.6 * (attempt + 1))
    raise err


PINNED = os.path.expanduser("~/.config/unifydrive/pinned.json")
OFFLINE_DIR = os.path.expanduser("~/.local/share/unifydrive/offline")


def _pins():
    try:
        data = json.load(open(PINNED))
    except (OSError, ValueError):
        data = {}
    data.setdefault("comics", {})
    data.setdefault("albums", {})
    data.setdefault("videos", {})
    return data


def _save_pins(data):
    os.makedirs(os.path.dirname(PINNED), exist_ok=True)
    with open(PINNED + ".tmp", "w") as f:
        json.dump(data, f)
    os.replace(PINNED + ".tmp", PINNED)


def _chapter_key(path, size, mtime):
    return hashlib.sha1(f"{path}|{size}|{mtime}".encode()).hexdigest()[:16]


def comic_evict(keep, cap_mb=None):
    """Keep the page cache under a size cap (default 3 GB) by deleting the least recently opened chapters."""
    cap = int((cap_mb or int(os.environ.get("UD_COMIC_CACHE_MB") or settings_get()["comic_cache_mb"])) * 1048576)
    try:
        dirs = [os.path.join(COMIC_CACHE, n) for n in os.listdir(COMIC_CACHE) if n != "covers"]
    except OSError:
        return
    pinned = {k for v in _pins()["comics"].values() for k in v.get("keys", [])}
    rows = []
    for dp in dirs:
        try:
            tot = sum(os.path.getsize(os.path.join(dp, f)) for f in os.listdir(dp))
            rows.append((os.path.getmtime(dp), dp, tot))
        except OSError:
            pass
    total = sum(r[2] for r in rows)
    for mt, dp, tot in sorted(rows):
        if total <= cap:
            break
        if os.path.basename(dp) == keep or os.path.basename(dp) in pinned or time.time() - mt < 1800:
            continue                                  # never the open chapter, a pinned one, or anything touched recently
        shutil.rmtree(dp, ignore_errors=True)
        total -= tot


def _emit(obj):
    try:
        print(json.dumps(obj), flush=True)
    except (BrokenPipeError, OSError):
        os._exit(0)   # the reader went away: stop downloading


def _download_whole(d, path, size, dest):
    """Whole remote file via parallel 4 MiB ranged reads (RAR and PDF cannot be read page by page). Emits progress."""
    from concurrent.futures import ThreadPoolExecutor
    CH = 4 << 20
    with open(dest, "wb") as out:
        out.truncate(size)
    done = [0]
    lock = threading.Lock()

    def part(off):
        data = _resilient(d, d.read_range, path, off, min(off + CH, size) - 1, size)
        with lock:
            with open(dest, "r+b") as out:
                out.seek(off)
                out.write(data)
            done[0] += len(data)
            _emit({"loading": min(99, done[0] * 100 // max(size, 1))})

    with ThreadPoolExecutor(int(os.environ.get("UD_COMIC_WORKERS", "6"))) as ex:
        for f in [ex.submit(part, o) for o in range(0, size, CH)]:
            f.result()


def _finish_pages(dp, names):
    for i, n in enumerate(names):
        f = os.path.join(dp, n)
        with open(f, "rb") as fh:
            w, h = image_size(fh.read(65536))
        _emit({"i": i, "path": f, "w": w, "h": h})
    with open(os.path.join(dp, "index.json"), "w") as fh:
        json.dump(names, fh)
    _emit({"done": True})


def _fetch_rar(d, path, size, dp):
    """CBR/RAR: fetch the archive, unpack it with bsdtar (libarchive reads RAR), keep only the page images."""
    if not shutil.which("bsdtar"):
        _emit({"error": "bsdtar is not installed (needed for .cbr files)"})
        return
    arc, tmp = os.path.join(dp, "archive.part"), os.path.join(dp, "x")
    try:
        _download_whole(d, path, size, arc)
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp)
        r = subprocess.run(["bsdtar", "-xf", arc, "-C", tmp], capture_output=True, timeout=600)
        found = []
        for root, _, files in os.walk(tmp):
            for fn in files:
                if fn.lower().endswith(IMG_EXT):
                    found.append(os.path.join(root, fn))
        if not found:
            _emit({"error": "no pages found in this archive" + (": " + r.stderr.decode()[:120] if r.returncode else "")})
            return
        found.sort(key=lambda f: _natkey(os.path.relpath(f, tmp)))
        names = []
        for k, f in enumerate(found):
            n = f"{k + 1:04d}{os.path.splitext(f)[1].lower()}"
            os.replace(f, os.path.join(dp, n))
            names.append(n)
        _emit({"total": len(names)})
        _finish_pages(dp, names)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            os.remove(arc)
        except OSError:
            pass


def _fetch_pdf(d, path, size, dp):
    """PDF: fetch the file, render each page with pdftoppm (one JPEG per page, emitted as soon as it exists)."""
    if not shutil.which("pdftoppm") or not shutil.which("pdfinfo"):
        _emit({"error": "poppler is not installed (needed for PDF files)"})
        return
    pdf = os.path.join(dp, "source.pdf")
    try:
        _download_whole(d, path, size, pdf)
        info = subprocess.run(["pdfinfo", pdf], capture_output=True, text=True, timeout=60).stdout
        m = re.search(r"^Pages:\s+(\d+)", info, re.M)
        if not m:
            _emit({"error": "could not read this PDF"})
            return
        total = int(m.group(1))
        _emit({"total": total})
        names = []
        for n in range(1, total + 1):
            stem = os.path.join(dp, f"{n:04d}")
            r = subprocess.run(["pdftoppm", "-jpeg", "-jpegopt", "quality=88", "-r", "110", "-f", str(n), "-l", str(n),
                                "-singlefile", pdf, stem], capture_output=True, timeout=120)
            f = stem + ".jpg"
            if r.returncode != 0 or not os.path.exists(f):
                _emit({"i": n - 1, "path": "", "w": 0, "h": 0, "error": "could not render this page"})
                names = None
                continue
            with open(f, "rb") as fh:
                w, h = image_size(fh.read(65536))
            _emit({"i": n - 1, "path": f, "w": w, "h": h})
            if names is not None:
                names.append(os.path.basename(f))
        if names is not None:
            with open(os.path.join(dp, "index.json"), "w") as fh:
                json.dump(names, fh)
        _emit({"done": True, "complete": names is not None})
    finally:
        try:
            os.remove(pdf)
        except OSError:
            pass


def comic_fetch(d, path, size, mtime):
    """Stream one chapter's pages into the cache. Prints JSON lines: {"total"}, then {"i","path","w","h"} per page in
    order as each lands, then {"done"}. Re-opening a chapter that is already cached prints it all at once."""
    from concurrent.futures import ThreadPoolExecutor
    key = hashlib.sha1(f"{path}|{size}|{mtime}".encode()).hexdigest()[:16]
    dp = os.path.join(COMIC_CACHE, key)
    os.makedirs(dp, exist_ok=True)
    os.utime(dp)
    comic_evict(key)
    idx = os.path.join(dp, "index.json")
    names = None
    try:
        names = json.load(open(idx))
        if not all(os.path.exists(os.path.join(dp, n)) for n in names):
            names = None
    except (OSError, ValueError):
        names = None
    if names is not None:
        _emit({"total": len(names), "key": key})
        for i, n in enumerate(names):
            f = os.path.join(dp, n)
            with open(f, "rb") as fh:
                w, h = image_size(fh.read(65536))
            _emit({"i": i, "path": f, "w": w, "h": h})
        _emit({"done": True})
        return
    ext = os.path.splitext(path)[1].lower()
    if ext in RAR_EXT:
        _fetch_rar(d, path, size, dp)
        return
    if ext == ".pdf":
        _fetch_pdf(d, path, size, dp)
        return
    infos = d.run(comic_index, d, path, size)
    if not infos:
        _emit({"error": "no pages found in this file"})
        return
    names = [f"{k + 1:04d}{os.path.splitext(inf.filename)[1].lower()}" for k, inf in enumerate(infos)]
    _emit({"total": len(names), "key": key})

    def one(k):
        f = os.path.join(dp, names[k])
        if not os.path.exists(f):
            data = _resilient(d, comic_member, d, path, size, infos[k])
            tmpf = f"{f}.part{os.getpid()}"                 # unique: the reader's own fetch and a prefetch can overlap
            with open(tmpf, "wb") as fh:
                fh.write(data)
            os.replace(tmpf, f)
        with open(f, "rb") as fh:
            w, h = image_size(fh.read(65536))
        return f, w, h

    with ThreadPoolExecutor(int(os.environ.get("UD_COMIC_WORKERS", "6"))) as ex:
        futs = [ex.submit(one, k) for k in range(len(names))]
        ok = True
        for k, fu in enumerate(futs):
            try:
                f, w, h = fu.result()
                _emit({"i": k, "path": f, "w": w, "h": h})
            except Exception as e:
                ok = False
                _emit({"i": k, "path": "", "w": 0, "h": 0, "error": str(e)[:160]})
    if ok:
        with open(idx, "w") as fh:
            json.dump(names, fh)
    _emit({"done": True, "complete": ok})


def _listing_cache(kind, key, fn):
    """Run fn(); remember the result on disk. If the NAS is unreachable, answer from the last saved copy."""
    f = os.path.join(COMIC_CACHE, f"{kind}-{hashlib.sha1(key.encode()).hexdigest()[:16]}.json")
    try:
        out = fn()
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:
        try:
            return json.load(open(f))
        except (OSError, ValueError):
            raise e
    os.makedirs(COMIC_CACHE, exist_ok=True)
    with open(f, "w") as fh:
        json.dump(out, fh)
    return out


def comic_chapters(d, series):
    return _listing_cache("chapters", series, lambda: _comic_chapters(d, series))


def _comic_chapters(d, series):
    out = []
    for e in d.ls(series):
        base, ext = os.path.splitext(e["name"])
        if e["dir"] or ext.lower() not in COMIC_EXT:
            continue
        nums = re.findall(r"\d+(?:\.\d+)?", base)
        num = float(nums[-1]) if nums else None
        label = ("Chapter " + (str(int(num)) if num == int(num) else str(num))) if num is not None else base
        group = re.sub(r"[_\s.\-]*(ch(apter)?)?[._\s]*\d+(\.\d+)?$", "", base, flags=re.I).strip(" _-.")
        out.append({"name": e["name"], "path": series.rstrip("/") + "/" + e["name"], "size": e["size"], "mtime": e["mtime"],
                    "num": num, "label": label, "group": group if num is not None else ""})
    out.sort(key=lambda c: (c["num"] is None, c["num"] if c["num"] is not None else 0, _natkey(c["name"])))
    return out


def comic_series(d, root):
    """Print one JSON line per series folder, then a second line for each cover that had to be fetched."""
    from concurrent.futures import ThreadPoolExecutor
    cdir = os.path.join(COMIC_CACHE, "covers")
    os.makedirs(cdir, exist_ok=True)
    folders = sorted(_listing_cache("folders", root, lambda: [e["name"] for e in d.ls(root) if e["dir"]]), key=_natkey)

    def cached(path):
        k = hashlib.sha1(path.encode()).hexdigest()[:16]
        for n in os.listdir(cdir):
            if n.startswith(k + "."):
                return os.path.join(cdir, n)
        return None

    def info(name):
        path = root.rstrip("/") + "/" + name
        chs = _resilient(d, comic_chapters, d, path)
        return {"name": name, "path": path, "chapters": len(chs), "cover": cached(path), "_first": chs[0] if chs else None}

    with ThreadPoolExecutor(4) as ex:
        rows = list(ex.map(info, folders))
    rows = [r for r in rows if r["chapters"] > 0]
    for r in rows:
        _emit({k: v for k, v in r.items() if k != "_first"})

    def cover(r):
        ch = r["_first"]
        infos = _resilient(d, comic_index, d, ch["path"], ch["size"])
        if not infos:
            return None
        data = _resilient(d, comic_member, d, ch["path"], ch["size"], infos[0])
        f = os.path.join(cdir, hashlib.sha1(r["path"].encode()).hexdigest()[:16] + os.path.splitext(infos[0].filename)[1].lower())
        with open(f + ".part", "wb") as fh:
            fh.write(data)
        os.replace(f + ".part", f)
        return f

    todo = [r for r in rows if not r["cover"]]
    with ThreadPoolExecutor(3) as ex:
        for r, f in zip(todo, ex.map(lambda r: _try(cover, r), todo)):
            if f:
                _emit({"name": r["name"], "path": r["path"], "chapters": r["chapters"], "cover": f})
    _emit({"done": True})


def _try(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return None


def comic_progress(args):
    """comic-progress            -> all saved positions
       comic-progress <series> <chapter_path> <page>   -> save one (local file, no network)"""
    try:
        data = json.load(open(COMIC_PROGRESS))
    except (OSError, ValueError):
        data = {}
    if len(args) >= 3:
        data[args[0]] = {"chapter": args[1], "page": int(args[2]), "ts": int(time.time())}
        os.makedirs(os.path.dirname(COMIC_PROGRESS), exist_ok=True)
        with open(COMIC_PROGRESS + ".tmp", "w") as fh:
            json.dump(data, fh)
        os.replace(COMIC_PROGRESS + ".tmp", COMIC_PROGRESS)
    return data


COMIC_PREFS = os.path.expanduser("~/.config/unifydrive/reader.json")


def comic_pref(args):
    """comic-pref -> saved reader preferences; comic-pref key value [key value...] saves them (local file)."""
    try:
        prefs = json.load(open(COMIC_PREFS))
    except (OSError, ValueError):
        prefs = {}
    if args:
        for k, v in zip(args[::2], args[1::2]):
            prefs[k] = v
        os.makedirs(os.path.dirname(COMIC_PREFS), exist_ok=True)
        with open(COMIC_PREFS + ".tmp", "w") as f:
            json.dump(prefs, f)
        os.replace(COMIC_PREFS + ".tmp", COMIC_PREFS)
    return prefs


def comic_pin(d, series, count=10, anchor=""):
    """Keep the next `count` chapters (from `anchor`, default the first unread/first) readable offline."""
    chs = comic_chapters(d, series)
    start = 0
    for i, c in enumerate(chs):
        if c["path"] == anchor:
            start = i
    pins = _pins()
    entry = pins["comics"].setdefault(series, {"keys": [], "paths": []})
    todo = chs[start:start + count]
    for n, c in enumerate(todo):
        key = _chapter_key(c["path"], c["size"], c["mtime"])
        if key not in entry["keys"]:
            entry["keys"].append(key)
            entry["paths"].append(c["path"])
        _save_pins(pins)
        r = subprocess.run([sys.executable, "-I", os.path.abspath(__file__), "comic-fetch", c["path"], str(c["size"]), str(c["mtime"])],
                           capture_output=True, text=True)
        ok = '"complete": false' not in r.stdout and '"error"' not in r.stdout.split('"total"')[0]
        print(json.dumps({"pin": n + 1, "of": len(todo), "chapter": c["name"], "ok": ok}), flush=True)
    return {"pinned": len(entry["keys"])}


def comic_unpin(series):
    pins = _pins()
    entry = pins["comics"].pop(series, None)
    _save_pins(pins)
    for k in (entry or {}).get("keys", []):
        shutil.rmtree(os.path.join(COMIC_CACHE, k), ignore_errors=True)
    return {"unpinned": len((entry or {}).get("keys", []))}


# --------------------------------------------------------------------------
# Albums saved for offline listening
# --------------------------------------------------------------------------
def album_dir(album_id):
    return os.path.join(OFFLINE_DIR, "music", re.sub(r"[^A-Za-z0-9_.-]", "_", album_id))


def album_pin(d, album_id, name="", cover=""):
    if not album_id:
        raise RuntimeError("no album id")
    tracks = songs(d, album_id)
    if not tracks:
        raise RuntimeError("album has no songs")
    dd = album_dir(album_id)
    os.makedirs(dd, mode=0o700, exist_ok=True)
    meta = {"id": album_id, "name": name or tracks[0]["album"], "cover": cover or tracks[0]["cover"], "tracks": [], "complete": False}
    try:
        cf = cover_file(d, meta["cover"]) if meta["cover"] else None
        if cf:
            shutil.copy(cf, os.path.join(dd, "cover.jpg"))
    except Exception:
        pass
    for n, t in enumerate(tracks):
        fn = f"{n + 1:02d} - " + re.sub(r"[\\/:*?\"<>|]", "_", t["title"] or "track")[:80] + os.path.splitext(t["path"])[1].lower()
        if not os.path.exists(os.path.join(dd, fn)):
            r = d.run(d.get, t["path"], dd)
            os.replace(r["path"], os.path.join(dd, fn))
        meta["tracks"].append({**{k: t[k] for k in ("title", "artist", "album", "cover", "ms")}, "file": fn})
        print(json.dumps({"pin": n + 1, "of": len(tracks), "title": t["title"]}), flush=True)
    meta["complete"] = True
    with open(os.path.join(dd, "meta.json"), "w") as f:
        json.dump(meta, f)
    pins = _pins()
    pins["albums"][album_id] = {"name": meta["name"], "count": len(tracks), "ts": int(time.time())}
    _save_pins(pins)
    return {"pinned": len(tracks)}


def album_unpin(album_id):
    shutil.rmtree(album_dir(album_id), ignore_errors=True)
    pins = _pins()
    pins["albums"].pop(album_id, None)
    _save_pins(pins)
    return {"unpinned": True}


def album_meta(album_id):
    try:
        m = json.load(open(os.path.join(album_dir(album_id), "meta.json")))
        return m if m.get("complete") else None
    except (OSError, ValueError):
        return None


def play_album_offline(album_id, start=0):
    """Play a saved album straight from disk (no network). The cover.jpg in the folder is what MPRIS shows."""
    m = album_meta(album_id)
    dd = album_dir(album_id)
    tracks = m["tracks"]
    recent_set("music", id=album_id, name=m["name"], cover=m["cover"])
    player("stop")
    try:
        os.remove(SOCK)
    except OSError:
        pass
    fd = os.open(QUEUE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump([{"title": x["title"], "artist": x["artist"], "album": x["album"], "cover": x["cover"]} for x in tracks[start:]], f)
    cmd = ["mpv", "--no-video", "--idle=no", "--force-window=no", f"--input-ipc-server={SOCK}", "--title=UnifyDrive Music",
           *[os.path.join(dd, x["file"]) for x in tracks[start:]]]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    return {"queued": len(tracks) - start, "first": tracks[start]["title"], "offline": True}


def offline_list():
    pins = _pins()
    return {"albums": {k: v for k, v in pins["albums"].items() if album_meta(k)},
            "comics": {k: {"chapters": len(v.get("keys", [])), "paths": v.get("paths", [])} for k, v in pins["comics"].items()},
            "videos": {k: v for k, v in pins["videos"].items() if video_local(k)}}


def comic_read(series, chapter=""):
    """Open the reader window (its own Quickshell instance, so it can't stall the bar). Replaces a reader already open."""
    try:
        old = int(open(READER_PID).read())
        os.kill(old, 15)
        time.sleep(0.3)
    except (OSError, ValueError):
        pass
    env = dict(os.environ, UD_HELPER=os.path.abspath(__file__), UD_SERIES=series, UD_CHAPTER=chapter,
               UD_PREFETCH=str(settings_get()["comic_prefetch"]))
    reader = os.path.join(os.path.dirname(os.path.abspath(__file__)), "reader")
    log = open(READER_LOG, "w")
    pr = subprocess.Popen(["quickshell", "-p", reader], env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    with open(READER_PID, "w") as fh:
        fh.write(str(pr.pid))
    return {"started": True, "pid": pr.pid}


# --------------------------------------------------------------------------
# Video: original file through a local range proxy
# --------------------------------------------------------------------------
# The relay answers open-ended / to-EOF Range requests with `200` + Content-Range instead of `206`, which mpv's HTTP
# layer reads as "range ignored" (seeks break), and one connection only moves ~1 MB/s. This tiny localhost server
# gives mpv proper 206 responses and fetches ahead over several connections.
def file_size(d, path):
    r = d.post("/v2/file/info", path=path)
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"file info refused: {r.get('code')}")
    return int(r["data"]["size"])


def range_server(d, path, size, name):
    """Start the proxy on 127.0.0.1 in a thread; returns the URL mpv should open."""
    import collections, http.server, secrets, socketserver
    from concurrent.futures import ThreadPoolExecutor
    token = secrets.token_urlsafe(12)
    pool = ThreadPoolExecutor(int(os.environ.get("UD_VIDEO_WORKERS", "6")))
    CH, AHEAD = 1 << 20, int(os.environ.get("UD_VIDEO_AHEAD", "8"))

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _serve(self, body):
            if not self.path.startswith(f"/{token}/"):
                self.send_error(404)
                return
            a, b, ranged = 0, size - 1, False
            m = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
            if m:
                ranged = True
                lo, hi = m.groups()
                if lo == "":
                    a = max(0, size - int(hi or 0))
                else:
                    a = int(lo)
                    b = min(int(hi), size - 1) if hi else size - 1
            if a >= size or a > b:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", _ctype(name))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(b - a + 1))
            if ranged:
                self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            self.end_headers()
            if not body:
                return
            futs, nxt = collections.deque(), a

            def submit():
                nonlocal nxt
                e = min(nxt + CH - 1, b)
                futs.append(pool.submit(_resilient, d, d.read_range, path, nxt, e, size))
                nxt = e + 1

            try:
                while len(futs) < AHEAD and nxt <= b:
                    submit()
                while futs:
                    self.wfile.write(futs.popleft().result())
                    if nxt <= b:
                        submit()
            except (BrokenPipeError, ConnectionResetError):
                pass            # mpv seeked or quit
            except Exception:
                self.close_connection = True   # upstream died mid-body: drop the connection, mpv reconnects
            finally:
                for f in futs:
                    f.cancel()

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):
            self._serve(False)

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{srv.server_address[1]}/{token}/{up.quote(name)}"


def _ctype(name):
    """The file's real media type: mpv decides from it (among other things) whether a stream is a playlist."""
    t = mimetypes.guess_type(name)[0] or ""
    return t if t.startswith(("audio/", "video/")) else "application/octet-stream"


# --------------------------------------------------------------------------
# Music: the original file through a local range proxy (a FLAC stays a FLAC)
# --------------------------------------------------------------------------
# Audio used to play only the server's HLS transcode (lossy AAC, ~350 kbps) whatever the file was. The original is
# served the way video's is: the relay answers open-ended Range requests with 200, which breaks seeking, so a
# localhost daemon turns them into proper 206s and reads ahead over several connections. One daemon serves every
# track of every album and exits after UD_AUDIO_PROXY_IDLE seconds (default 30 min) with nothing playing.
# UD_AUDIO_TRANSCODE=1 brings the old behaviour back (the HLS stream, also through this daemon: mpv never holds the
# NAS cookie), so if the daemon cannot start, music cannot play.
AUDIO_PROXY_STATE = os.path.join(_runtime(), "unifydrive-audio-proxy.json")


def audio_proxy():
    """The daemon (`udrive.py audio-proxy`): prints one JSON line {port, token}, then serves until idle."""
    import collections, http.server, secrets, socketserver
    from concurrent.futures import ThreadPoolExecutor
    d = Drive()
    token = secrets.token_urlsafe(12)
    pool = ThreadPoolExecutor(int(os.environ.get("UD_AUDIO_WORKERS", "16")))
    CH, AHEAD = 1 << 18, int(os.environ.get("UD_AUDIO_AHEAD", "32"))
    idle = int(os.environ.get("UD_AUDIO_PROXY_IDLE", "1800"))
    sizes, lock, st = {}, threading.Lock(), {"busy": 0, "last": time.time()}

    def size_of(path):
        with lock:
            if path in sizes:
                return sizes[path]
        n = _resilient(d, file_size, d, path)
        with lock:
            sizes[path] = n
        return n

    class H(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *a):
            pass

        def _serve(self, body):
            with lock:
                st["busy"] += 1
            try:
                self._serve_one(body)
            finally:
                with lock:
                    st["busy"] -= 1
                    st["last"] = time.time()

        def _serve_one(self, body):
            if up.urlsplit(self.path).path.startswith(f"/{token}~nas/"):
                return self._nas(body)
            m = re.match(r"^/%s(/.+)$" % re.escape(token), up.urlsplit(self.path).path)
            remote = up.unquote(m.group(1)) if m else ""
            if not remote.startswith("/") or ".." in remote.split("/") or remote.lower().rsplit(".", 1)[-1] not in AUDIO_EXT:
                self.send_error(404)
                return
            try:
                size = size_of(remote)
            except Exception:
                self.send_error(502)
                return
            a, b, ranged = 0, size - 1, False
            mr = re.match(r"bytes=(\d*)-(\d*)$", (self.headers.get("Range") or "").strip())
            if mr:
                ranged = True
                lo, hi = mr.groups()
                if lo == "":
                    a = max(0, size - int(hi or 0))
                else:
                    a = int(lo)
                    b = min(int(hi), size - 1) if hi else size - 1
            if a >= size or a > b:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(206 if ranged else 200)
            self.send_header("Content-Type", _ctype(remote))
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(b - a + 1))
            if ranged:
                self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            self.end_headers()
            if not body:
                return
            futs, nxt = collections.deque(), a

            def submit():
                nonlocal nxt
                e = min(nxt + CH - 1, b)
                futs.append(pool.submit(_resilient, d, d.read_range, remote, nxt, e, size))
                nxt = e + 1

            try:
                while len(futs) < AHEAD and nxt <= b:
                    submit()
                while futs:
                    self.wfile.write(futs.popleft().result())
                    if nxt <= b:
                        submit()
            except (BrokenPipeError, ConnectionResetError):
                pass            # mpv seeked or quit
            except Exception:
                self.close_connection = True   # upstream died mid-body: drop the connection, mpv reconnects
            finally:
                for f in futs:
                    f.cancel()

        def _nas(self, body):
            """The server's HLS transcode (UD_*_TRANSCODE=1, or the fallback): forward to the NAS with the cookie.
            Only GETs under /transcode/; playlists are rewritten so every URI in them comes back here."""
            u = up.urlsplit(self.path)
            rpath = u.path[len(f"/{token}~nas"):]
            if not rpath.startswith(HLS_PREFIX) or ".." in up.unquote(rpath).split("/"):
                self.send_error(404)
                return
            hdrs = {"Range": self.headers["Range"]} if self.headers.get("Range") else {}
            try:
                r = _resilient(d, d._req, d.host + rpath + (f"?{u.query}" if u.query else ""), None, hdrs)
            except urllib.error.HTTPError as e:
                self.send_error(e.code if 400 <= e.code < 600 else 502)
                return
            except Exception:
                self.send_error(502)
                return
            with r:
                ctype = r.headers.get("Content-Type") or "application/octet-stream"
                if "mpegurl" in ctype.lower() or rpath.lower().endswith(".m3u8"):
                    data = _hls_rewrite(r.read().decode("utf8", "replace"), d.host, rpath, f"/{token}~nas").encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if body:
                        self.wfile.write(data)
                    return
                self.send_response(r.status)
                for k in ("Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"):
                    if r.headers.get(k):
                        self.send_header(k, r.headers[k])
                if not r.headers.get("Content-Length"):
                    self.close_connection = True
                self.end_headers()
                if not body:
                    return
                try:
                    while True:
                        b = r.read(1 << 16)
                        if not b:
                            break
                        self.wfile.write(b)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception:
                    self.close_connection = True

        def do_GET(self):
            self._serve(True)

        def do_HEAD(self):
            self._serve(False)

    class S(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True

    def reaper():
        while True:
            time.sleep(min(30, max(1, idle // 4)))
            with lock:
                quiet = st["busy"] == 0 and time.time() - st["last"] > idle
            if quiet:
                try:
                    if json.load(open(AUDIO_PROXY_STATE)).get("pid") == os.getpid():
                        os.remove(AUDIO_PROXY_STATE)
                except (OSError, ValueError):
                    pass
                os._exit(0)

    srv = S(("127.0.0.1", 0), H)
    threading.Thread(target=reaper, daemon=True).start()
    print(json.dumps({"port": srv.server_address[1], "token": token}), flush=True)
    srv.serve_forever()


def _audio_proxy_ensure():
    """The running audio proxy's {port, token}, starting it if needed; None if it cannot be started."""
    import fcntl, select, socket
    with open(AUDIO_PROXY_STATE + ".lock", "a") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)          # several tracks resolve at once: only one starts the daemon
        try:
            st = json.load(open(AUDIO_PROXY_STATE))
            socket.create_connection(("127.0.0.1", int(st["port"])), timeout=1).close()
            return st
        except Exception:
            pass
        p = subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "audio-proxy"], stdout=subprocess.PIPE,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        if not select.select([p.stdout], [], [], 12)[0]:
            p.kill()
            return None
        st = json.loads(p.stdout.readline())
        st["pid"] = p.pid
        fd = os.open(AUDIO_PROXY_STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            json.dump(st, f)
        return st


HLS_PREFIX = "/transcode/"


def _hls_rewrite(text, host, base, prefix):
    """Point every URI of an HLS playlist (lines and URI="..." attributes) at `prefix` + its path on the NAS. A URI
    that is not on the NAS is dropped: nothing in a playlist can send mpv anywhere but back to this proxy."""
    def fix(u):
        a = up.urljoin(host + base, u.strip())
        return prefix + a[len(host):] if a.startswith(host + "/") else None

    out = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("#"):
            bad = []

            def attr(m):
                v = fix(m.group(1))
                if v is None:
                    bad.append(1)
                return f'URI="{v}"'
            line = re.sub(r'URI="([^"]*)"', attr, line)
            if bad or re.search(r'URI=(?!")', line, re.I):
                continue
        elif s:
            line = fix(s)
            if line is None:
                continue
        out.append(line)
    return "\n".join(out) + "\n"


def hls_proxy_url(uri):
    """The audio proxy's URL for a transcode stream the NAS returned (a path under /transcode/), or None."""
    if not uri.startswith(HLS_PREFIX):
        raise RuntimeError(f"unexpected transcode stream location: {uri[:60]}")
    try:
        st = _audio_proxy_ensure()
    except Exception:
        return None
    return f"http://127.0.0.1:{st['port']}/{st['token']}~nas{up.quote(uri, safe='/:?&=%+~,')}" if st else None


def audio_direct_url(path):
    """mpv's URL for the original audio file, or None to fall back to the HLS transcode."""
    try:
        st = _audio_proxy_ensure()
        return f"http://127.0.0.1:{st['port']}/{st['token']}{up.quote(path, safe='/')}" if st else None
    except Exception:
        return None


VSOCK = os.path.join(_runtime(), "unifydrive-video.sock")
VIDEO_PROGRESS = os.path.expanduser("~/.config/unifydrive/video.json")
SUB_EXT = (".srt", ".ass", ".ssa", ".vtt", ".sub")
SUB_DIR = os.path.join(_runtime(), "unifydrive-subs")


def video_progress(paths=()):
    """{remote path: {pos, dur, ts, done}} for videos that were started (all of them, or just `paths`)."""
    try:
        data = json.load(open(VIDEO_PROGRESS))
    except (OSError, ValueError):
        data = {}
    return {p: data[p] for p in paths if p in data} if paths else data


def _save_video_progress(path, pos, dur):
    data = video_progress()
    done = bool(dur) and (dur - pos < 60 or pos / dur > 0.95)
    data[path] = {"pos": 0 if done else round(pos), "dur": round(dur or 0), "ts": int(time.time()), "done": done}
    if len(data) > 2000:  # keep the newest
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("ts", 0))[-1500:])
    os.makedirs(os.path.dirname(VIDEO_PROGRESS), exist_ok=True)
    tmp = VIDEO_PROGRESS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f)
    os.replace(tmp, VIDEO_PROGRESS)


def _sibling_subs(d, path):
    """Subtitle files stored next to a video (same name stem, also in Subs/ folders) on the NAS."""
    folder, name = path.rsplit("/", 1)
    stem = os.path.splitext(name)[0].lower()
    found = []
    for sub in ("", "Subs", "Subtitles", "subs", "subtitles", "Sub"):
        try:
            items = d.ls(folder + ("/" + sub if sub else ""))
        except Exception:
            continue
        for i in items:
            n = i["name"].lower()
            if not i["dir"] and n.endswith(SUB_EXT) and i["size"] < 8 << 20 and (n.startswith(stem) or sub):
                found.append(f"{folder}/{sub}/{i['name']}" if sub else f"{folder}/{i['name']}")
    return found[:12]


def _load_subs(d, path, sock):
    """Fetch sibling subtitle files and hand them to the running mpv (flag `auto`: never overrides the embedded pick)."""
    try:
        subs = d.run(_sibling_subs, d, path)
        if not subs:
            return
        tmp = os.path.join(SUB_DIR, hashlib.sha1(path.encode()).hexdigest()[:12])
        os.makedirs(tmp, mode=0o700, exist_ok=True)
        for sp in subs:
            r = d.run(d.get, sp, tmp)
            for _ in range(20):
                try:
                    _ipc(["sub-add", r["path"], "auto"], sock=sock)
                    break
                except (OSError, RuntimeError):
                    time.sleep(0.5)
    except Exception:
        pass


def _watch_video(path, sock, proc):
    """Save the play position every few seconds so the next play resumes there. Returns True when the video ran to its end
    (mpv is kept open at the last frame, so the end is seen exactly and the window is then closed here)."""
    pos = dur = None
    tick = 0
    while proc.poll() is None:
        time.sleep(1)
        tick += 1
        try:
            if _ipc(["get_property", "eof-reached"], 2, sock) is True:
                if dur:
                    _save_video_progress(path, dur, dur)
                try:
                    _ipc(["quit"], 1, sock)
                except (OSError, RuntimeError):
                    pass
                proc.wait(timeout=10)
                return True
        except (OSError, RuntimeError):
            pass
        if tick % 3:
            continue
        try:
            p = _ipc(["get_property", "time-pos"], 2, sock)
            dd = _ipc(["get_property", "duration"], 2, sock)
        except (OSError, RuntimeError):
            continue
        if p is not None and dd:
            pos, dur = float(p), float(dd)
            _save_video_progress(path, pos, dur)
    return False


def _autoplay_next(me, ended):
    """After a video: start the next queued one if this supervisor still owns the queue (a newer play replaces it)."""
    q = vqueue_read()
    if not q or q.get("owner") != me or not q.get("items"):
        return
    immediate = bool(q.get("immediate"))
    if not (immediate or (ended and settings_get()["autoplay"])):
        return
    nxt, rest = q["items"][0], q["items"][1:]
    if not immediate:
        delay = max(0, settings_get()["autoplay_delay"])
        notify("Up next: " + os.path.splitext(os.path.basename(nxt))[0], f"Starting in {delay} s. Press the stop button in the panel to cancel.", timeout=delay * 1000)
        end = time.time() + delay
        while time.time() < end:
            time.sleep(0.5)
            q2 = vqueue_read()
            if not q2 or q2.get("owner") != me or q2.get("items", [None])[0] != nxt:
                return
    subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "play", nxt, *(["--queue=" + json.dumps(rest)] if rest else [])],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)


def vplay(d, path, extra):
    """Supervisor (detached): runs the range proxy for as long as mpv lives. Prints one JSON line once mpv is up."""
    extra = list(extra)
    from_start = "--from-start" in extra
    queue = []
    for x in extra:
        if x.startswith("--queue="):
            try:
                queue = [p for p in json.loads(x[8:]) if isinstance(p, str)]
            except ValueError:
                pass
    extra = [x for x in extra if x != "--from-start" and not x.startswith("--queue=")]
    local = video_local(path)
    if local:
        url = None
    else:
        size = d.run(file_size, d, path)
        url = range_server(d, path, size, os.path.basename(path))
    me = os.getpid()
    info, cmd = play(None, path, extra, url) if local else d.run(play, d, path, extra, url, f"video-{me}")
    try:                                        # one video player at a time
        _ipc(["quit"], 1, VSOCK)
        time.sleep(0.4)
    except (OSError, RuntimeError):
        pass
    if os.path.exists(VSOCK):
        os.unlink(VSOCK)
    vqueue_write(me, queue)                     # a manual play without a queue also clears the old one
    cmd.insert(1, f"--input-ipc-server={VSOCK}")
    cmd = ["--keep-open=yes" if x == "--keep-open=no" else x for x in cmd]   # stay on the last frame so the end is seen over IPC
    saved = video_progress([path]).get(path)
    if saved and not from_start and saved.get("pos", 0) > 15:
        cmd.insert(1, f"--start={saved['pos']}")
        info["resumed"] = saved["pos"]
    pr = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    info.pop("url", None)
    info["queued"] = len(queue)
    info["offline"] = bool(local)
    print(json.dumps(info), flush=True)
    sys.stdout.close()
    if d and not local:
        threading.Thread(target=_load_subs, args=(d, path, VSOCK), daemon=True).start()
    ended = _watch_video(path, VSOCK, pr)
    if not local:
        try:
            os.remove(os.path.join(_runtime(), "unifydrive-play", f"video-{me}.m3u"))
        except OSError:
            pass
    shutil.rmtree(os.path.join(SUB_DIR, hashlib.sha1(path.encode()).hexdigest()[:12]), ignore_errors=True)
    _autoplay_next(me, ended)


def vplayer(action):
    """Control the running video mpv (subtitle/audio track choice, seek, pause) over its IPC socket."""
    try:
        if action == "pause":
            _ipc(["cycle", "pause"], sock=VSOCK)
        elif action == "stop":
            vqueue_clear()                  # stopping by hand also cancels autoplay
            _ipc(["quit"], sock=VSOCK)
            return {"playing": False}
        elif action.startswith("seek:"):
            _ipc(["seek", action.split(":", 1)[1], "absolute"], sock=VSOCK)
        elif action.startswith("skip:"):
            _ipc(["seek", action.split(":", 1)[1], "relative"], sock=VSOCK)
        elif action.startswith("sid:"):
            _ipc(["set_property", "sid", action.split(":", 1)[1]], sock=VSOCK)
        elif action.startswith("aid:"):
            _ipc(["set_property", "aid", action.split(":", 1)[1]], sock=VSOCK)
        elif action.startswith("volume:"):
            _ipc(["set_property", "volume", max(0, min(130, int(action.split(":", 1)[1])))], sock=VSOCK)
        elif action == "next":
            q = vqueue_read()
            if q and q.get("items"):
                vqueue_write(q["owner"], q["items"], True)
                _ipc(["quit"], sock=VSOCK)
                return {"playing": False, "skipping": True}
        elif action == "cancelnext":
            vqueue_clear()
        def g(k):
            try:
                return _ipc(["get_property", k], sock=VSOCK)
            except RuntimeError:        # "property unavailable" while the file is still loading
                return None
        tracks = []
        for t in g("track-list") or []:
            if t.get("type") in ("audio", "sub"):
                tracks.append({"id": t["id"], "type": t["type"], "lang": t.get("lang", ""), "title": t.get("title", ""),
                               "codec": t.get("codec", ""), "selected": bool(t.get("selected")),
                               "external": bool(t.get("external")), "forced": bool(t.get("forced"))})
        q = vqueue_read() or {}
        nxt = (q.get("items") or [None])[0]
        return {"playing": True, "paused": g("pause"), "volume": g("volume"), "title": g("media-title"), "pos": g("time-pos"),
                "duration": g("duration"), "tracks": tracks, "queued": len(q.get("items") or []),
                "next": os.path.splitext(os.path.basename(nxt))[0] if nxt else ""}
    except (OSError, RuntimeError):
        return {"playing": False}


# --------------------------------------------------------------------------
# Settings (shared by the panel, the CLI and the background agent)
# --------------------------------------------------------------------------
SETTINGS = os.path.expanduser("~/.config/unifydrive/settings.json")
SETTING_DEFAULTS = {
    "autoplay": True,              # start the next episode when one ends
    "autoplay_delay": 8,           # seconds of warning before it does
    "alerts": True,                # desktop notifications about NAS health
    "alert_free_pct": 10,          # warn when free space drops below this many percent
    "alert_temp": 55,              # warn when a disk gets hotter than this (°C)
    "notify_sync": True,           # notify when background uploads finish
    "capture_remote": "/nvme11/my/data/Pictures/Screenshots",
    "capture_link": False,         # quick capture also makes a share link and copies it
    "photo_local": "~/Pictures",   # folder the live photo/screenshot upload watches
    "photo_remote": "/nvme11/my/data/Pictures/Desktop",
    "share_days": 7,
    "share_password": True,
    "comic_prefetch": 2,           # chapters to download ahead while reading (0 = off)
    "comic_cache_mb": 3072,
}


def settings_get():
    try:
        data = json.load(open(SETTINGS))
    except (OSError, ValueError):
        data = {}
    return {**SETTING_DEFAULTS, **{k: v for k, v in data.items() if k in SETTING_DEFAULTS}}


def settings_set(key, value):
    if key not in SETTING_DEFAULTS:
        raise RuntimeError(f"unknown setting: {key}")
    dflt = SETTING_DEFAULTS[key]
    if isinstance(dflt, bool):
        value = str(value).lower() in ("1", "true", "on", "yes")
    elif isinstance(dflt, int):
        value = int(float(value))
    else:
        value = str(value)
    try:
        data = json.load(open(SETTINGS))
    except (OSError, ValueError):
        data = {}
    data[key] = value
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS + ".tmp", "w") as f:
        json.dump(data, f, indent=1)
    os.replace(SETTINGS + ".tmp", SETTINGS)
    return settings_get()


def notify(title, body="", urgent=False, timeout=6000):
    try:
        subprocess.run(["notify-send", "-a", "UnifyDrive", "-t", str(timeout), *(["-u", "critical"] if urgent else []), title, body],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass


# --------------------------------------------------------------------------
# Transfer queue (the NAS's own copy / move / delete / extract jobs)
# --------------------------------------------------------------------------
def tasks_list(d, num=30):
    r = d.post("/v2/file/tasks", start=0, num=num)
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"tasks refused: {r.get('code')}")
    out = []
    for t in (r.get("data") or {}).get("list") or []:
        def n(k):
            try:
                return int(float(t.get(k) or 0))
            except ValueError:
                return 0
        out.append({"id": str(t.get("id")), "op": t.get("opt", ""), "state": t.get("state", ""), "progress": n("progress"),
                    "speed": n("speed"), "src": t.get("src") or [], "to": t.get("to") or "", "total": n("total_size"),
                    "done": n("done_size"), "files": n("total_num"), "failed": n("fail_num"), "created": n("created_at"),
                    "updated": n("updated_at")})
    return {"tasks": out, "active": sum(1 for x in out if x["state"] not in ("done", "fail", "failed", "cancel", "cancelled"))}


def task_act(d, action, ids):
    ep = {"pause": "/v2/file/task/pause", "continue": "/v2/file/task/continue", "del": "/v2/file/taskdel"}.get(action)
    if not ep:
        raise RuntimeError("action must be pause, continue or del")
    for i in ids:                                        # the web UI sends one task id per call
        r = d.post(ep, id=i, **({"ignore": 0} if action == "continue" else {}))
        if str(r.get("code")) != "200":
            raise RuntimeError(f"{action} refused: {r.get('msg')}")
    return {"ok": True}


# --------------------------------------------------------------------------
# Share links (/v2/share/*): a link anyone can open, with an optional password and expiry
# --------------------------------------------------------------------------
SHARE_TPL = "https://znas.cn/AppH5/share/"


def _b32(text):
    return base64.b32encode(text.encode()).decode().rstrip("=")


def _share_link(tpl, nas_id, code):
    return f"{tpl}?nid={_b32(nas_id)}&code={code}&mode=file&display=list&advancedmode=0"


def share_create(d, paths, days=None, password=None):
    s = settings_get()
    days = int(days if days is not None else s["share_days"])
    password = s["share_password"] if password is None else password
    pairs = [("paths[]", p) for p in paths] + [("has_pass", "1" if password else "0"), ("advanced_mode", "0"), ("snum", "0"),
                                               ("type", "2"), ("expire", str(max(1, days) * 86400))]
    r = d._post_raw("/v2/share/create", pairs)
    if str(r.get("code")) != "200":
        raise RuntimeError(f"share refused: {r.get('msg')}")
    data = r["data"]
    sh = data["share"]
    return {"id": sh["id"], "link": _share_link(data["tpl_url"], data["nas_id"], sh["code"]), "pass": sh.get("pass") if password else "",
            "days": days, "name": sh.get("fname") or sh.get("dirname") or ""}


def share_list(d):
    r = d.post("/v2/share/list", start=0, num=100)
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"share list refused: {r.get('code')}")
    data = r.get("data") or {}
    out = []
    for x in data.get("list") or []:
        out.append({"id": x["id"], "code": x["code"], "link": share_link_for(d, x), "pass": x.get("pass", ""), "name": x.get("fname") or x.get("dirname") or "",
                    "count": int(x.get("fnum") or 0) + int(x.get("dirnum") or 0), "expire": int(x.get("expire") or 0),
                    "expired": x.get("expired") == "1", "created": int(x.get("created_at") or 0)})
    return {"shares": out}


def share_link_for(d, share):
    """Rebuild the link of an existing share (the list does not carry the template)."""
    return _share_link(SHARE_TPL, d.s.get("nasid") or _profile().get("nasid") or "", share["code"])


def share_delete(d, ids):
    r = d._post_raw("/v2/share/delete", [("ids[]", i) for i in ids])
    if str(r.get("code")) != "200":
        raise RuntimeError(f"share delete refused: {r.get('msg')}")
    return {"deleted": len(ids)}


# --------------------------------------------------------------------------
# Web apps of the NAS (Docker, Downloader, Backup, ...) in the signed-in window
# --------------------------------------------------------------------------
APP_ROUTES = {"video": "/home/video", "music": "/home/musicGuide", "photoAlbum": "/home/album/", "fileManage": "/home/fileManage/myFiles"}


def apps_list(d):
    r = d.post("/AppStore/v1/list")
    if str(r.get("code")) != "200":
        raise ExpiredSession(f"apps refused: {r.get('code')}")
    out = []
    for a in (r.get("data") or {}).get("list") or []:
        li = a.get("local_info") or {}
        if a.get("status") != "running" or not li:
            continue
        names = {x.get("lang"): x.get("name") for x in (li.get("i18n") or [])}
        out.append({"app": a["app_name"], "title": names.get("en_us") or next(iter(names.values()), a["app_name"]),
                    "admin": "users" not in (li.get("role") or []), "docker": bool(li.get("is_docker"))})
    out.sort(key=lambda x: (x["admin"], x["title"].lower()))
    return {"apps": out}


# --------------------------------------------------------------------------
# Offline videos
# --------------------------------------------------------------------------
def video_dir(path):
    return os.path.join(OFFLINE_DIR, "video", hashlib.sha1(path.encode()).hexdigest()[:12])


def video_local(path):
    """(file, [subtitle files]) when this video was saved for offline viewing, else None."""
    dd = video_dir(path)
    try:
        m = json.load(open(os.path.join(dd, "meta.json")))
    except (OSError, ValueError):
        return None
    f = os.path.join(dd, m.get("file", ""))
    if not m.get("complete") or not os.path.exists(f):
        return None
    return f, [os.path.join(dd, x) for x in m.get("subs", []) if os.path.exists(os.path.join(dd, x))]


def video_pin(d, path, name=""):
    """Download the original file (resumable) plus sibling subtitles into the offline folder. Prints {"pin": percent} lines."""
    dd = video_dir(path)
    os.makedirs(dd, mode=0o700, exist_ok=True)
    size = d.run(file_size, d, path)
    ext = os.path.splitext(path)[1].lower() or ".mkv"
    fn = "video" + ext
    part = os.path.join(dd, fn + ".part")
    have = os.path.getsize(part) if os.path.exists(part) else 0
    last = -1
    while have < size:
        end = min(size - 1, have + (64 << 20) - 1)
        try:
            data = d.read_range(path, have, end, size)
        except (urllib.error.HTTPError, ExpiredSession):
            login(interactive=False)
            d.__init__()
            continue
        with open(part, "ab") as f:
            f.write(data)
        have += len(data)
        pct = int(have * 100 / size)
        if pct != last:
            last = pct
            print(json.dumps({"pin": pct}), flush=True)
    if os.path.exists(part):
        os.replace(part, os.path.join(dd, fn))
    subs = []
    try:
        for k, sp in enumerate(d.run(_sibling_subs, d, path)):
            r = d.run(d.get, sp, dd)
            sn = f"sub{k}-" + os.path.basename(r["path"])
            os.replace(r["path"], os.path.join(dd, sn))
            subs.append(sn)
    except Exception:
        pass
    with open(os.path.join(dd, "meta.json"), "w") as f:
        json.dump({"path": path, "name": name or os.path.basename(path), "file": fn, "subs": subs, "size": size, "complete": True,
                   "ts": int(time.time())}, f)
    pins = _pins()
    pins["videos"][path] = {"name": name or os.path.basename(path), "size": size, "ts": int(time.time())}
    _save_pins(pins)
    return {"pinned": True, "size": size}


def video_unpin(path):
    shutil.rmtree(video_dir(path), ignore_errors=True)
    pins = _pins()
    pins["videos"].pop(path, None)
    _save_pins(pins)
    return {"unpinned": True}


# --------------------------------------------------------------------------
# Video queue: the next episodes after the one that is playing (autoplay)
# --------------------------------------------------------------------------
VQUEUE = os.path.join(_runtime(), "unifydrive-vqueue.json")


def vqueue_read():
    try:
        return json.load(open(VQUEUE))
    except (OSError, ValueError):
        return None


def vqueue_write(owner, items, immediate=False):
    if not items:
        vqueue_clear()
        return
    fd = os.open(VQUEUE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump({"owner": owner, "items": items, "immediate": immediate}, f)


def vqueue_clear():
    try:
        os.unlink(VQUEUE)
    except OSError:
        pass
    return {"queue": 0}


# --------------------------------------------------------------------------
# Background agent: live upload of a photo/screenshot folder + NAS health alerts (a systemd user service)
# --------------------------------------------------------------------------
AGENT_STATE = os.path.join(CACHE, "agent.json")
ALERTS = os.path.expanduser("~/.config/unifydrive/alerts.json")


def _agent_note(**kw):
    try:
        st = json.load(open(AGENT_STATE))
    except (OSError, ValueError):
        st = {}
    st.update(kw)
    st["pid"] = os.getpid() if kw.get("_me") else st.get("pid")
    st.pop("_me", None)
    os.makedirs(CACHE, exist_ok=True)
    with open(AGENT_STATE + ".tmp", "w") as f:
        json.dump(st, f)
    os.replace(AGENT_STATE + ".tmp", AGENT_STATE)


def alerts_check(d, notify_on=True):
    """Compare the NAS state with the thresholds; notify once when a problem appears, and again when the NAS comes back."""
    try:
        st = d.run(nas_status, d)
        problems = st["problems"]
        online = True
    except Exception:
        problems, online = ["NAS unreachable"], False
    try:
        data = json.load(open(ALERTS))
    except (OSError, ValueError):
        data = {}
    active = data.get("active", {})
    fails = data.get("fails", 0)
    if not online:
        fails += 1
        problems = problems if fails >= 2 else []          # one missed poll is not an outage
    else:
        fails = 0
    keys = {re.sub(r"\d+(\.\d+)?", "#", p): p for p in problems}
    for k, p in keys.items():
        if k not in active:
            active[k] = int(time.time())
            if notify_on:
                notify("UnifyDrive: " + p, "Open the UnifyDrive panel for details.", urgent=True, timeout=0)
    for k in [k for k in active if k not in keys]:
        del active[k]
        if notify_on and online:
            notify("UnifyDrive: all clear", "The earlier problem is gone.")
    with open(ALERTS, "w") as f:
        json.dump({"active": active, "fails": fails}, f)
    return {"problems": list(keys.values()), "online": online}


def _photo_sig(root):
    n = m = 0
    for cur, dirs, files in os.walk(root):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."):
                continue
            try:
                m = max(m, os.stat(os.path.join(cur, f)).st_mtime)
                n += 1
            except OSError:
                pass
    return (n, int(m))


def agent_run():
    """Long-running loop: upload changed folders flagged `live` (every ~15 s) and run the health check (every 5 min)."""
    _agent_note(_me=1, started=int(time.time()), syncing="", uploaded=0)
    sigs, last_health, last_job = {}, 0, {}
    while True:
        try:
            s = settings_get()
            cfg = _sync_cfg()
            for job in [j for j in cfg["jobs"] if j.get("live")]:
                if not os.path.isdir(job["local"]):
                    continue
                sig = _photo_sig(job["local"])
                if sigs.get(job["name"]) == sig or time.time() - last_job.get(job["name"], 0) < 15:
                    continue
                _agent_note(syncing=job["name"])
                try:
                    d = Drive()
                    res = d.run(sync_one, d, job)
                    res["ts"] = int(time.time())
                    cfg = _sync_cfg()
                    cfg["state"][job["name"]] = res
                    _save_sync(cfg)
                    if not res["failed"] and not res["recent"]:      # files still being written get another pass
                        sigs[job["name"]] = sig
                    if res["uploaded"] and s["notify_sync"]:
                        notify("UnifyDrive: uploaded " + str(res["uploaded"]) + (" file" if res["uploaded"] == 1 else " files"), job["name"] + " → " + job["remote"])
                    _agent_note(uploaded=(json.load(open(AGENT_STATE)).get("uploaded", 0) + res["uploaded"]))
                except Exception as e:
                    _agent_note(error=str(e)[:160])
                last_job[job["name"]] = time.time()
                _agent_note(syncing="", last_sync=int(time.time()))
            if s["alerts"] and time.time() - last_health > 300:
                last_health = time.time()
                try:
                    alerts_check(Drive())
                except Exception as e:
                    _agent_note(error=str(e)[:160])
                _agent_note(last_health=int(time.time()))
        except Exception as e:
            _agent_note(error=str(e)[:160])
        time.sleep(5)


def agent_status():
    on = subprocess.run(["systemctl", "--user", "is-active", "unifydrive-agent.service"], capture_output=True, text=True).stdout.strip() == "active"
    try:
        st = json.load(open(AGENT_STATE))
    except (OSError, ValueError):
        st = {}
    try:
        al = json.load(open(ALERTS)).get("active", {})
    except (OSError, ValueError):
        al = {}
    live = [j["name"] for j in _sync_cfg()["jobs"] if j.get("live")]
    return {"running": on, "syncing": st.get("syncing", "") if on else "", "last_sync": st.get("last_sync"), "last_health": st.get("last_health"),
            "uploaded": st.get("uploaded", 0), "error": st.get("error", ""), "alerts": list(al), "live": live}


def agent_service(mode):
    udir = os.path.expanduser("~/.config/systemd/user")
    if mode == "on":
        os.makedirs(udir, exist_ok=True)
        with open(os.path.join(udir, "unifydrive-agent.service"), "w") as f:
            f.write(f"[Unit]\nDescription=UnifyDrive background agent (live upload, NAS health alerts)\nAfter=network-online.target\n\n"
                    f"[Service]\nExecStart={sys.executable} -I {os.path.abspath(__file__)} agent\nRestart=on-failure\nRestartSec=30\nNice=10\n\n"
                    f"[Install]\nWantedBy=default.target\n")
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True)
        subprocess.run(["systemctl", "--user", "enable", "--now", "unifydrive-agent.service"], check=True, capture_output=True)
    elif mode == "off":
        subprocess.run(["systemctl", "--user", "disable", "--now", "unifydrive-agent.service"], capture_output=True)
    return agent_status()


def photosync(mode):
    """Live upload of the photo/screenshot folder: a sync job flagged `live` plus the background agent."""
    s = settings_get()
    cfg = _sync_cfg()
    cfg["jobs"] = [j for j in cfg["jobs"] if j["name"] != "Photos"]
    if mode == "on":
        local = os.path.abspath(os.path.expanduser(s["photo_local"]))
        os.makedirs(local, exist_ok=True)
        cfg["jobs"].append({"name": "Photos", "local": local, "remote": s["photo_remote"].rstrip("/"), "live": True})
    else:
        cfg["state"].pop("Photos", None)
    _save_sync(cfg)
    if mode == "on":
        agent_service("on")
    elif not any(j.get("live") for j in cfg["jobs"]) and not s["alerts"]:
        agent_service("off")
    return agent_status()


# --------------------------------------------------------------------------
# Quick capture: screenshot -> NAS (optionally with a share link on the clipboard)
# --------------------------------------------------------------------------
def capture(d, mode="region", link=None):
    s = settings_get()
    os.makedirs(os.path.join(CACHE, "capture"), exist_ok=True)
    name = time.strftime("screenshot-%Y-%m-%d_%H-%M-%S") + ".png"
    tmp = os.path.join(CACHE, "capture", name)
    if mode == "region":
        g = subprocess.run(["slurp"], capture_output=True, text=True).stdout.strip()
        if not g:
            return {"cancelled": True}
        subprocess.run(["grim", "-g", g, tmp], check=True)
    else:
        subprocess.run(["grim", tmp], check=True)
    dest = s["capture_remote"].rstrip("/")
    try:
        d.run(_ensure_remote, d, dest)
        d.run(d.put, tmp, dest, 0, (1, 1))
    except Exception:
        os.unlink(tmp)
        raise
    rpath = dest + "/" + name
    out = {"path": rpath}
    want_link = s["capture_link"] if link is None else link
    if want_link:
        sh = d.run(share_create, d, [rpath], None, None)
        out["link"] = sh["link"]
        out["pass"] = sh["pass"]
        text = sh["link"] + (f"\nPassword: {sh['pass']}" if sh["pass"] else "")
    else:
        text = rpath
    try:
        subprocess.run(["wl-copy", text], timeout=5)
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        os.unlink(tmp)
    except OSError:
        pass
    notify("Screenshot saved to your NAS", ("Share link copied" if want_link else "Path copied") + "  ·  " + name)
    return out



def main(a):
    cmd = a[0] if a else ""
    if cmd == "login":
        try:
            out = login(interactive="--auto" not in a, forget="--forget" in a)
        except NeedsInteraction as e:
            print(json.dumps({"error": str(e), "needs_interaction": True}))
            sys.exit(2)
        print(json.dumps(out))
        return
    if cmd == "logout":
        out = end_session()
        if "--forget" in a:
            out.update(login(forget=True))
        print(json.dumps(out))
        return
    if cmd == "status":
        prof = _profile()
        print(json.dumps({"email": prof.get("email"), "username": prof.get("username"),
                          "password_saved": bool(_secret("password")), "cloud_token_saved": bool(_secret("cloud_token")),
                          "session": os.path.exists(SESSION)}))
        return
    if cmd == "cover-proxy":
        cover_proxy()
        return
    if cmd == "audio-proxy":
        audio_proxy()
        return
    if cmd == "comic-progress":
        print(json.dumps(comic_progress(a[1:])))
        return
    if cmd == "comic-pref":
        print(json.dumps(comic_pref(a[1:])))
        return
    if cmd == "comic-read":
        print(json.dumps(comic_read(a[1], a[2] if len(a) > 2 else "")))
        return
    if cmd == "_feed":
        d = Drive()
        feed(d, a[1:])
        return
    if cmd == "player":
        print(json.dumps(player(a[1] if len(a) > 1 else "status")))
        return
    if cmd == "vplayer":
        print(json.dumps(vplayer(a[1] if len(a) > 1 else "status")))
        return
    if cmd == "sync-list":
        print(json.dumps(sync_list()))
        return
    if cmd == "sync-add":
        print(json.dumps(sync_add(a[1], a[2], a[3] if len(a) > 3 else "")))
        return
    if cmd == "sync-remove":
        print(json.dumps(sync_remove(a[1])))
        return
    if cmd == "sync-timer":
        print(json.dumps(sync_timer(a[1] if len(a) > 1 else "status")))
        return
    if cmd == "photo-open":
        print(json.dumps(photo_open(a[1] if len(a) > 1 else "0", a[2] if len(a) > 2 else "")))
        return
    if cmd == "offline-list":
        print(json.dumps(offline_list()))
        return
    if cmd == "album-unpin":
        print(json.dumps(album_unpin(a[1])))
        return
    if cmd == "comic-unpin":
        print(json.dumps(comic_unpin(a[1])))
        return
    if cmd == "continue":
        print(json.dumps(continue_items()))
        return
    if cmd == "video-progress":
        print(json.dumps(video_progress(a[1:])))
        return
    if cmd == "settings":
        if len(a) >= 4 and a[1] == "set":
            print(json.dumps(settings_set(a[2], a[3])))
        else:
            print(json.dumps(settings_get()))
        return
    if cmd == "vqueue-clear":
        print(json.dumps(vqueue_clear()))
        return
    if cmd == "video-unpin":
        print(json.dumps(video_unpin(a[1])))
        return
    if cmd == "agent":
        agent_run()
        return
    if cmd == "agent-status":
        print(json.dumps(agent_status()))
        return
    if cmd == "agent-service":
        print(json.dumps(agent_service(a[1] if len(a) > 1 else "status")))
        return
    if cmd == "photosync":
        print(json.dumps(photosync(a[1] if len(a) > 1 else "off")))
        return
    if not os.path.exists(SESSION):
        login(interactive=False)
    d = Drive()
    if cmd == "webui":
        print(json.dumps(d.run(webui, d, a[1] if len(a) > 1 else "")), flush=True)
        return
    if cmd == "_vplay":
        vplay(d, a[1], a[2:])
        return
    if cmd == "play" and _kind(a[1]) == "video" and not os.environ.get("UD_VIDEO_TRANSCODE"):
        sup = subprocess.Popen([sys.executable, "-I", os.path.abspath(__file__), "_vplay", a[1], *a[2:]],
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL, start_new_session=True)
        line = sup.stdout.readline().decode().strip()
        if not line:
            print(json.dumps({"error": "could not start the video player"}))
            sys.exit(1)
        print(line)
        return
    if cmd == "stream-url":
        print(json.dumps(d.run(resolve_stream, d, a[1])))
        return
    if cmd == "play":
        info, argv = d.run(play, d, a[1], a[2:])
        subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        info.pop("url")
        print(json.dumps(info))
        return
    if cmd == "upload":
        pol = "ask"
        args = a[1:]
        if args and args[0].startswith("--on-conflict="):
            pol, args = args[0].split("=", 1)[1], args[1:]
        print(json.dumps(upload_many(d, args[1:], args[0], pol)))
        return
    if cmd == "comic-fetch":
        comic_fetch(d, a[1], int(a[2]), int(a[3]) if len(a) > 3 else 0)
        return
    if cmd == "comic-series":
        comic_series(d, a[1])
        return
    if cmd == "mkdir":
        name = a[2] if len(a) > 2 else _ask("New folder name")
        print(json.dumps({"path": d.run(d.mkdir, a[1], name)}))
        return
    if cmd == "rm":
        print(json.dumps(d.run(d.remove, a[1:])))
        return
    if cmd == "album-pin":
        print(json.dumps(album_pin(d, a[1], a[2] if len(a) > 2 else "", a[3] if len(a) > 3 else "")))
        return
    if cmd == "comic-pin":
        print(json.dumps(comic_pin(d, a[1], int(a[2]) if len(a) > 2 else 10, a[3] if len(a) > 3 else "")))
        return
    if cmd == "tasks":
        out = d.run(tasks_list, d)
    elif cmd == "task-act":
        out = d.run(task_act, d, a[1], a[2:])
    elif cmd == "share-create":
        days, pw, paths = None, None, []
        for x in a[1:]:
            if x.startswith("--days="):
                days = int(x[7:])
            elif x == "--nopass":
                pw = False
            else:
                paths.append(x)
        out = d.run(share_create, d, paths, days, pw)
    elif cmd == "share-list":
        out = d.run(share_list, d)
    elif cmd == "share-delete":
        out = d.run(share_delete, d, a[1:])
    elif cmd == "apps":
        out = d.run(apps_list, d)
    elif cmd == "video-pin":
        out = video_pin(d, a[1], a[2] if len(a) > 2 else "")
    elif cmd == "capture":
        out = capture(d, a[1] if len(a) > 1 else "region", True if "--link" in a else (False if "--nolink" in a else None))
    elif cmd == "alerts-check":
        out = alerts_check(d, "--quiet" not in a)
    elif cmd == "sync-run":
        out = sync_run(d, a[1] if len(a) > 1 and not a[1].startswith("--") else "", "--dry" in a)
    elif cmd == "photos":
        out = d.run(photo_list, d, int(a[1]) if len(a) > 1 else 0, int(a[2]) if len(a) > 2 else 60)
    elif cmd == "photo-get":
        out = photo_get(d, a[1])
    elif cmd == "nas-status":
        out = d.run(nas_status, d)
    elif cmd == "search":
        out = d.run(search_all, d, a[1], a[2] if len(a) > 2 else "/nvme11/my/data", a[3] if len(a) > 3 else "/nvme11/my/data/Comics")
    elif cmd == "video-libs":
        out = d.run(video_libs, d)
    elif cmd == "video-list":
        out = d.run(video_list, d, a[1])
    elif cmd == "video-items":
        out = d.run(video_items, d, a[1], a[2])
    elif cmd == "albums-all":
        out = d.run(albums_all, d, "--refresh" in a)
    elif cmd == "albums":
        out = d.run(albums, d, int(a[1]) if len(a) > 1 else 1)
    elif cmd == "songs":
        out = d.run(songs, d, a[1])
    elif cmd == "play-album":
        out = d.run(play_album, d, a[1], int(a[2]) if len(a) > 2 else 0, a[3] if len(a) > 3 else "", a[4] if len(a) > 4 else "")
    elif cmd == "comic-chapters":
        out = d.run(comic_chapters, d, a[1])
    elif cmd == "ls":
        out = d.run(d.ls, a[1])
    elif cmd == "get":
        out = d.run(d.get, a[1], a[2] if len(a) > 2 else ".")
    elif cmd == "put":
        out = d.run(d.put, a[1], a[2])
    elif cmd == "check":
        out = {"ok": d.run(lambda: str(d.post("/v2/file/info", path="/nvme11/my/data").get("code")) == "200")}
    else:
        sys.exit(__doc__)
    print(json.dumps(out))


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except NeedsInteraction as e:
        print(json.dumps({"error": str(e), "needs_interaction": True}))
        sys.exit(2)
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
