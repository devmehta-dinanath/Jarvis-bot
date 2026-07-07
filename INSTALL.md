# Jarvis Desktop Installation Guide

## Download

Get the latest installer from GitHub Actions artifacts:
- **Windows**: `jarvis-windows-installers` → `Jarvis Setup X.X.X.exe`
- **macOS**: `jarvis-darwin-installers` → `Jarvis-X.X.X-arm64.dmg`
- **Linux**: `jarvis-linux-installers` → `Jarvis-X.X.X.AppImage`

---

## Windows Installation

1. Download `Jarvis Setup X.X.X.exe`
2. Double-click to install
3. **If SmartScreen appears**: Click "More info" → "Run anyway"
4. Follow the installer prompts
5. Launch Jarvis from Start Menu
6. **If Windows Firewall prompts**: Allow access on Private networks

### Windows Verification

Open Command Prompt and run:
```cmd
curl http://127.0.0.1:3030/health
curl http://127.0.0.1:8000/health
```

Both should return `{"status":"ok"}`.

### Windows Troubleshooting

If Jarvis fails to start, check logs at:
```
%APPDATA%\Jarvis\logs\
```

Files to inspect:
- `runtime.log` — startup sequence
- `screenpipe.log` — screen capture errors
- `backend.log` — API server errors

---

## macOS Installation

### ⚠️ Important: The app is not code-signed with an Apple Developer ID

Because we don't have an Apple Developer account yet, macOS Gatekeeper will block the app by default. You need to bypass it manually.

### Steps:

1. Download `Jarvis-X.X.X-arm64.dmg`
2. Open the DMG and drag Jarvis to Applications
3. **DO NOT double-click Jarvis yet**
4. Open Terminal and run:
   ```bash
   xattr -cr /Applications/Jarvis.app
   ```
5. Now open Jarvis from Applications (right-click → Open the first time)
6. macOS will ask to grant permissions:
   - **Screen Recording**: System Settings → Privacy & Security → Screen Recording → Enable Jarvis
   - **Microphone**: System Settings → Privacy & Security → Microphone → Enable Jarvis
7. **Quit and relaunch Jarvis** after granting permissions

### macOS Verification

Open Terminal and run:
```bash
curl http://127.0.0.1:3030/health
curl http://127.0.0.1:8000/health
```

### macOS Troubleshooting

Logs are at:
```
~/Library/Application Support/Jarvis/logs/
```

If you see "screenpipe exited code=X" repeatedly, it usually means:
- **Screen Recording permission not granted** — check System Settings
- **Wrong architecture** — Intel Mac users need x64 build (currently ARM64 only)

---

## Linux Installation

### Prerequisites

```bash
# Ubuntu/Debian
sudo apt install libfuse2 libasound2t64 ffmpeg

# Fedora
sudo dnf install fuse-libs alsa-lib ffmpeg
```

### Steps:

1. Download `Jarvis-X.X.X.AppImage`
2. Make executable:
   ```bash
   chmod +x Jarvis-*.AppImage
   ```
3. Run:
   ```bash
   ./Jarvis-*.AppImage
   ```

### Linux Verification

```bash
curl http://127.0.0.1:3030/health
curl http://127.0.0.1:8000/health
```

### Linux Troubleshooting

Logs are at:
```
~/.config/Jarvis/logs/
```

Common issues:
- **AppImage won't launch**: Install `libfuse2` (not `libfuse3`)
- **Screenpipe crashes on Wayland**: Switch to X11 session (log out → gear icon → "Ubuntu on Xorg")

---

## Known Limitations

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| Auto-install | ✅ | ⚠️ Needs xattr command | ✅ |
| Screen capture | ✅ | ⚠️ Needs permission | ✅ (X11 only) |
| Code signing | ❌ (SmartScreen warning) | ❌ (Gatekeeper blocks) | N/A |
| Auto-updates | ❌ | ❌ | ❌ |

### To fix macOS Gatekeeper (production)

Requires:
1. Apple Developer Program membership ($99/year)
2. Developer ID Application certificate
3. Apple ID with app-specific password for notarization
4. Setting these secrets in GitHub Actions:
   - `APPLE_ID`
   - `APPLE_APP_SPECIFIC_PASSWORD`
   - `APPLE_TEAM_ID`
   - `CSC_LINK` (base64 of .p12 certificate)
   - `CSC_KEY_PASSWORD`

### To fix Windows SmartScreen (production)

Requires:
1. Code signing certificate (~$100-400/year from DigiCert, Sectigo, etc.)
2. Setting `CSC_LINK` and `CSC_KEY_PASSWORD` secrets

---

## Support

- Backend runs at: `http://127.0.0.1:8000`
- Screenpipe runs at: `http://127.0.0.1:3030`
- Central sync server: `https://jarvis-api.lilium.co.in`
