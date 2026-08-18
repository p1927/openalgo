import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { compression } from 'vite-plugin-compression2'
import path from 'path'

// https://vite.dev/config/
export default defineConfig(({ command }) => ({
  // Serve this SPA under the gateway path /apps/openalgo/ — but only for the
  // BUILD (`vite build`, `command === 'build'`). The prebuilt dist/ bundle
  // is served by Flask at both the bare origin (`:5001/`) and the gateway
  // prefix (`:8080/apps/openalgo/...`) via a Flask middleware that strips
  // `/apps/openalgo/` server-side before routing — but that stripping only
  // rewrites what Flask sees, not the browser's actual URL, so the SPA's
  // `<BrowserRouter>` (no `basename` set — it assumes it always runs at
  // root) only works because Flask always serves the *document* at root
  // and merely serves *assets* under the prefix. Setting `base` makes the
  // emitted asset paths prefix-aware so the browser resolves them under
  // either host: with default Vite base `/`, asset paths come out as
  // `/assets/index-...js`, which 404s when served under the gateway.
  //
  // The dev server (`vite`/`vite dev`, `command === 'serve'`, launched by
  // stack_start_openalgo_ui in scripts/stack_lib.sh for `trade dev`) has no
  // build-time asset-prefix problem to solve — it serves modules on demand.
  // But it DOES enforce `base` on the document itself: hitting bare `/`
  // 302-redirects to `/apps/openalgo/`, which breaks the same basename-less
  // router (the SPA's own NotFound route renders, since `BrowserRouter`
  // doesn't recognize `/apps/openalgo/` as a known path). Keeping `base: '/'`
  // in dev serves the document at root, matching how Flask already does it,
  // so `trade dev`'s Vite server can be embedded the same way Flask is.
  base: command === 'build' ? '/apps/openalgo/' : '/',
  plugins: [
    react(),
    tailwindcss(),
    // Emit pre-compressed .br and .gz next to each asset at build time.
    // CI force-commits frontend/dist/ to main, so these ship to every
    // deployment (incl. no-nginx laptop installs) without a Node step.
    // blueprints/react_app.py serves them when the client advertises the
    // encoding, falling back to the raw asset otherwise. Zero per-request
    // CPU; nginx passes Content-Encoding through without double-compressing.
    compression({ algorithms: ['brotliCompress', 'gzip'], exclude: [/\.(br|gz)$/], threshold: 1024 }),
  ],
  // plotly.js-dist-min's UMD wrapper has an unguarded `global.matchMedia`
  // reference. Vite 8 no longer shims Node's `global` in the browser, so the
  // /tools pages that load Plotly (StrategyBuilder, MaxPain, OI Tracker, etc.)
  // threw "global is not defined". Map `global` to the browser `globalThis`.
  define: {
    global: 'globalThis',
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
      '/socket.io': {
        target: 'http://localhost:5000',
        ws: true,
      },
      '/auth': {
        target: 'http://localhost:5000',
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    // Plotly core can legitimately produce a large shared chart chunk.
    // Keep the limit high enough for that known vendor cost while still
    // flagging any new app-code chunk that drifts above 1MB.
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        // Split the stable framework libs into their own long-cached chunk
        // so an app-code change doesn't bust react/router/query for returning
        // users, and the browser can fetch vendor + page chunks in parallel.
        // Vite already splits the heavy charting libs (plotly, lightweight-
        // charts) automatically, so we only carve out the framework core here.
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          if (/[\\/]node_modules[\\/](react|react-dom|react-router|react-router-dom|scheduler)[\\/]/.test(id)) {
            return 'react-vendor'
          }
          if (id.includes('tanstack/react-query')) return 'tanstack'
        },
      },
    },
  },
}))
