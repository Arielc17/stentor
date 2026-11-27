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
    installing: 'Instalando las voces de Stentor (una sola vez, unos 120 MB). El avance se ve debajo del cuadro de texto.',
    alreadyInstalling: 'Las voces ya se están instalando; el avance se ve debajo del cuadro de texto.',
    stepEnvironment: 'Stentor: preparando Python…',
    stepPackages: 'Stentor: instalando kokoro-onnx…',
    stepVoices: (file: number, of: number, percent: number) => `Stentor: descargando voces (${file} de ${of})… ${percent}%`,
    installed: 'Voces instaladas: presiona 1 para escuchar',
    installFailed: 'No se pudieron instalar las voces: ',
    inTerminal: 'También puedes ejecutar en una terminal: ',
    noPython: 'Stentor necesita Python 3.10 o más nuevo: https://www.python.org/downloads/',
    commandDescription: 'Instala las voces de Stentor',
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
    installing: 'Installing the Stentor voices (once, about 120 MB). The progress shows under the prompt.',
    alreadyInstalling: 'The voices are already being installed; the progress shows under the prompt.',
    stepEnvironment: 'Stentor: preparing Python…',
    stepPackages: 'Stentor: installing kokoro-onnx…',
    stepVoices: (file: number, of: number, percent: number) => `Stentor: downloading voices (${file} of ${of})… ${percent}%`,
    installed: 'Voices installed: press 1 to listen',
    installFailed: 'Could not install the voices: ',
    inTerminal: 'You can also run in a terminal: ',
    noPython: 'Stentor needs Python 3.10 or newer: https://www.python.org/downloads/',
    commandDescription: 'Installs the Stentor voices',
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

type Labels = (typeof LABELS)['es']

/** The Python that runs install.py, in the order to try: the first one that starts. */
type Paths = { script: string; python: string; installer: string; systemPythons: string[][]; installLine: string }

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
      installer: `${root}\\scripts\\install.py`,
      systemPythons: [['py', '-3'], ['python']],
      installLine: `py -3 "${root}\\scripts\\install.py"`,
    }
  }
  const base = set(await $.env.get('XDG_DATA_HOME')) ?? `${set(await $.env.get('HOME')) ?? '~'}/.local/share`
  const data = custom ?? `${base}/stentor`
  const isWindows = localAppData !== undefined

  return {
    script: `${root}/scripts/stentor.py`,
    python: isWindows ? `${data}\\venv\\Scripts\\python.exe` : `${data}/venv/bin/python3`,
    installer: `${root}/scripts/install.py`,
    systemPythons: isWindows ? [['py', '-3'], ['python']] : [['python3'], ['python']],
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

type Step = { step?: string; file?: number; of?: number; percent?: number }

/** The progress line for one step install.py --progress reported, or undefined for none. */
function stepLine(text: Labels, { step, file = 1, of = 1, percent = 0 }: Step): string | undefined {
  switch (step) {
    case 'environment':
      return text.stepEnvironment
    case 'packages':
      return text.stepPackages
    case 'voices':
      return text.stepVoices(file, of, percent)
    default:
      return undefined
  }
}

/**
 * Runs install.py --progress with one system Python, showing each step on the
 * status line; 'not-started' when that Python is not on this machine.
 */
async function follow($: EngineInterface, text: Labels, argv: string[], installLine: string): Promise<'not-started' | 'ended'> {
  const child = $.process.spawn({ argv, env: { PYTHONUNBUFFERED: '1' } })
  let started = false
  let pending = ''
  let errors = ''
  let code: number | null = null
  try {
    for (;;) {
      const piece = await child.next()
      started = true
      if (piece.done) {
        code = piece.value.code
        break
      }
      if (piece.value.stream === 'stderr') {
        errors = (errors + piece.value.text).slice(-4000)
        continue
      }
      const lines = (pending + piece.value.text).split('\n')
      pending = lines.pop() ?? ''
      for (const line of lines) {
        let step: Step
        try {
          step = JSON.parse(line) as Step
        } catch {
          continue
        }
        const shown = stepLine(text, step)
        if (shown !== undefined) {
          $.ui.status(shown)
        }
      }
    }
  } catch (error) {
    if (!started) {
      return 'not-started'
    }
    errors += `\n${String(error)}`
  }
  $.ui.status(undefined)
  if (code === 0) {
    $.ui.toast(text.installed, { timeoutMs: 10000 })
  } else {
    const reason = errors.trim().split('\n').at(-1)?.trim() ?? ''
    $.ui.toast(`${text.installFailed}${reason}\n${text.inTerminal}${installLine}`, { timeoutMs: 30000 })
  }

  return 'ended'
}

async function install($: EngineInterface, text: Labels) {
  const { installer, systemPythons, installLine } = await paths($)
  for (const python of systemPythons) {
    if ((await follow($, text, [...python, installer, '--progress'], installLine)) === 'ended') {
      return
    }
  }
  $.ui.toast(text.noPython, { timeoutMs: 15000 })
}

/** One install at a time; a reload ends the running one with the module. */
let installing = false

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

  // The install runs on after the command answers, so the person can keep working.
  on('command.run', { command: 'install' }, async $ => {
    if (installing) {
      return { text: text.alreadyInstalling, isTextDisplayOnly: true }
    }
    installing = true
    void install($, text).finally(() => {
      installing = false
    })

    return { text: text.installing, isTextDisplayOnly: true }
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
