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
import { writeFileSync, appendFileSync, existsSync } from "fs"
import { spawn } from "child_process"

function anjadevRoot() {
  if (process.env.ANJADEV_DIR) return process.env.ANJADEV_DIR
  // plugin in <root>/.opencode/plugin/anja.js → root due livelli sopra
  return resolve(import.meta.dir, "..", "..")
}

const HOOKS = join(anjadevRoot(), "hooks")
const PY = process.env.ANJA_PYTHON || "python3"

// Diagnostica opt-in (ANJA_OC_DEBUG=1): traccia loading + hook su /tmp/anja-opencode.log.
const DEBUG = !!process.env.ANJA_OC_DEBUG
function dbg(msg) {
  if (!DEBUG) return
  try { appendFileSync("/tmp/anja-opencode.log", `[${new Date().toISOString()}] ${msg}\n`) } catch {}
}

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
  dbg(`loaded · dir=${directory} · hooks=${HOOKS} · session_end=${existsSync(join(HOOKS, "session_end.py"))}`)

  // stdin via child_process (la Bun shell non espone un .stdin() concatenabile):
  // gli hook Python leggono il payload da sys.stdin. Fire-and-forget, mai bloccante.
  const run = (script, payload) => new Promise((res) => {
    try {
      const p = spawn(PY, [join(HOOKS, script)], { cwd: directory, stdio: ["pipe", "ignore", "ignore"] })
      p.on("error", () => res())
      p.on("close", () => res())
      p.stdin.write(payload)
      p.stdin.end()
    } catch { res() }
  })

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
        dbg(`session.idle sid=${sid} items=${items.length} → session_end.py`)
      } catch (e) { dbg(`session.idle ERR ${e}`) }  /* best-effort: mai disturbare la sessione */
    },

    // Re-embed del wiki dopo un edit dentro .anjawiki/wiki (post_tool_use.py invariato).
    "tool.execute.after": async (input) => {
      const tool = (input?.tool || "").toLowerCase()
      if (!["write", "edit", "patch", "multiedit"].includes(tool)) return
      const args = input?.args || {}
      const fp = args.filePath || args.path || args.file || args.file_path
      dbg(`tool.execute.after tool=${tool} argKeys=${Object.keys(args)} fp=${fp}`)
      if (!fp || !String(fp).includes("/.anjawiki/wiki/")) return
      try {
        await run("post_tool_use.py", JSON.stringify({
          tool_name: tool === "write" ? "Write" : "Edit",
          tool_input: { file_path: fp },
        }))
        dbg(`re-embed → post_tool_use.py fp=${fp}`)
      } catch (e) { dbg(`tool.after ERR ${e}`) }  /* fire-and-forget */
    },

    // Context injection best-effort: al primo messaggio, prepende l'output di
    // session_start.py al prompt utente (OpenCode non ha un hook che scrive nel system).
    "chat.message": async (input, output) => {
      const sid = input?.sessionID
      if (!sid || injected[sid]) return
      injected[sid] = true
      try {
        const ctx = (await $`${PY} ${join(HOOKS, "session_start.py")}`.cwd(directory).quiet().nothrow().text()).trim()
        const ok = ctx && Array.isArray(output?.parts)
        if (ok) output.parts.unshift({ type: "text", text: `[anja]\n${ctx}\n` })
        dbg(`chat.message sid=${sid} ctxlen=${ctx.length} parts=${Array.isArray(output?.parts)} injected=${!!ok}`)
      } catch (e) { dbg(`chat.message ERR ${e}`) }  /* best-effort */
    },
  }
}
