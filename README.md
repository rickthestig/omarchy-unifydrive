# UnifyDrive for Omarchy

A bar widget and panel for the Omarchy shell that drives a **UnifyDrive UT2 NAS** through the `ud.link` relay: no VPN, no port forwarding.

**Install** — copy into a terminal and run:

```bash
omarchy plugin add https://github.com/rickthestig/omarchy-unifydrive.git --enable
```

**Update** an installed copy:

```bash
omarchy plugin update eg.unifydrive --yes && omarchy restart shell
```

## What it does

- **Files**: browse, upload (files, folders, drag and drop), download, delete to the recycle bin, share links (copied with the password).
- **Music**: albums, offline albums, mini-player.
- **Video**: libraries, resume, subtitles, autoplay next episode, offline videos (mpv).
- **Comics**: a reader window, per-series progress, preloading of the next chapters, offline chapters.
- **Photos**: timeline viewer with slideshow; optional live upload of a local photo folder.
- **Find**: one search across files, albums, videos and comics.
- **More**: NAS transfer queue, share links, NAS web apps launcher, settings page.
- **Health alerts** (optional background agent): low free space, disk temperature, pool problems, NAS unreachable.
- **Quick capture**: screenshot a region or the screen straight to the NAS and copy the link.
- **Web UI window**: opens the NAS web UI already signed in.

## Requirements

`python3`, `mpv`, `libsecret` (`secret-tool`, for the keyring), `wl-clipboard`, `grim` and `slurp` (capture), `libnotify`. Install what you use; the rest is optional.

## First run

Open the panel and sign in: your UnifyDrive cloud e-mail, the captcha, the code e-mailed to you, then the NAS username and password. The cloud token and password live in the system keyring, never in the plugin folder. Session data is under `~/.config/unifydrive/`, caches under `~/.cache/unifydrive/`.

## Settings

Widget settings: start folder (default `/nvme11/my/data`, the UT2 storage pool path; change it for another layout), download folder, comics folder. The **More → Settings** page holds autoplay, alerts, capture, photo sync, share defaults and the comic cache. The background agent and photo sync are off until you switch them on there (this installs a systemd user unit).

## CLI

Everything the panel does is `python3 udrive.py <command>`, printing JSON. See the docstring at the top of `udrive.py`.

## Notes

- This is an unofficial client built on the web app's own API; it can break when the relay changes.
- Plugins run unsandboxed. Read the code before installing.
- Deleting shares or tasks acts on the real NAS; test on items you created.

## License

MIT
