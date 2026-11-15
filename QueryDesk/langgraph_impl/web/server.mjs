import http from 'node:http'
import fs from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), 'dist')
const port = Number(process.env.UI_PORT || 3000)
const api = new URL(process.env.QUERYDESK_API_URL || 'http://127.0.0.1:8000')
if (api.protocol !== 'http:' || !['127.0.0.1', 'localhost'].includes(api.hostname)) throw new Error('The API must use a local HTTP address.')
if (!fs.existsSync(path.join(root, 'index.html'))) throw new Error('Frontend build missing. Run npm run build first.')
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.svg': 'image/svg+xml', '.png': 'image/png', '.ico': 'image/x-icon', '.woff2': 'font/woff2' }

const server = http.createServer((req, res) => {
  res.setHeader('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; form-action 'self'")
  res.setHeader('Referrer-Policy','no-referrer')
  res.setHeader('X-Frame-Options','DENY')
  const url = new URL(req.url, 'http://localhost')
  if (url.pathname.startsWith('/api/')) {
    const headers = { ...req.headers, host: api.host }
    delete headers.connection
    const proxy = http.request({ hostname: api.hostname, port: api.port || 80, path: url.pathname + url.search, method: req.method, headers }, upstream => {
      res.writeHead(upstream.statusCode, upstream.headers)
      upstream.pipe(res)
    })
    proxy.on('error', () => { if (!res.headersSent) res.writeHead(502, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ detail: 'The data service is not available. Start the FastAPI backend and try again.' })) })
    req.pipe(proxy)
    return
  }
  if (!['GET', 'HEAD'].includes(req.method)) { res.writeHead(405); res.end(); return }
  let pathname
  try { pathname = decodeURIComponent(url.pathname) } catch { res.writeHead(400); res.end(); return }
  const target = path.resolve(root, '.' + pathname)
  if (target !== root && !target.startsWith(root + path.sep)) { res.writeHead(403); res.end(); return }
  const isFile = fs.existsSync(target) && fs.statSync(target).isFile()
  const file = isFile ? target : path.join(root, 'index.html')
  if (!isFile && path.extname(pathname)) { res.writeHead(404); res.end(); return }
  res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'X-Content-Type-Options': 'nosniff', 'Cache-Control': 'no-cache' })
  if (req.method === 'HEAD') res.end()
  else fs.createReadStream(file).pipe(res)
})
server.listen(port, '127.0.0.1', () => console.log(`QueryDesk: http://127.0.0.1:${port}`))
