# Pixel FTP setup

The sync service uploads into a folder on an Android phone over FTP. Google Photos must be configured to back up that folder.

## Suggested apps

- **Primitive FTPd** (or similar) — FTP/FTPS server on device
- **Google Photos** — backup & sync enabled for the target directory

## Directory

Use a path under a location Google Photos watches, commonly:

```text
/storage/emulated/0/DCIM/ImmichSync
```

Create the folder once (Files app or `adb shell mkdir -p …`). Put that exact path in the portal as **Remote dir**.

## FTP settings (typical)

| Setting | Example |
|---------|---------|
| Host | Pixel LAN IP (`pixel.example.local` or `192.168.x.x`) |
| Port | `12345` (or your app’s port) |
| Username / password | as configured in the FTP app |
| Passive mode | enabled (this client uses PASV) |

In the portal, use **Test FTP** after saving the user. Success means login + cwd/create of `remote_dir` worked.

## Operational tips

- Keep the FTP server **running** while syncing (foreground service / start on boot if available)
- The Pixel should stay on the same LAN as the sync host (or VPN)
- Charge-limiting / always-plugged setups help for a dedicated “backup brick” phone
- After files appear under `DCIM/ImmichSync`, confirm Google Photos shows them and backup progresses

## Security

FTP on a phone is for **trusted LAN** only. Prefer a strong password; do not expose the FTP port to the internet.
