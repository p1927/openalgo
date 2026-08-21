#!/usr/bin/env node
// Watches src/ and rebuilds dist/ (tsc -b && vite build) on every change,
// for testing against the prebuilt bundle Flask serves at HOST_SERVER
// (broker OAuth callbacks always land there, unlike the Vite dev server).
//
// A build failure can't be shown by React's own ErrorBoundary — if tsc or
// vite build fails there is no valid bundle for React to even boot. Instead
// this writes a static error page directly to dist/index.html so reloading
// the browser shows the failure instead of a blank screen or silently stale
// build.

import { execFile } from 'node:child_process'
import { existsSync, mkdirSync, writeFileSync } from 'node:fs'
import { watch } from 'node:fs'
import path from 'node:path'
import { fileURLToPath } from 'node:url'

const __dirname = path.dirname(fileURLToPath(import.meta.url))
const root = path.resolve(__dirname, '..')
const distDir = path.join(root, 'dist')
const distIndex = path.join(distDir, 'index.html')
const srcDir = path.join(root, 'src')

let building = false
let pending = false
let debounceTimer = null

function escapeHtml(s) {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}

function writeErrorPage(title, output) {
  if (!existsSync(distDir)) mkdirSync(distDir, { recursive: true })
  const html = `<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<title>Build failed — OpenAlgo</title>
<style>
  body { background: #0b0d12; color: #e5e7eb; font-family: system-ui, sans-serif; padding: 2rem; }
  h1 { color: #f87171; margin-bottom: 0.25rem; }
  p { color: #9ca3af; margin-top: 0; }
  pre { background: #14161c; border: 1px solid #262a33; border-radius: 8px; padding: 1rem;
        color: #fca5a5; white-space: pre-wrap; word-break: break-word;
        font-family: ui-monospace, SFMono-Regular, monospace; font-size: 13px; }
</style>
</head>
<body>
  <h1>${escapeHtml(title)}</h1>
  <p>Fix the error below, save, and this page reloads automatically on the next successful build.</p>
  <pre>${escapeHtml(output || '(no output captured)')}</pre>
</body>
</html>`
  writeFileSync(distIndex, html)
}

function runStep(cmd, args) {
  return new Promise((resolve) => {
    execFile(
      cmd,
      args,
      { cwd: root, maxBuffer: 10 * 1024 * 1024 },
      (error, stdout, stderr) => {
        resolve({ ok: !error, output: `${stdout || ''}${stderr || ''}`.trim() })
      }
    )
  })
}

async function build() {
  if (building) {
    pending = true
    return
  }
  building = true
  const startedAt = Date.now()
  console.log('[build-watch] rebuilding...')

  const typeCheck = await runStep('npx', ['tsc', '-b'])
  if (!typeCheck.ok) {
    console.error('[build-watch] type check failed')
    writeErrorPage('TypeScript type check failed', typeCheck.output)
    return finish()
  }

  const build = await runStep('npx', ['vite', 'build'])
  if (!build.ok) {
    console.error('[build-watch] vite build failed')
    writeErrorPage('Vite build failed', build.output)
    return finish()
  }

  console.log(`[build-watch] build OK (${Date.now() - startedAt}ms)`)
  finish()
}

function finish() {
  building = false
  if (pending) {
    pending = false
    build()
  }
}

function scheduleBuild() {
  clearTimeout(debounceTimer)
  debounceTimer = setTimeout(build, 300)
}

build()
watch(srcDir, { recursive: true }, (_event, filename) => {
  if (!filename) return
  scheduleBuild()
})
console.log(`[build-watch] watching ${srcDir} for changes...`)
