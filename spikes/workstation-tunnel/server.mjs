import http from "node:http";
import crypto from "node:crypto";

const PORT = Number(process.env.SPIKE_PORT ?? 47000);
const WS_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11";

const log = (msg, extra = {}) =>
  console.log(JSON.stringify({ ts: new Date().toISOString(), msg, ...extra }));

const downloadHashes = new Map();

function readBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on("data", (c) => chunks.push(c));
    req.on("end", () => resolve(Buffer.concat(chunks)));
    req.on("error", reject);
  });
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url, "http://localhost");
  log("request", { method: req.method, path: url.pathname, cl: req.headers["content-length"] ?? null });

  if (req.method === "GET" && url.pathname === "/ping") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
    return;
  }

  if (req.method === "POST" && url.pathname === "/execute") {
    const raw = await readBody(req);
    let duration = 300;
    try {
      duration = Number(JSON.parse(raw.toString() || "{}").duration_s ?? 300);
    } catch {}
    const started = Date.now();
    res.writeHead(200, {
      "content-type": "application/x-ndjson",
      "cache-control": "no-store",
      "x-accel-buffering": "no",
    });
    res.write(JSON.stringify({ type: "start", duration_s: duration }) + "\n");
    let n = 0;
    const keepalive = setInterval(() => {
      n += 1;
      const ok = res.write(
        JSON.stringify({ type: "keepalive", n, elapsed_s: (Date.now() - started) / 1000 }) + "\n",
      );
      log("keepalive", { n, ok });
    }, 10_000);
    const finish = setTimeout(() => {
      clearInterval(keepalive);
      res.end(
        JSON.stringify({
          type: "exit",
          exit_code: 0,
          elapsed_s: (Date.now() - started) / 1000,
          keepalives: n,
        }) + "\n",
      );
      log("execute finished", { duration, keepalives: n });
    }, duration * 1000);
    res.on("close", () => {
      clearInterval(keepalive);
      clearTimeout(finish);
      log("execute connection closed", { writableEnded: res.writableEnded, elapsed_s: (Date.now() - started) / 1000 });
    });
    return;
  }

  if (req.method === "POST" && url.pathname === "/upload") {
    const hash = crypto.createHash("sha256");
    let bytes = 0;
    req.on("data", (c) => {
      bytes += c.length;
      hash.update(c);
    });
    req.on("end", () => {
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ bytes, sha256: hash.digest("hex") }));
      log("upload done", { bytes });
    });
    req.on("error", (e) => {
      log("upload error", { error: String(e), bytes });
      if (!res.headersSent) res.writeHead(400).end();
    });
    return;
  }

  if (req.method === "GET" && url.pathname === "/download") {
    const total = Number(url.searchParams.get("bytes") ?? 1024);
    const token = url.searchParams.get("token") ?? "default";
    const hash = crypto.createHash("sha256");
    res.writeHead(200, {
      "content-type": "application/octet-stream",
      "content-length": String(total),
    });
    let sent = 0;
    const CHUNK = 256 * 1024;
    const pump = () => {
      while (sent < total) {
        const size = Math.min(CHUNK, total - sent);
        const buf = crypto.randomBytes(size);
        hash.update(buf);
        sent += size;
        if (!res.write(buf)) {
          res.once("drain", pump);
          return;
        }
      }
      downloadHashes.set(token, { bytes: sent, sha256: hash.digest("hex") });
      res.end();
      log("download done", { bytes: sent, token });
    };
    pump();
    return;
  }

  if (req.method === "GET" && url.pathname === "/download-hash") {
    const token = url.searchParams.get("token") ?? "default";
    const entry = downloadHashes.get(token);
    res.writeHead(entry ? 200 : 404, { "content-type": "application/json" });
    res.end(JSON.stringify(entry ?? { error: "unknown token" }));
    return;
  }

  res.writeHead(404, { "content-type": "application/json" });
  res.end(JSON.stringify({ error: "not found", path: url.pathname }));
});

server.on("upgrade", (req, socket) => {
  const url = new URL(req.url, "http://localhost");
  log("upgrade", { path: url.pathname, headers: req.headers });
  if (url.pathname !== "/ws" || (req.headers.upgrade ?? "").toLowerCase() !== "websocket") {
    socket.end("HTTP/1.1 400 Bad Request\r\n\r\n");
    return;
  }
  const key = req.headers["sec-websocket-key"];
  const accept = crypto.createHash("sha1").update(key + WS_GUID).digest("base64");
  socket.write(
    "HTTP/1.1 101 Switching Protocols\r\n" +
      "Upgrade: websocket\r\n" +
      "Connection: Upgrade\r\n" +
      `Sec-WebSocket-Accept: ${accept}\r\n\r\n`,
  );

  let buf = Buffer.alloc(0);
  socket.on("data", (chunk) => {
    buf = Buffer.concat([buf, chunk]);
    for (;;) {
      if (buf.length < 2) return;
      const fin = (buf[0] & 0x80) !== 0;
      const opcode = buf[0] & 0x0f;
      const masked = (buf[1] & 0x80) !== 0;
      let len = buf[1] & 0x7f;
      let offset = 2;
      if (len === 126) {
        if (buf.length < 4) return;
        len = buf.readUInt16BE(2);
        offset = 4;
      } else if (len === 127) {
        if (buf.length < 10) return;
        len = Number(buf.readBigUInt64BE(2));
        offset = 10;
      }
      let mask = null;
      if (masked) {
        if (buf.length < offset + 4) return;
        mask = buf.subarray(offset, offset + 4);
        offset += 4;
      }
      if (buf.length < offset + len) return;
      const payload = Buffer.from(buf.subarray(offset, offset + len));
      buf = buf.subarray(offset + len);
      if (mask) for (let i = 0; i < payload.length; i++) payload[i] ^= mask[i % 4];

      if (opcode === 0x8) {
        socket.end(Buffer.from([0x88, 0x00]));
        return;
      }
      if (opcode === 0x9) {
        socket.write(frame(0xa, payload));
        continue;
      }
      if (opcode === 0x1 || opcode === 0x2) {
        log("ws echo", { text: payload.toString(), fin });
        socket.write(frame(opcode, payload));
      }
    }
  });
  socket.on("error", (e) => log("ws socket error", { error: String(e) }));
});

function frame(opcode, payload) {
  const len = payload.length;
  let header;
  if (len < 126) {
    header = Buffer.from([0x80 | opcode, len]);
  } else if (len < 65536) {
    header = Buffer.alloc(4);
    header[0] = 0x80 | opcode;
    header[1] = 126;
    header.writeUInt16BE(len, 2);
  } else {
    header = Buffer.alloc(10);
    header[0] = 0x80 | opcode;
    header[1] = 127;
    header.writeBigUInt64BE(BigInt(len), 2);
  }
  return Buffer.concat([header, payload]);
}

server.headersTimeout = 0;
server.requestTimeout = 0;
server.timeout = 0;
server.keepAliveTimeout = 120_000;

server.listen(PORT, "127.0.0.1", () => {
  log("listening", { port: PORT });
});
