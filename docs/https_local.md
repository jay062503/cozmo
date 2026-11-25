# Running FieldCapture Web over HTTPS on a local network

Android Chrome blocks `getUserMedia` (camera + mic) on plain HTTP origins that
are not `localhost`. When you open `http://192.168.x.x:8000` from your phone,
the camera will be denied.

There are two ways around this.

---

## Option A — mkcert (recommended, 5 minutes)

mkcert creates a locally-trusted certificate that Chrome accepts without warnings.

### 1. Install mkcert on Windows

Download the Windows binary from https://github.com/FiloSottile/mkcert/releases
and place `mkcert.exe` somewhere on your PATH, or run from the project folder.

### 2. Install the local CA

```
mkcert -install
```

This adds a root CA to your Windows certificate store. Run once per machine.

### 3. Generate a certificate for your LAN IP

Replace `192.168.1.42` with your actual LAN IP from `ipconfig`:

```
mkcert 192.168.1.42 localhost 127.0.0.1
```

This creates two files in the current directory:
- `192.168.1.42+2.pem`      (certificate)
- `192.168.1.42+2-key.pem`  (private key)

### 4. Trust the CA on your Android phone

Copy `%LOCALAPPDATA%\mkcert\rootCA.pem` to your phone and install it:
- Settings → Security → Install a certificate → CA certificate
- Pick the `rootCA.pem` file

### 5. Start the server with TLS

```
uvicorn backend.main:app --host 0.0.0.0 --port 8000 ^
  --ssl-certfile "192.168.1.42+2.pem" ^
  --ssl-keyfile  "192.168.1.42+2-key.pem"
```

### 6. Open on Android Chrome

```
https://192.168.1.42:8000
```

No certificate warning. Camera and mic will be allowed.

---

## Option B — Chrome flag (fastest, demo-only)

Chrome has a flag that marks specific origins as "secure" even over HTTP.
This avoids certificate setup entirely but only works on the specific Chrome
instance where you set the flag.

1. On your Android phone, open Chrome and go to:
   ```
   chrome://flags/#unsafely-treat-insecure-origin-as-secure
   ```
2. Enable the flag and add your server URL:
   ```
   http://192.168.1.42:8000
   ```
3. Tap **Relaunch**.
4. Open `http://192.168.1.42:8000` — camera and mic will now work.

This is fine for recording a demo video. Do not use it in production.

---

## Option C — USB tethering (no Wi-Fi needed)

Connect your Android phone to your laptop via USB and enable USB tethering
on the phone (Settings → Hotspot & Tethering → USB tethering).

Your phone will appear as a network adapter on Windows. Find the new IP with
`ipconfig`, then open `http://<that-ip>:8000` from Chrome on the phone.
Because the connection goes over USB (not Wi-Fi), it counts as `localhost`
equivalent in Chrome's security model — camera and mic are allowed over HTTP.
