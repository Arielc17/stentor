export type Stentor = { hasAnswer: boolean }

declare module 'claude-code' {
  interface PluginState {
    'stentor': { hasAnswer: boolean }
  }
}
