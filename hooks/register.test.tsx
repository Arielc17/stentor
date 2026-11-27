import type { On } from 'claude-code'
import { expect, mock, test } from 'claude-code/testing'
import type { Engine, MockClock } from 'claude-code/testing'

const PLUGIN = 'stentor'
const SESSION = 'sesion-1'
const SURFACE = 'terminal' as const
const LINUX_ENV = { HOME: '/home/ana' }
const LINUX_PYTHON = '/home/ana/.local/share/stentor/venv/bin/python3'

type Call = { argv: readonly string[]; stdin?: string; env?: Record<string, string> }

type Row = { uuid: string; text: string }

type World = { calls: Call[]; toasts: string[]; checked: string[] }

/** Stands in for the machine, the session and stentor.py, recording each call to it. */
function world(
  on: On,
  rows: Row[],
  {
    env = LINUX_ENV,
    installed = true,
    exit = { exitCode: 0, stderr: '' },
  }: { env?: Record<string, string>; installed?: boolean; exit?: { exitCode: number; stderr: string } } = {},
): World {
  const found: World = { calls: [], toasts: [], checked: [] }
  mock.env(on, env)
  on('fs.exists', (_, e) => {
    found.checked.push(e.path)

    return { value: installed }
  })
  on('ui.toast', (_, e) => {
    found.toasts.push(e.text)

    return { value: undefined }
  })
  on('session.id', () => ({ value: SESSION }))
  on('turn.complete', (_, e) => ({ text: e.answer }))
  on('session.messages', () => ({
    value: rows.map(row => ({ role: 'assistant' as const, text: row.text, toolUses: [], uuid: row.uuid })),
  }))
  on('process.run', (_, e) => {
    found.calls.push({ argv: e.argv, stdin: e.init?.stdin, env: e.init?.env })

    return {
      value: { ...exit, stdout: 'ef_dora (voz de mujer)\n', isStdoutTruncated: false, isStderrTruncated: false },
    }
  })

  return found
}

/** What reached stentor.py, without the interpreter and script paths. */
const sent = (calls: Call[]) => calls.map(({ argv, stdin }) => ({ argv: argv.slice(2), stdin }))

type Shown = { first: number; last: number; of: number } | null | undefined

async function message($: Engine, id: string, text: string, onScreen: Shown) {
  return $.ui.mount({
    plugin: PLUGIN,
    surface: SURFACE,
    component: 'AssistantMessage',
    requestId: id,
    props: onScreen === undefined ? { text, isFirstOfReply: true } : { text, isFirstOfReply: true, onScreen },
  })
}

async function band($: Engine) {
  return $.ui.mount({
    plugin: PLUGIN,
    surface: SURFACE,
    component: 'AbovePrompt',
    props: { hasSurvey: false } as never,
  })
}

async function pressOne($: Engine) {
  const row = await band($)
  await row.press({ key: 'listen' })
  await row.unmount()
}

test('scrolled up, key 1 reads the reply on screen, not the newest', async ($, on) => {
  const { calls } = world(on, [
    { uuid: 'a', text: 'Primera respuesta' },
    { uuid: 'b', text: 'Última respuesta' },
  ])
  await message($, 'a', 'Primera respuesta', { first: 0, last: 9, of: 10 })
  await message($, 'b', 'Última respuesta', null)

  await pressOne($)

  expect(sent(calls)).toEqual([
    { argv: ['save', `${SESSION}-picked`], stdin: 'Primera respuesta' },
    { argv: ['speak', `${SESSION}-picked`], stdin: undefined },
  ])
})

test('at the bottom, key 1 reads the newest reply', async ($, on) => {
  const { calls } = world(on, [
    { uuid: 'a', text: 'Primera respuesta, larga' },
    { uuid: 'b', text: 'Corta' },
  ])
  await message($, 'a', 'Primera respuesta, larga', { first: 0, last: 30, of: 31 })
  await message($, 'b', 'Corta', { first: 0, last: 2, of: 3 })

  await pressOne($)

  expect(sent(calls)).toEqual([
    { argv: ['save', SESSION], stdin: 'Corta' },
    { argv: ['speak', SESSION], stdin: undefined },
  ])
})

test('of two older replies on screen, key 1 reads the one taking more rows', async ($, on) => {
  const { calls } = world(on, [
    { uuid: 'a', text: 'Arriba' },
    { uuid: 'b', text: 'Medio' },
    { uuid: 'c', text: 'Abajo' },
  ])
  await message($, 'a', 'Arriba', { first: 8, last: 10, of: 11 })
  await message($, 'b', 'Medio', { first: 0, last: 14, of: 15 })
  await message($, 'c', 'Abajo', { first: 0, last: 3, of: 20 })

  await pressOne($)

  expect(sent(calls)[0]).toEqual({ argv: ['save', `${SESSION}-picked`], stdin: 'Medio' })
})

test('without screen positions (main screen layout), key 1 reads the newest reply', async ($, on) => {
  const { calls } = world(on, [
    { uuid: 'a', text: 'Primera' },
    { uuid: 'b', text: 'Última' },
  ])
  await message($, 'a', 'Primera', undefined)
  await message($, 'b', 'Última', undefined)

  await pressOne($)

  expect(sent(calls)).toEqual([
    { argv: ['save', SESSION], stdin: 'Última' },
    { argv: ['speak', SESSION], stdin: undefined },
  ])
})

test('the play mark on a reply reads that reply', async ($, on) => {
  const { calls } = world(on, [
    { uuid: 'a', text: 'Primera' },
    { uuid: 'b', text: 'Última' },
  ])
  const row = await message($, 'a', 'Primera', { first: 0, last: 3, of: 4 })

  await row.press({ key: 'play-this' })

  expect(sent(calls)).toEqual([
    { argv: ['save', `${SESSION}-picked`], stdin: 'Primera' },
    { argv: ['speak', `${SESSION}-picked`], stdin: undefined },
  ])
  expect(await row.keepsBeneath()).toBe(true)
})

test('keys 2 and 3 stop and switch the voice, which a toast names', async ($, on) => {
  const { calls, toasts } = world(on, [])
  const row = await band($)
  await row.press({ key: 'stop' })
  await row.press({ key: 'switch' })

  expect(sent(calls).map(call => call.argv)).toEqual([['stop'], ['switch']])
  expect(toasts).toEqual(['Ahora: ef_dora (voz de mujer)'])
})

test('on Linux and macOS it runs the bundled stentor.py with the Python in the data folder', async ($, on) => {
  const { calls, checked } = world(on, [])
  const row = await band($)
  await row.press({ key: 'stop' })

  expect(checked).toEqual([LINUX_PYTHON])
  expect(calls[0]?.argv[0]).toBe(LINUX_PYTHON)
  expect(calls[0]?.argv[1]).toMatch(/\/scripts\/stentor\.py$/)
  expect(calls[0]?.env).toEqual({ STENTOR_VOICES: 'em_alex,ef_dora', STENTOR_SPEED: '1' })
})

test('on Windows it uses the Python under LOCALAPPDATA', async ($, on) => {
  const { calls } = world(on, [], { env: { LOCALAPPDATA: 'C:\\Users\\ana\\AppData\\Local' } })
  const row = await band($)
  await row.press({ key: 'stop' })

  expect(calls[0]?.argv[0]).toBe('C:\\Users\\ana\\AppData\\Local\\stentor\\venv\\Scripts\\python.exe')
  expect(calls[0]?.argv[1]).toMatch(/\\scripts\\stentor\.py$/)
})

test('XDG_DATA_HOME moves the data folder', async ($, on) => {
  const { calls } = world(on, [], { env: { HOME: '/home/ana', XDG_DATA_HOME: '/datos' } })
  const row = await band($)
  await row.press({ key: 'stop' })

  expect(calls[0]?.argv[0]).toBe('/datos/stentor/venv/bin/python3')
})

test('before the voices are installed, a key says how to install them and runs nothing', async ($, on) => {
  const { calls, toasts } = world(on, [{ uuid: 'a', text: 'Hola' }], { installed: false })
  await pressOne($)

  expect(calls).toEqual([])
  expect(toasts).toEqual(['Falta instalar las voces: escribe /stentor:install'])
})

type Piece = { stream: 'stdout' | 'stderr'; text: string }

type Installer = { spawned: string[][]; statuses: (string | undefined)[]; toasts: string[]; clock: MockClock }

/** Stands in for the machine's Pythons and install.py: who was started, and what showed. */
function installer(
  on: On,
  {
    env = LINUX_ENV,
    missing = [],
    pieces = [],
    code = 0,
    waitMs = 0,
  }: { env?: Record<string, string>; missing?: string[]; pieces?: Piece[]; code?: number; waitMs?: number } = {},
): Installer {
  const found: Installer = { spawned: [], statuses: [], toasts: [], clock: mock.clock(on) }
  mock.env(on, env)
  on('ui.toast', (_, e) => {
    found.toasts.push(e.text)

    return { value: undefined }
  })
  on('ui.status', (_, e) => {
    found.statuses.push(e.text)

    return { value: undefined }
  })
  on('process.spawn', async function* (_, e) {
    found.spawned.push([...e.argv])
    if (missing.includes(e.argv[0] ?? '')) {
      return { deny: `spawn ${e.argv[0]} ENOENT` }
    }
    if (waitMs > 0) {
      await found.clock.sleep(waitMs)
    }
    for (const piece of pieces) {
      yield piece
    }

    return { value: { code, signal: null } }
  })

  return found
}

const out = (text: string): Piece => ({ stream: 'stdout', text })

/** Types /stentor:install and lets the install it starts run as far as it can. */
async function runInstall($: Engine, { clock }: Installer) {
  const result = await $.command.run({
    command: 'install',
    args: '',
    origin: { kind: 'composer' },
    presentation: { isFullscreen: true, columns: 120 },
  })
  await clock.settle()

  return result
}

test('/stentor:install runs install.py with the system Python and answers at once', async ($, on) => {
  const found = installer(on)
  const result = await runInstall($, found)

  expect(result.text).toBe('Instalando las voces de Stentor (una sola vez, unos 120 MB). El avance se ve debajo del cuadro de texto.')
  expect(found.spawned).toHaveLength(1)
  expect(found.spawned[0]?.[0]).toBe('python3')
  expect(found.spawned[0]?.[1]).toMatch(/\/scripts\/install\.py$/)
  expect(found.spawned[0]?.[2]).toBe('--progress')
})

test('/stentor:install shows each step under the prompt, then says the voices are ready', async ($, on) => {
  const found = installer(on, {
    pieces: [
      out('{"step": "environment"}\n{"step": "pack'),
      out('ages"}\n'),
      { stream: 'stderr', text: 'Installing kokoro-onnx>=0.4.7,<0.5…\n' },
      out('{"step": "voices", "file": 1, "of": 2, "percent": 60}\n'),
      out('{"step": "done"}\n'),
    ],
  })
  await runInstall($, found)

  expect(found.statuses).toEqual([
    'Stentor: preparando Python…',
    'Stentor: instalando kokoro-onnx…',
    'Stentor: descargando voces (1 de 2)… 60%',
    undefined,
  ])
  expect(found.toasts).toEqual(['Voces instaladas: presiona 1 para escuchar'])
})

test('/stentor:install speaks English with English voices', { options: { voices: 'af_heart' } }, async ($, on) => {
  const found = installer(on, {
    pieces: [out('{"step": "voices", "file": 2, "of": 2, "percent": 5}\n'), out('{"step": "done"}\n')],
  })
  const result = await runInstall($, found)

  expect(result.text).toBe('Installing the Stentor voices (once, about 120 MB). The progress shows under the prompt.')
  expect(found.statuses[0]).toBe('Stentor: downloading voices (2 of 2)… 5%')
  expect(found.toasts).toEqual(['Voices installed: press 1 to listen'])
})

test('when install.py fails, its last message says why and the progress line goes away', async ($, on) => {
  const found = installer(on, {
    pieces: [
      out('{"step": "voices", "file": 2, "of": 2, "percent": 40}\n'),
      { stream: 'stderr', text: 'Traceback…\nCould not download voices-v1.0.bin (timed out). Check the connection and run this installer again.\n' },
    ],
    code: 1,
  })
  await runInstall($, found)

  expect(found.statuses.at(-1)).toBeUndefined()
  expect(found.toasts).toHaveLength(1)
  expect(found.toasts[0]).toMatch(
    /^No se pudieron instalar las voces: Could not download voices-v1\.0\.bin \(timed out\)\. Check the connection and run this installer again\.\nTambién puedes ejecutar en una terminal: python3 ".*\/scripts\/install\.py"$/,
  )
})

test('without python3, /stentor:install tries python', async ($, on) => {
  const found = installer(on, { missing: ['python3'], pieces: [out('{"step": "done"}\n')] })
  await runInstall($, found)

  expect(found.spawned.map(argv => argv[0])).toEqual(['python3', 'python'])
  expect(found.toasts).toEqual(['Voces instaladas: presiona 1 para escuchar'])
})

test('on Windows, /stentor:install starts the py launcher with Python 3', async ($, on) => {
  const found = installer(on, { env: { LOCALAPPDATA: 'C:\\Users\\ana\\AppData\\Local' } })
  await runInstall($, found)

  expect(found.spawned[0]?.slice(0, 2)).toEqual(['py', '-3'])
  expect(found.spawned[0]?.[2]).toMatch(/\\scripts\\install\.py$/)
})

test('with no Python at all, /stentor:install says where to get one', async ($, on) => {
  const found = installer(on, { missing: ['python3', 'python'] })
  await runInstall($, found)

  expect(found.spawned).toHaveLength(2)
  expect(found.toasts).toEqual(['Stentor necesita Python 3.10 o más nuevo: https://www.python.org/downloads/'])
})

test('a second /stentor:install while one runs starts nothing new', async ($, on) => {
  const found = installer(on, { pieces: [out('{"step": "done"}\n')], waitMs: 1000 })
  await runInstall($, found)
  const second = await runInstall($, found)

  expect(second.text).toBe('Las voces ya se están instalando; el avance se ve debajo del cuadro de texto.')
  expect(found.spawned).toHaveLength(1)

  await found.clock.advance(1000)
  await runInstall($, found)

  expect(found.spawned).toHaveLength(2)
})

test('voices and speed from the settings reach stentor.py, and English voices get English labels', { options: { voices: 'af_heart,am_michael', speed: 1.2 } }, async ($, on) => {
  const { calls, toasts } = world(on, [])
  const row = await band($)

  expect(await row.find({ type: 'Button', key: 'listen' })).toBeDefined()
  await row.press({ key: 'switch' })

  expect(calls[0]?.env).toEqual({ STENTOR_VOICES: 'af_heart,am_michael', STENTOR_SPEED: '1.2' })
  expect(toasts[0]).toMatch(/^Now: /)
})

test('the labels follow the first named voice, as stentor.py does', { options: { voices: ' , ef_dora,af_heart' } }, async ($, on) => {
  const { toasts } = world(on, [])
  const row = await band($)

  await row.press({ key: 'switch' })

  expect(toasts[0]).toMatch(/^Ahora: /)
})

async function finishTurn($: Engine, answer: string) {
  await $.turn.complete({ answer, durationMs: 1, isAborted: false, turnId: 't1', reason: 'completed' } as never)
}

test('a finished answer is saved for key 1', async ($, on) => {
  const { calls } = world(on, [])
  await finishTurn($, 'Respuesta nueva')

  expect(sent(calls)).toEqual([{ argv: ['save', SESSION], stdin: 'Respuesta nueva' }])
})

test('before the voices are installed, finished answers pass quietly, without a toast each turn', async ($, on) => {
  const { calls, toasts } = world(on, [], { installed: false })
  await finishTurn($, 'Respuesta nueva')

  expect(calls).toEqual([])
  expect(toasts).toEqual([])
})

test('when stentor.py fails, its message shows in a toast', async ($, on) => {
  const { toasts } = world(on, [{ uuid: 'a', text: 'Hola' }], {
    exit: { exitCode: 1, stderr: 'Traceback…\nLa voz ef_dorra no existe; revisa Voices en /plugin\n' },
  })
  await pressOne($)

  expect(toasts).toEqual(['La voz ef_dorra no existe; revisa Voices en /plugin'])
})

test('an empty XDG_DATA_HOME counts as unset', async ($, on) => {
  const { calls } = world(on, [], { env: { HOME: '/home/ana', XDG_DATA_HOME: '' } })
  const row = await band($)
  await row.press({ key: 'stop' })

  expect(calls[0]?.argv[0]).toBe(LINUX_PYTHON)
})
