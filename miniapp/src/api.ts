export type Language = 'ru' | 'en'
export type MenuModifier = { id: number; name: string; kind: string }
export type MenuItem = {
  id: number; category_id: number; name: string; description: string; ingredients: string
  image_url: string | null; is_alcoholic: boolean; modifiers: MenuModifier[]
}
export type Category = { id: number; name: string; sort_order: number; items: MenuItem[] }
export type Menu = { event_id: number; categories: Category[] }
export type CartItem = {
  id: number; menu_item_id: number; name: string; quantity: number
  modifier_ids: number[]; modifiers: string[]; comment: string
}
export type Cart = { id: number; event_id: number; items: CartItem[]; total_quantity: number }
export type Order = {
  id: number; public_number: string; status: string; version: number; created_at: string
  status_automatically: boolean; completed_automatically: boolean
  comment: string; items: { name: string; quantity: number; modifiers: string[]; comment: string }[]
  new_achievements?: string[]
}
export type Achievement = {
  code: string; name: string; description: string; symbol: string; awarded_at: string | null
}
export type Achievements = {
  achievements: Achievement[]
  quiz: { question: string; options: { id: string; label: string }[] }
}
export type SecretOffer = {
  id: number; riddle: string; available_from: string; available_until: string
  remaining: number; available: boolean; unavailable_reason: 'sold_out' | 'upcoming' | 'unavailable' | null
  unlocked: boolean; item: MenuItem | null
}
export type SecretMenu = { offers: SecretOffer[] }
export type Bootstrap = {
  user: { display_name: string; language: Language }
  event: null | { id: number; name: string; orders_enabled: boolean; max_items_per_order: number; max_same_item: number }
  cart_count: number; active_order: Order | null; latest_order: Order | null
}
export type Leaderboard = {
  top: { rank: number; name: string; score: number; is_me: boolean }[]
  me: { rank: number; name: string; score: number; is_me: boolean } | null
  show_telegram_name: boolean; updated_at: string
}

export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message) }
}

export async function api<T>(path: string, initData: string, method = 'GET', body?: unknown): Promise<T> {
  const response = await fetch(`/api/v1/miniapp${path}`, {
    method,
    headers: { 'X-Telegram-Init-Data': initData, ...(body ? { 'Content-Type': 'application/json' } : {}) },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
  })
  if (!response.ok) {
    const payload = await response.json().catch(() => null)
    const detail = payload?.detail
    throw new ApiError(response.status, detail?.message || detail || payload?.message || `HTTP ${response.status}`)
  }
  return response.json() as Promise<T>
}
