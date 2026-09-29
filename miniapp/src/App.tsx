import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, ApiError, type Bootstrap, type Cart, type CartItem, type Language, type Leaderboard, type Menu, type MenuItem, type Order } from './api'
import { copy } from './i18n'
import { initTelegram, telegram } from './telegram'

type Screen = 'menu' | 'mystery' | 'top' | 'cart' | 'item' | 'order'
type Draft = { item: MenuItem; origin: 'menu' | 'mystery' | 'cart'; cartItemId?: number; quantity: number; modifierIds: number[]; comment: string; expectedQuantity?: number }
type MysteryResult = { item: MenuItem; can_reroll: boolean }

function Icon({ name, size = 20 }: { name: string; size?: number }) {
  return <svg width={size} height={size} aria-hidden="true" className="icon"><use href={`/static/panel-icons.svg#${name}`} /></svg>
}

function CountControl({ value, min = 1, max, onChange, disabled = false }: { value: number; min?: number; max: number; onChange: (next: number) => void; disabled?: boolean }) {
  return <div className="count-control" aria-label="Quantity">
    <button type="button" aria-label="Decrease quantity" disabled={disabled || value <= min} onClick={() => onChange(value - 1)}>−</button>
    <span aria-live="polite">{value}</span>
    <button type="button" aria-label="Increase quantity" disabled={disabled || value >= max} onClick={() => onChange(value + 1)}>+</button>
  </div>
}

function ItemPhoto({ item }: { item: MenuItem }) {
  const [failed, setFailed] = useState(false)
  if (!item.image_url || failed) return <div className="item-photo item-photo--empty" aria-hidden="true"><span>{item.name.slice(0, 1)}</span></div>
  return <img className="item-photo" src={item.image_url} alt="" loading="lazy" onError={() => setFailed(true)} />
}

export function App() {
  const initData = useMemo(initTelegram, [])
  const [language, setLanguage] = useState<Language>('ru')
  const t = copy(language)
  const [bootstrap, setBootstrap] = useState<Bootstrap | null>(null)
  const [menu, setMenu] = useState<Menu | null>(null)
  const [cart, setCart] = useState<Cart | null>(null)
  const [board, setBoard] = useState<Leaderboard | null>(null)
  const [screen, setScreen] = useState<Screen>('menu')
  const [draft, setDraft] = useState<Draft | null>(null)
  const [mystery, setMystery] = useState<MysteryResult | null>(null)
  const [categoryId, setCategoryId] = useState<number | null>(null)
  const [mysteryCategoryId, setMysteryCategoryId] = useState<number | null>(null)
  const [query, setQuery] = useState('')
  const [alcoholFree, setAlcoholFree] = useState(false)
  const [order, setOrder] = useState<Order | null>(null)
  const [orderComment, setOrderComment] = useState('')
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [authError, setAuthError] = useState(false)
  const requestKey = useRef<string | null>(null)

  const load = useCallback(async () => {
    if (!initData) { setLoading(false); return }
    try {
      const initial = await api<Bootstrap>('/bootstrap', initData)
      setBootstrap(initial)
      setLanguage(initial.user.language)
      setOrder(initial.active_order || initial.latest_order)
      if (initial.event) {
        const [catalog, basket] = await Promise.all([
          api<Menu>('/menu', initData), api<Cart>('/cart', initData),
        ])
        setMenu(catalog)
        setCart(basket)
      }
      setError('')
      setAuthError(false)
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : copy('ru').loadError)
      setAuthError(cause instanceof ApiError && cause.status === 401)
    } finally { setLoading(false) }
  }, [initData])

  useEffect(() => { void load() }, [load])

  useEffect(() => {
    if (!bootstrap?.event) return
    const timer = window.setInterval(async () => {
      if (document.visibilityState !== 'visible') return
      try {
        const current = await api<Bootstrap>('/bootstrap', initData)
        setBootstrap(current)
        setOrder(current.active_order || current.latest_order)
      } catch { /* the next refresh will retry */ }
    }, 10000)
    return () => window.clearInterval(timer)
  }, [bootstrap?.event, initData])

  useEffect(() => {
    const refresh = () => { if (document.visibilityState === 'visible') void load() }
    document.addEventListener('visibilitychange', refresh)
    return () => document.removeEventListener('visibilitychange', refresh)
  }, [load])

  const refreshCart = useCallback(async () => {
    if (!bootstrap?.event) return
    const basket = await api<Cart>('/cart', initData)
    setCart(basket)
  }, [bootstrap?.event, initData])

  const refreshBoard = useCallback(async () => {
    if (!bootstrap?.event) return
    const result = await api<Leaderboard>('/leaderboard', initData)
    setBoard(result)
  }, [bootstrap?.event, initData])

  useEffect(() => {
    if (screen !== 'top' || !bootstrap?.event) return
    void refreshBoard().catch((cause: Error) => setError(cause.message))
    const timer = window.setInterval(() => {
      if (document.visibilityState === 'visible') void refreshBoard().catch(() => {})
    }, 15000)
    return () => window.clearInterval(timer)
  }, [screen, bootstrap?.event, refreshBoard])

  const goBack = useCallback(() => {
    setError('')
    if (screen === 'item') setScreen(draft?.origin || 'menu')
    else if (screen === 'cart' || screen === 'order') setScreen('menu')
    else setScreen('menu')
  }, [screen, draft?.origin])

  useEffect(() => {
    const back = telegram()?.BackButton
    if (!back) return
    const active = screen === 'item' || screen === 'cart' || screen === 'order'
    if (active) back.show(); else back.hide()
    back.onClick(goBack)
    return () => { back.offClick(goBack); back.hide() }
  }, [screen, goBack])

  const run = async (operation: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try { await operation() }
    catch (cause) {
      setError(cause instanceof Error ? cause.message : t.loadError)
      if (cause instanceof ApiError && cause.status === 401) setAuthError(true)
      if (cause instanceof ApiError && cause.status === 409) {
        void refreshCart().catch(() => {})
        void api<Menu>('/menu', initData).then(setMenu).catch(() => {})
      }
    } finally { setBusy(false) }
  }

  const categories = menu?.categories || []
  const allItems = categories.flatMap(category => category.items)
  const visibleCategories = categories
    .filter(category => categoryId === null || category.id === categoryId)
    .map(category => ({ ...category, items: category.items.filter(item => {
      const needle = query.trim().toLocaleLowerCase()
      return (!needle || `${item.name} ${item.description} ${item.ingredients}`.toLocaleLowerCase().includes(needle))
        && (!alcoholFree || !item.is_alcoholic)
    }) }))
    .filter(category => category.items.length > 0)

  const openItem = (item: MenuItem, origin: 'menu' | 'mystery' | 'cart', cartItem?: CartItem) => {
    setDraft({
      item, origin, cartItemId: cartItem?.id, quantity: cartItem?.quantity || 1,
      modifierIds: cartItem?.modifier_ids || [], comment: cartItem?.comment || '',
      expectedQuantity: cartItem?.quantity,
    })
    setError('')
    setScreen('item')
  }

  const saveDraft = () => {
    if (!draft) return
    void run(async () => {
      const payload = { quantity: draft.quantity, modifier_ids: draft.modifierIds, comment: draft.comment }
      const basket = draft.cartItemId
        ? await api<Cart>(`/cart/items/${draft.cartItemId}`, initData, 'PATCH', { ...payload, expected_quantity: draft.expectedQuantity })
        : await api<Cart>('/cart/items', initData, 'POST', { ...payload, menu_item_id: draft.item.id })
      setCart(basket)
      requestKey.current = null
      setDraft(null)
      setScreen('cart')
    })
  }

  const updateQuantity = (item: CartItem, next: number) => {
    void run(async () => {
      const basket = await api<Cart>(`/cart/items/${item.id}`, initData, 'PATCH', {
        quantity: next, modifier_ids: item.modifier_ids, comment: item.comment,
        expected_quantity: item.quantity,
      })
      setCart(basket)
      requestKey.current = null
    })
  }

  const submit = () => {
    requestKey.current ||= crypto.randomUUID()
    void run(async () => {
      const created = await api<Order>('/orders', initData, 'POST', {
        comment: orderComment, idempotency_key: requestKey.current,
      })
      setOrder(created)
      setScreen('order')
      setCart({ id: cart?.id || 0, event_id: cart?.event_id || 0, items: [], total_quantity: 0 })
      requestKey.current = null
      try { setBootstrap(await api<Bootstrap>('/bootstrap', initData)) } catch { /* order remains confirmed */ }
    })
  }

  const drawMystery = () => {
    void run(async () => {
      const result = await api<MysteryResult>('/mystery/generate', initData, 'POST', {
        category_id: mysteryCategoryId, exclude_menu_item_id: mystery?.item.id,
      })
      setMystery(result)
    })
  }

  const switchLanguage = () => {
    void run(async () => {
      const next: Language = language === 'ru' ? 'en' : 'ru'
      await api('/me/language', initData, 'PUT', { language: next })
      const [catalog, basket, latest] = await Promise.all([
        api<Menu>('/menu', initData), api<Cart>('/cart', initData),
        api<{ order: Order | null }>('/orders/latest', initData),
      ])
      setLanguage(next); setMenu(catalog); setCart(basket); setOrder(latest.order); setBoard(null); setMystery(null)
    })
  }

  if (!initData) return <main className="gate"><div className="gate-brand"><img src="/static/images/azati-logo-current-light.svg" alt="Azati" /></div><h1>{t.auth}</h1><p>{t.authHint}</p></main>
  if (loading) return <main className="app-shell skeleton-shell" aria-busy="true"><div className="skeleton skeleton-header" /><div className="skeleton skeleton-search" /><div className="skeleton skeleton-item" /><div className="skeleton skeleton-item" /><div className="skeleton skeleton-item" /></main>
  if (authError) return <main className="gate"><div className="gate-brand"><img src="/static/images/azati-logo-current-light.svg" alt="Azati" /></div><h1>{t.auth}</h1><p>{t.authHint}</p></main>
  if (!bootstrap?.event) return <main className="gate"><div className="gate-brand"><img src="/static/images/azati-logo-current-light.svg" alt="Azati" /></div><h1>{t.noEvent}</h1><p>{error || t.noEventHint}</p><button className="button button--secondary" onClick={() => { setLoading(true); void load() }}>{t.retry}</button></main>

  const event = bootstrap.event
  const activeOrder = bootstrap.active_order
  const canOrder = event.orders_enabled && !activeOrder
  const cartCount = cart?.total_quantity || 0

  return <div className="app-shell">
    <header className="app-header">
      <div className="brand-row">
        <div className="brand-plate"><img src="/static/images/azati-logo-current-light.svg" alt="Azati" /></div>
        <button className="language-button" onClick={switchLanguage} disabled={busy} aria-label="Change language">{language.toUpperCase()}</button>
      </div>
      <p className="event-eyebrow">AZATI <span aria-hidden="true">/</span> EVENT BAR</p>
      <h1>{event.name}</h1>
      <div className={`event-status ${event.orders_enabled ? 'event-status--open' : ''}`}><span aria-hidden="true" />{event.orders_enabled ? t.orderOpen : t.orderPaused}</div>
    </header>

    {error && <div className="notice" role="alert"><span>{error}</span><button aria-label="Close" onClick={() => setError('')}><Icon name="close" size={17} /></button></div>}

    <main className="app-content">
      {screen === 'menu' && <>
        <div className="section-heading"><h2>{t.menu}</h2><span>{allItems.length}</span></div>
        <label className="search-field"><Icon name="search" size={19} /><span className="sr-only">{t.search}</span><input type="search" placeholder={t.search} value={query} onChange={event => setQuery(event.target.value)} /></label>
        <div className="chip-row" aria-label={t.menu}>
          <button className={categoryId === null ? 'chip chip--active' : 'chip'} onClick={() => setCategoryId(null)}>{t.all}</button>
          {categories.map(category => <button key={category.id} className={categoryId === category.id ? 'chip chip--active' : 'chip'} onClick={() => setCategoryId(category.id)}>{category.name}</button>)}
        </div>
        <label className="filter-row"><input type="checkbox" checked={alcoholFree} onChange={event => setAlcoholFree(event.target.checked)} /><span>{t.noAlcohol}</span></label>
        {visibleCategories.length === 0 ? <Empty title={allItems.length ? t.emptySearch : t.emptyMenu} /> : visibleCategories.map(category => <section className="category-section" key={category.id}>
          <h3>{category.name}</h3>
          <div className="menu-list">{category.items.map(item => <button className="menu-item" key={item.id} onClick={() => openItem(item, 'menu')}>
            <div className="menu-item-copy"><strong>{item.name}</strong><span>{item.description || item.ingredients}</span>{item.is_alcoholic && <small>{t.alcoholic}</small>}</div>
            <ItemPhoto item={item} />
          </button>)}</div>
        </section>)}
      </>}

      {screen === 'item' && draft && <>
        <button className="text-back" onClick={goBack}><span aria-hidden="true">‹</span>{t.back}</button>
        <div className="detail-photo"><ItemPhoto item={draft.item} /></div>
        <h2 className="detail-title">{draft.item.name}</h2>
        {draft.item.description && <p className="detail-description">{draft.item.description}</p>}
        {draft.item.ingredients && <div className="detail-ingredients"><strong>{t.ingredients}</strong><p>{draft.item.ingredients}</p></div>}
        {draft.item.is_alcoholic && <span className="alcohol-label">{t.alcoholic}</span>}
        {['ice', 'variant', 'extra'].map(kind => {
          const options = draft.item.modifiers.filter(modifier => modifier.kind === kind)
          if (!options.length) return null
          return <fieldset className="modifier-group" key={kind}><legend>{kind === 'ice' ? (language === 'ru' ? 'Лёд' : 'Ice') : kind === 'variant' ? (language === 'ru' ? 'Вариант' : 'Variant') : (language === 'ru' ? 'Добавки' : 'Extras')}</legend>
            {options.map(option => {
              const checked = draft.modifierIds.includes(option.id)
              return <label className="modifier-option" key={option.id}>
                <input type="checkbox" checked={checked} onChange={() => setDraft(current => {
                  if (!current) return current
                  const withoutKind = kind === 'extra' ? current.modifierIds : current.modifierIds.filter(id => !options.some(modifier => modifier.id === id))
                  return { ...current, modifierIds: checked ? current.modifierIds.filter(id => id !== option.id) : [...withoutKind, option.id] }
                })} /><span>{option.name}</span>
              </label>
            })}
          </fieldset>
        })}
        <div className="form-row"><label>{t.quantity}</label><CountControl value={draft.quantity} max={event.max_same_item} onChange={quantity => setDraft(current => current ? { ...current, quantity } : current)} /></div>
        <label className="field-label">{t.comment}<textarea maxLength={300} value={draft.comment} onChange={event => setDraft(current => current ? { ...current, comment: event.target.value } : current)} rows={2} /></label>
        <button className="button button--primary detail-submit" disabled={busy || !event.orders_enabled} onClick={saveDraft}>{draft.cartItemId ? t.save : t.add}</button>
      </>}

      {screen === 'cart' && <>
        <button className="text-back" onClick={goBack}><span aria-hidden="true">‹</span>{t.back}</button>
        <h2 className="screen-title">{t.cart}</h2>
        {activeOrder && <div className="inline-info">{t.activeOrder}: <strong>{activeOrder.public_number}</strong></div>}
        {!cart?.items.length ? <Empty title={t.cartEmpty} body={t.cartHint} /> : <>
          <div className="cart-list">{cart.items.map(item => <div className="cart-item" key={item.id}>
            <div className="cart-item-top"><strong>{item.name}</strong><button className="link-button" onClick={() => { const product = allItems.find(product => product.id === item.menu_item_id); if (product) openItem(product, 'cart', item) }}>{t.save}</button></div>
            {item.modifiers.length > 0 && <p>{item.modifiers.join(', ')}</p>}
            {item.comment && <p>{item.comment}</p>}
            <div className="cart-item-actions"><CountControl value={item.quantity} max={event.max_same_item} onChange={next => updateQuantity(item, next)} disabled={busy || !event.orders_enabled} /><button className="link-button link-button--danger" disabled={busy} onClick={() => void run(async () => { setCart(await api<Cart>(`/cart/items/${item.id}`, initData, 'DELETE')); requestKey.current = null })}>{t.remove}</button></div>
          </div>)}</div>
          <div className="cart-total"><span>{t.total}</span><strong>{cartCount} / {event.max_items_per_order}</strong></div>
          <label className="field-label">{t.orderComment}<textarea maxLength={300} value={orderComment} onChange={event => setOrderComment(event.target.value)} rows={2} /></label>
          <p className="helper-text">{t.checkOrder}</p>
          <button className="button button--primary confirm-button" disabled={busy || !canOrder} onClick={submit}>{busy ? '…' : t.confirm}</button>
        </>}
      </>}

      {screen === 'mystery' && <>
        <h2 className="screen-title">{t.mystery}</h2>
        <p className="section-intro">{t.mysteryIntro}</p>
        <div className="mystery-stage"><div className="mystery-glyph" aria-hidden="true">?</div><p>{mystery ? mystery.item.name : t.surprise}</p></div>
        <label className="field-label">{t.menu}<select value={mysteryCategoryId || ''} onChange={event => { setMysteryCategoryId(event.target.value ? Number(event.target.value) : null); setMystery(null) }}><option value="">{t.all}</option>{categories.map(category => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
        {mystery && <div className="mystery-result"><h3>{mystery.item.name}</h3><p>{mystery.item.description || mystery.item.ingredients}</p>{mystery.item.is_alcoholic && <small>{t.alcoholic}</small>}<button className="button button--primary" disabled={!event.orders_enabled} onClick={() => openItem(mystery.item, 'mystery')}>{t.add}</button></div>}
        <button className={`button ${mystery ? 'button--secondary' : 'button--primary'}`} disabled={busy || !event.orders_enabled || (mystery !== null && !mystery.can_reroll)} onClick={drawMystery}>{mystery ? t.again : t.surprise}</button>
      </>}

      {screen === 'top' && <>
        <h2 className="screen-title">{t.top}</h2><p className="section-intro">{t.topIntro}</p>
        {!board ? <div className="skeleton skeleton-item" /> : board.top.length === 0 ? <Empty title={t.topEmpty} /> : <ol className="leaderboard-list">{board.top.map(entry => <li className={entry.is_me ? 'leaderboard-row leaderboard-row--me' : 'leaderboard-row'} key={entry.rank}><span className="rank">{entry.rank}</span><strong>{entry.name}</strong><span className="score">{entry.score}</span></li>)}</ol>}
        {board && <div className="my-rank"><span>{t.myPlace}</span><strong>{board.me ? `${board.me.rank} · ${board.me.score}` : t.noPlace}</strong></div>}
        <label className="consent-row"><input type="checkbox" checked={board?.show_telegram_name || false} disabled={!board || busy} onChange={event => void run(async () => { await api('/leaderboard/privacy', initData, 'PUT', { show_telegram_name: event.target.checked }); await refreshBoard() })} /><span><strong>{t.nameConsent}</strong><small>{t.nameConsentHint}</small></span></label>
      </>}

      {screen === 'order' && order && <>
        <div className="order-confirmation"><div className="confirmation-mark"><Icon name="check" size={30} /></div><p>{t.status}: {t[order.status as keyof typeof t] || order.status}</p><h2>{order.public_number}</h2></div>
        <div className="order-lines">{order.items.map((item, index) => <div key={`${item.name}-${index}`}><span>{item.name}{item.modifiers.length ? ` · ${item.modifiers.join(', ')}` : ''}</span><strong>×{item.quantity}</strong></div>)}</div>
        <button className="button button--secondary" onClick={() => setScreen('menu')}>{t.menu}</button>
      </>}
    </main>

    {screen !== 'cart' && screen !== 'item' && screen !== 'order' && cartCount > 0 && <button className="cart-floating" onClick={() => { void refreshCart().catch(() => {}); setScreen('cart') }}><span>{t.cart}</span><strong>{cartCount}</strong></button>}
    {screen !== 'item' && screen !== 'cart' && screen !== 'order' && <nav className="bottom-nav" aria-label="Main navigation"><button className={screen === 'menu' ? 'active' : ''} onClick={() => setScreen('menu')}>{t.menu}</button><button className={screen === 'mystery' ? 'active' : ''} onClick={() => setScreen('mystery')}>{t.mystery}</button><button className={screen === 'top' ? 'active' : ''} onClick={() => setScreen('top')}>{t.top}</button></nav>}
  </div>
}

function Empty({ title, body }: { title: string; body?: string }) {
  return <div className="empty-state" role="status"><strong>{title}</strong>{body && <p>{body}</p>}</div>
}
