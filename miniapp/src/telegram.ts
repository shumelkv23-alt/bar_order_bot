type BackButton = { show: () => void; hide: () => void; onClick: (callback: () => void) => void; offClick: (callback: () => void) => void }
type WebApp = {
  initData: string; ready: () => void; expand: () => void
  setHeaderColor?: (color: string) => void; setBackgroundColor?: (color: string) => void
  BackButton?: BackButton
}
declare global { interface Window { Telegram?: { WebApp: WebApp } } }

export function telegram(): WebApp | undefined { return window.Telegram?.WebApp }

export function initTelegram(): string {
  const app = telegram()
  if (!app) return ''
  app.setHeaderColor?.('#060b1a')
  app.setBackgroundColor?.('#060b1a')
  app.ready()
  app.expand()
  return app.initData
}
