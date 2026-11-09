import { createServer } from "node:http";
import { createReadStream, existsSync, statSync } from "node:fs";
import { cp, mkdir } from "node:fs/promises";
import { dirname, extname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = dirname(fileURLToPath(import.meta.url));
const dist = join(root, "dist");
const files = [
  "index.html", "product.html", "download.html", "styles.css", "site.js", "release.js",
  "assets/app-icon.png", "assets/violet-form.webp", "assets/after-dark.webp",
  "assets/after-dark-graded.webp", "assets/violet-gate.webp",
  "assets/night-shift.webp", "assets/night-shift.mp4", "assets/frame-study.webp",
];

if (process.argv[2] === "build") {
  await mkdir(join(dist, "assets"), { recursive: true });
  for (const file of files) await cp(join(root, file), join(dist, file));
  const downloads = join(root, "downloads");
  if (existsSync(downloads)) await cp(downloads, join(dist, "downloads"), { recursive: true });
  console.log("Ready to deploy: " + dist);
} else {
  const types = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".exe": "application/octet-stream",
  };
  const port = Number(process.argv[2] || 4173);
  createServer((request, response) => {
    if (request.method !== "GET" && request.method !== "HEAD") {
      response.writeHead(405, { Allow: "GET, HEAD" }).end();
      return;
    }
    const pathname = request.url.split("?")[0];
    const file = pathname === "/" ? "index.html" : pathname.slice(1);
    const installer = /^downloads\/[\w.-]+\.exe$/i.test(file);
    const local = join(root, file);
    if ((!files.includes(file) && !installer) || !existsSync(local) || !statSync(local).isFile()) {
      response.writeHead(404).end("Not found");
      return;
    }
    const size = statSync(local).size;
    const extension = extname(file).toLowerCase();
    const headers = {
      "Content-Type": types[extension],
      "Content-Length": size,
      "Cache-Control": "no-store",
      ...(installer && { "Content-Disposition": "attachment" }),
    };
    let range;
    if (extension === ".mp4") {
      headers["Accept-Ranges"] = "bytes";
      const match = /^bytes=(\d*)-(\d*)$/.exec(request.headers.range || "");
      if (match && (match[1] || match[2])) {
        const start = match[1] ? Number(match[1]) : Math.max(0, size - Number(match[2]));
        const end = match[1] && match[2] ? Math.min(size - 1, Number(match[2])) : size - 1;
        if (start >= size || end < start) {
          response.writeHead(416, { "Content-Range": "bytes */" + size }).end();
          return;
        }
        range = { start, end };
        headers["Content-Range"] = "bytes " + start + "-" + end + "/" + size;
        headers["Content-Length"] = end - start + 1;
      }
    }
    response.writeHead(range ? 206 : 200, headers);
    if (request.method === "HEAD") response.end();
    else createReadStream(local, range).pipe(response);
  }).listen(port, "127.0.0.1", () => console.log("Local: http://127.0.0.1:" + port));
}
