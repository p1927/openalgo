/**
 * Clipboard utility with a fallback for when the async Clipboard API is
 * unavailable or rejects.
 *
 * `navigator.clipboard` is only defined in a secure context (https, or
 * http on `localhost`/`127.0.0.1`) — reached over a LAN IP, a tunnel, or
 * any non-loopback http origin, `navigator.clipboard` is `undefined` and
 * `.writeText` throws immediately. It can also reject when the document
 * isn't focused (e.g. DevTools just took focus) even on a secure origin.
 * Either way the caller sees a generic failure with no indication why.
 *
 * `copyToClipboard` tries the async API first and falls back to the
 * legacy `document.execCommand('copy')` path (a hidden, off-screen
 * textarea + select + execCommand) so a copy click still works in those
 * cases instead of silently failing.
 */

function copyWithExecCommand(text: string): boolean {
  const textarea = document.createElement('textarea')
  textarea.value = text
  // Keep it out of the visible viewport and out of the tab order, but
  // still selectable — off-screen positioning (not display:none/hidden)
  // is required for execCommand('copy') to have something to select.
  textarea.style.position = 'fixed'
  textarea.style.top = '0'
  textarea.style.left = '-9999px'
  textarea.setAttribute('readonly', '')
  textarea.setAttribute('aria-hidden', 'true')
  document.body.appendChild(textarea)

  const previousActiveElement = document.activeElement as HTMLElement | null
  textarea.select()
  textarea.setSelectionRange(0, textarea.value.length)

  let succeeded = false
  try {
    succeeded = document.execCommand('copy')
  } catch {
    succeeded = false
  } finally {
    document.body.removeChild(textarea)
    previousActiveElement?.focus?.()
  }
  return succeeded
}

/**
 * Copy `text` to the clipboard, returning whether it succeeded. Never
 * throws — callers can rely on the boolean instead of a try/catch.
 */
export async function copyToClipboard(text: string): Promise<boolean> {
  if (navigator.clipboard?.writeText) {
    try {
      await navigator.clipboard.writeText(text)
      return true
    } catch {
      // Falls through to the execCommand fallback below — covers the
      // "API exists but write was rejected" case (e.g. unfocused
      // document), not just the "API doesn't exist" case.
    }
  }
  return copyWithExecCommand(text)
}
