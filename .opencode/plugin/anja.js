/**
 * anja — OpenCode adapter (F-OpenCodeAdapter)
 *
 * Aggancia i lifecycle di OpenCode agli hook Python di anjadev SENZA riscriverne la
 * logica e SENZA toccare il path Claude Code / Codex / Grok: il plugin TRADUCE i dati
 * di OpenCode nel formato che gli script CC già parsano, poi li invoca via subprocess.
 * Tutto il codice Python condiviso resta invariato → zero regressioni sugli altri harness.
 *
 * Automatismi replicati:
 *   - event(session.idle)  → session_end.py    journal di sessione (debounced 60s)
 *   - tool.execute.after   → post_tool_use.py  re-embed del wiki dopo edit di .anjawiki/wiki
 *   - chat.message         → session_start.py  context injection (best-effort, 1×/sessione)
 *
 * Install: symlink/copia questo file in `.opencode/plugin/` del progetto, oppure in
 *   `~/.config/opencode/plugin/`. Se il plugin vive fuori dal repo, imposta ANJADEV_DIR.
 *
 * NB: i campi esatti dell'API OpenCode (event payload, Part, tool args) sono accedibili
 * in modo DIFENSIVO (più fallback) — da validare sul campo quando OpenCode è in uso. Il
 * contratto verso gli script Python è invece testato (tests/test_opencode_adapter.py).
 */
import { join, resolve } from "path"
import { tmpdir } from "os"
import { writeFileSync } from "fs"

function anjadevRoot() {
  if (process.env.ANJADEV_DIR) return process.env.ANJADEV_DIR
  // plugin in <root>/.opencode/plugin/anja.js → root due livelli sopra
  return resolve(import.meta.dir, "..", "..")
}

const HOOKS = join(anjadevRoot(), "hooks")
const PY = process.env.ANJA_PYTHON || "python3"

function sessionIdOf(ev) {
  const p = ev?.properties || ev || {}
  return p.sessionID || p.session?.id || p.info?.id || ev?.sessionID || null
}

function isoOf(info) {
  const t = info?.time?.created || info?.time?.completed || info?.created || Date.now()
  try { return new Date(typeof t === "number" ? t : Date.parse(t)).toISOString() }
  catch { return new Date().toISOString() }
}

// Traduce i messaggi OpenCode (client.session.messages → [{info, parts}]) nel JSONL
// che parse_transcript di session_end.py già legge: una riga per messaggio con
// {timestamp, type, message:{role, content}}, content array di {type:text|tool_use}.
export function toCcTranscript(items) {
  const lines = []
  for (const it of items || []) {
    const info = it?.info || it || {}
    const role = info.role === "assistant" ? "assistant" : "user"
    const ts = isoOf(info)
    const parts = it?.parts || []
    if (role === "user") {
      const text = parts.filter(p => p?.type === "text").map(p => p.text || "").join("\n").trim()
      lines.push(JSON.stringify({ timestamp: ts, type: "user", message: { role: "user", content: text } }))
    } else {
      const content = []
      for (const p of parts) {
        if (p?.type === "text" && p.text) content.push({ type: "text", text: p.text })
        else if (p?.type === "tool" || p?.tool || p?.type === "tool-invocation")
          content.push({ type: "tool_use", name: p.tool || p.name || "tool" })
      }
      lines.push(JSON.stringify({ timestamp: ts, type: "assistant", message: { role: "assistant", content } }))
    }
  }
  return lines.join("\n") + "\n"
}

export const AnjaPlugin = async ({ client, directory, $ }) => {
  const lastJournal = {}   // debounce: session.idle scatta a ogni turno, non solo a fine sessione
  const injected = {}      // context injection una volta per sessione

  const run = (script, payload) =>
    $`${PY} ${join(HOOKS, script)}`.cwd(directory).stdin(payload).quiet().nothrow()

  return {
    // Journal di sessione: tradotto e passato a session_end.py (invariato).
    event: async ({ event }) => {
      if (event?.type !== "session.idle") return
      const sid = sessionIdOf(event)
      if (!sid) return
      const now = Date.now()
      if (lastJournal[sid] && now - lastJournal[sid] < 60000) return
      lastJournal[sid] = now
      try {
        const res = await client.session.messages({ path: { id: sid } })
        const items = res?.data ?? res ?? []
        const tpath = join(tmpdir(), `anja-oc-${sid}.jsonl`)
        writeFileSync(tpath, toCcTranscript(items), "utf-8")
        await run("session_end.py", JSON.stringify({
          session_id: sid, transcript_path: tpath, cwd: directory,
          hook_event_name: "SessionEnd", reason: "other",
        }))
      } catch { /* best-effort: mai disturbare la sessione */ }
    },

    // Re-embed del wiki dopo un edit dentro .anjawiki/wiki (post_tool_use.py invariato).
    "tool.execute.after": async (input) => {
      const tool = (input?.tool || "").toLowerCase()
      if (!["write", "edit", "patch", "multiedit"].includes(tool)) return
      const args = input?.args || {}
      const fp = args.filePath || args.path || args.file || args.file_path
      if (!fp || !String(fp).includes("/.anjawiki/wiki/")) return
      try {
        await run("post_tool_use.py", JSON.stringify({
          tool_name: tool === "write" ? "Write" : "Edit",
          tool_input: { file_path: fp },
        }))
      } catch { /* fire-and-forget */ }
    },

    // Context injection best-effort: al primo messaggio, prepende l'output di
    // session_start.py al prompt utente (OpenCode non ha un hook che scrive nel system).
    "chat.message": async (input, output) => {
      const sid = input?.sessionID
      if (!sid || injected[sid]) return
      injected[sid] = true
      try {
        const ctx = (await $`${PY} ${join(HOOKS, "session_start.py")}`.cwd(directory).quiet().nothrow().text()).trim()
        if (ctx && Array.isArray(output?.parts)) {
          output.parts.unshift({ type: "text", text: `[anja]\n${ctx}\n` })
        }
      } catch { /* best-effort */ }
    },
  }
}
