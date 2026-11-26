import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

// A reply picked by scroll or by its play mark is stored apart, so the newest one stays.
const PICKED = '-picked'
const DEFAULT_VOICES = 'em_alex,ef_dora'

const hasAnswer = atom({ plugin: 'stentor', key: 'hasAnswer' } as const, false)

type OnScreen = { first: number; last: number; of: number }
type Shown = { text: string; onScreen: OnScreen }
type Row = { role: string; text: string; uuid?: string }

const LABELS = {
  es: {
    band: 'Voz: ',
    listen: 'Escuchar',
    stop: 'Parar',
    switch: 'Cambiar voz',
    playThis: '▶ Escuchar',
    nothing: 'Todavía no hay respuesta para leer',
    now: 'Ahora: ',
    notInstalled: 'Falta instalar las voces: escribe /stentor:install',
    install: 'Para instalar las voces (una sola vez, unos 120 MB), ejecuta en una terminal:',
    installAfter: 'Luego reinicia Claude Code o usa /reload-plugins.',
    commandDescription: 'Muestra cómo instalar las voces de Stentor',
  },
  en: {
    band: 'Voice: ',
    listen: 'Listen',
    stop: 'Stop',
    switch: 'Switch voice',
    playThis: '▶ Listen',
    nothing: 'No answer to read yet',
    now: 'Now: ',
    notInstalled: 'Voices not installed yet: type /stentor:install',
    install: 'To install the voices (once, about 120 MB), run in a terminal:',
    installAfter: 'Then restart Claude Code or use /reload-plugins.',
    commandDescription: 'Shows how to install the Stentor voices',
  },
}

/**
 * The reply blocks the transcript shows now, by message id. The engine reports a
 * block again only when its visible rows change, so this keeps the last report.
 */
const shown = new Map<string, Shown>()

const visibleRows = (block: Shown) => block.onScreen.last - block.onScreen.first + 1
const endIsVisible = (block: Shown) => block.onScreen.last >= block.onScreen.of - 1

/** Where a drawn block sits in the conversation: by message id, else by its text. */
function position(rows: Row[], id: string, block: Shown): number {
  const byId = rows.findIndex(row => row.uuid === id)
  if (byId !== -1) {
    return byId
  }
  const text = block.text.trim()

  return text === '' ? -1 : rows.findLastIndex(row => row.text.includes(text))
}

/**
 * The block to read for key 1, or undefined to read the newest reply: nothing
 * reported on screen (the main screen layout), or the newest reply shown down to
 * its end, which is where the transcript sits until the person scrolls up.
 */
function blockOnScreen(rows: Row[]): string | undefined {
  const replies = rows.filter(row => row.role === 'assistant' && row.text.trim() !== '')
  const newest = replies.length - 1
  const candidates = [...shown].map(([id, block]) => ({ block, at: position(replies, id, block) }))
  if (candidates.length === 0) {
    return undefined
  }
  if (candidates.some(({ block, at }) => at === newest && endIsVisible(block))) {
    return undefined
  }
  // The most rows on screen wins; on a tie, the later reply.
  candidates.sort((a, b) => visibleRows(b.block) - visibleRows(a.block) || b.at - a.at)

  return candidates[0]?.block.text
}

type Paths = { script: string; python: string; installLine: string }

/** An environment variable's value, with an empty one counted as unset. */
const set = (value: string | undefined) => (value === '' ? undefined : value)

/** stentor.py ships with the plugin; its Python lives in the data folder, kept across updates. */
async function paths($: EngineInterface): Promise<Paths> {
  const root = $.plugin.root
  const custom = set(await $.env.get('STENTOR_DATA'))
  const localAppData = set(await $.env.get('LOCALAPPDATA'))
  if (localAppData !== undefined && custom === undefined) {
    const data = `${localAppData}\\stentor`

    return {
      script: `${root}\\scripts\\stentor.py`,
      python: `${data}\\venv\\Scripts\\python.exe`,
      installLine: `py -3 "${root}\\scripts\\install.py"`,
    }
  }
  const base = set(await $.env.get('XDG_DATA_HOME')) ?? `${set(await $.env.get('HOME')) ?? '~'}/.local/share`
  const data = custom ?? `${base}/stentor`
  const isWindows = localAppData !== undefined

  return {
    script: `${root}/scripts/stentor.py`,
    python: isWindows ? `${data}\\venv\\Scripts\\python.exe` : `${data}/venv/bin/python3`,
    installLine: `${isWindows ? 'py -3' : 'python3'} "${root}/scripts/install.py"`,
  }
}

type Settings = { env: Record<string, string>; notInstalled: string }

type Run = { stdin?: string; quiet?: boolean }

/**
 * Runs stentor.py; undefined when it could not run or failed. A key press says
 * why in a toast (voices missing, stentor.py's last error line); `quiet` runs,
 * made after every answer, say nothing.
 */
async function runStentor($: EngineInterface, settings: Settings, args: string[], run: Run = {}) {
  const { script, python } = await paths($)
  if (!(await $.fs.exists(python))) {
    if (!run.quiet) {
      $.ui.toast(settings.notInstalled)
    }

    return undefined
  }
  const init = run.stdin === undefined ? { env: settings.env } : { env: settings.env, stdin: run.stdin }
  const result = await $.process.run([python, script, ...args], init)
  if (result.exitCode !== 0) {
    const reason = result.stderr.trim().split('\n').at(-1)
    if (!run.quiet && reason) {
      $.ui.toast(reason)
    }

    return undefined
  }

  return result
}

export const register: Register = (on, options) => {
  const voices = typeof options.voices === 'string' && options.voices.trim() !== '' ? options.voices : DEFAULT_VOICES
  const speed = typeof options.speed === 'number' ? options.speed : 1
  // Like stentor.py, the first named voice sets the language.
  const firstVoice = voices.split(',').map(v => v.trim()).find(v => v !== '') ?? ''
  const text = firstVoice.startsWith('e') ? LABELS.es : LABELS.en
  const settings: Settings = {
    env: { STENTOR_VOICES: voices, STENTOR_SPEED: String(speed) },
    notInstalled: text.notInstalled,
  }

  on('session.start', async ($, e, next) => {
    shown.clear()
    await $.command.register({ name: 'install', description: text.commandDescription })

    return next(e)
  })

  on('command.run', { command: 'install' }, async $ => {
    const { installLine } = await paths($)

    return { text: `${text.install}\n\n  ${installLine}\n\n${text.installAfter}`, isTextDisplayOnly: true }
  })

  on('turn.complete', async ($, e, next) => {
    // Only the main conversation's finished answers, never a subagent's report.
    if (e.agentId === undefined && !e.isAborted && e.answer.trim() !== '') {
      const session = await $.session.id()
      if (await runStentor($, settings, ['save', session], { stdin: e.answer, quiet: true })) {
        await update($, hasAnswer, () => true)
      }
    }

    return next(e)
  })

  on('ui.render', { component: 'AssistantMessage' }, async ($, e, next) => {
    const { onScreen, text: blockText } = e.props
    if (onScreen) {
      shown.set(e.requestId, { text: blockText, onScreen })
    } else {
      shown.delete(e.requestId)
    }

    const { Box, Button } = $.ui.resolve(e)
    const listenToThis = async () => {
      const session = `${await $.session.id()}${PICKED}`
      if (await runStentor($, settings, ['save', session], { stdin: blockText })) {
        await runStentor($, settings, ['speak', session])
      }
    }

    return (
      <Box key={`stentor-${e.requestId}`} flexDirection="column">
        {await next(e)}
        <Box position="absolute" top={0} right={0} display="none" hover={{ display: 'flex' }}>
          <Button key="play-this" plain onPress={listenToThis}>
            {text.playThis}
          </Button>
        </Box>
      </Box>
    )
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    if (e.props.hasSurvey) {
      return next(e)
    }

    const { Box, Button, Text } = $.ui.resolve(e)
    const listenToNewest = async (rows: Row[]) => {
      const session = await $.session.id()
      // A resumed session has no answer saved yet: take the newest one in history.
      if (!(await read($, hasAnswer))) {
        const latest = rows.findLast(row => row.role === 'assistant' && row.text.trim() !== '')
        if (latest === undefined) {
          $.ui.toast(text.nothing)
          return
        }
        if (!(await runStentor($, settings, ['save', session], { stdin: latest.text }))) {
          return
        }
        await update($, hasAnswer, () => true)
      }
      await runStentor($, settings, ['speak', session])
    }
    const listen = async () => {
      const found = await $.session.messages({ last: 400 })
      const rows: Row[] = Array.isArray(found) ? found : []
      const picked = blockOnScreen(rows)
      if (picked === undefined) {
        await listenToNewest(rows)
        return
      }
      const session = `${await $.session.id()}${PICKED}`
      if (await runStentor($, settings, ['save', session], { stdin: picked })) {
        await runStentor($, settings, ['speak', session])
      }
    }
    const stop = async () => {
      await runStentor($, settings, ['stop'])
    }
    const switchVoice = async () => {
      const result = await runStentor($, settings, ['switch'])
      if (result) {
        $.ui.toast(`${text.now}${result.stdout.trim()}`)
      }
    }

    return (
      <Box>
        <Text dimColor>{text.band}</Text>
        <Button key="listen" hotkey="1" plain onPress={listen}>
          {text.listen}
        </Button>
        <Text> </Text>
        <Button key="stop" hotkey="2" plain onPress={stop}>
          {text.stop}
        </Button>
        <Text> </Text>
        <Button key="switch" hotkey="3" plain onPress={switchVoice}>
          {text.switch}
        </Button>
      </Box>
    )
  })
}
