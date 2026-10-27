# Спокойное движение меню работодателя

Исследование, 6 октября 2026. Приложение и деплой не менялись. Локально подтверждены `motion@13.4.6` и `radix-ui@1.6.7` в `interface-lab/node_modules`; перечисленные ниже API есть в установленных декларациях. Это предложения для сравнения, а не принятые требования продукта. Фрагменты иллюстрируют API и не проверялись в браузере.

Context7: обязательный поиск предпринят. CLI использует старый API v1 и завершился ошибкой; документированный API v2 также вернул HTTP 429. Родительское исследование отдельно установило исчерпание месячной квоты. Поэтому выдача Context7 docs не получена, ниже только официальные первоисточники и локальные декларации. Motion MCP в сессии не подключён: [Motion AI Kit](https://motion.dev/docs/ai-kit).

## Четыре самостоятельных варианта

| Вариант | Что видит человек | Одно характерное движение | Готовая основа | Оговорка для HR Vision |
|---|---|---|---|---|
| **Раскрывающаяся левая рейка** | Постоянные значки; кнопка «Разделы» открывает подписи вправо | Ширина поверхности меняется, значки остаются на месте; подписи проявляются одновременно | `motion.aside animate={{width}}`; обычные `nav` / ссылки; `Collapsible` нужен только если действительно скрываются дополнительные элементы | Полезна при частом переключении. Не двигать весь рабочий экран и не перетасовывать пункты |
| **Неподвижное меню с живой меткой** | Все подписи видны; активный раздел выделен тонкой плашкой или короткой чертой | Только метка переезжает к выбранному пункту | `LayoutGroup` + один `layoutId` | Самый спокойный контрольный вариант: функция читается без раскрытия |
| **Панель от кнопки** | Кнопка «Разделы» рядом с названием текущего экрана; короткий список появляется возле неё | Панель вырастает от своей точки привязки: `scale .98 → 1` и opacity | `Popover` из `radix-ui`, `asChild`, `AnimatePresence`, `forceMount` | Сильнее освобождает экран, но добавляет действие перед переходом. Текущий раздел должен быть виден и при закрытой панели |
| **Компактный плавающий док** | Небольшая горизонтальная панель из нескольких стабильных пунктов с короткими подписями | При фокусе или наведении один пункт приподнимается на 2 px; на нажатии возвращается | `motion.nav`, `motion.a` с `whileFocus` / `whileHover` / `whileTap` | Имеет смысл для небольшого числа равноправных разделов. Не делать увеличение соседних иконок, «магнит» за курсором или скрытие при прокрутке |

Размеры и диапазоны движения в таблице выбраны для прототипа. Это веб-адаптация, не численные стандарты Apple. Из Apple здесь используются непосредственный отклик, возможность сменить решение во время движения и понятная пространственная связь источника с результатом. [Apple, Designing Fluid Interfaces](https://developer.apple.com/videos/play/wwdc2018/803/)

## Общая настройка

```tsx
import { AnimatePresence, LayoutGroup, MotionConfig,
  motion, useReducedMotion } from "motion/react";
import { Popover } from "radix-ui";

// Наши стартовые параметры: быстрое движение без декоративной раскачки.
const spring = { type: "spring" as const, stiffness: 420, damping: 41, mass: 1 };

// Внешняя обёртка всех вариантов:
<MotionConfig reducedMotion="user">{children}</MotionConfig>
```

Для частого переключения подходят physics springs: `stiffness` / `damping` / `mass` учитывают текущую скорость. Пара `duration` + `bounce` скорость не наследует. Не ставить `disabled` до завершения анимации, не откладывать переход страницы до `onAnimationComplete`. [Motion: transitions](https://motion.dev/docs/react-transitions)

`MotionConfig reducedMotion="user"` отключает transform и layout-анимации, но сохраняет opacity. Для прямого `width` требуется отдельная ветка через `useReducedMotion`; одной глобальной обёртки недостаточно. [Motion: accessibility](https://motion.dev/docs/react-accessibility)

## Короткие фрагменты

### 1. Рейка

```tsx
const reduce = useReducedMotion();
<motion.aside
  initial={false}
  animate={{ width: expanded ? 224 : 64 }}
  transition={reduce ? { duration: 0 } : spring}
  style={{ overflow: "hidden" }}
>
  {/* Постоянная кнопка: aria-expanded={expanded}, aria-controls="employer-nav". */}
  {/* nav остаётся смонтирован; ссылки сохраняют полные доступные названия. */}
</motion.aside>
```

224/64 px являются пробными значениями. Фиксированная внутренняя ширина и `white-space: nowrap` удерживают текст от перелома на каждом кадре. Раскрытие накладывается на контент, а не заставляет всю страницу непрерывно перевёрстываться. Здесь нет `layout`: не анимировать одну геометрию двумя механизмами одновременно. Если потребуется изменение общей компоновки, заменить подход на `layout` и менять ширину через `style`, затем отдельно проверить текст. [Motion: layout animations](https://motion.dev/docs/react-layout-animations)

### 2. Активная метка

```tsx
<LayoutGroup id="employer-nav-preview">
  <nav aria-label="Разделы работодателя">
    {items.map(item => (
      <a key={item.id} href={item.href}
        aria-current={item.id === activeId ? "page" : undefined}
        style={{ position: "relative" }}>
        {item.id === activeId && (
          <motion.span aria-hidden="true" layoutId="active-mark"
            transition={spring} className="active-mark" />
        )}
        {item.label}
      </a>
    ))}
  </nav>
</LayoutGroup>
```

`.active-mark` является декоративным абсолютным слоем с `pointer-events: none`; активная подпись сохраняет различимый вес/цвет. Для нескольких одновременно открытых вариантов нужны разные `LayoutGroup id`. Метка следует выбранному маршруту, а не временному наведению. Focus outline рисуется отдельно. [Motion: shared layout](https://motion.dev/docs/react-layout-animations)

### 3. Панель от кнопки

```tsx
const [open, setOpen] = useState(false);
const reduce = useReducedMotion();
<Popover.Root open={open} onOpenChange={setOpen}>
  <Popover.Trigger>Разделы</Popover.Trigger>
  <AnimatePresence>
    {open && (
      <Popover.Portal key="sections" forceMount>
        <Popover.Content asChild forceMount align="start"
          sideOffset={8} collisionPadding={12} aria-label="Разделы работодателя">
          <motion.div
            style={{ transformOrigin: "var(--radix-popover-content-transform-origin)" }}
            initial={{ opacity: 0, scale: reduce ? 1 : 0.98 }}
            animate={{ opacity: 1, scale: 1 }}
            exit={{ opacity: 0, scale: reduce ? 1 : 0.98 }}
            transition={{ ...spring, opacity: { duration: 0.12 } }}>
            {/* nav с настоящими ссылками; выбор закрывает панель. */}
          </motion.div>
        </Popover.Content>
      </Popover.Portal>
    )}
  </AnimatePresence>
</Popover.Root>
```

Дополнительный импорт: `useState` из React. `forceMount` относится к Radix, не к `motion.div`. Управляемое состояние позволяет AnimatePresence закончить выход; не применять `mode="wait"` для переключения навигации. Паттерн интеграции: [Motion + Radix](https://motion.dev/docs/radix), жизненный цикл: [AnimatePresence](https://motion.dev/docs/react-animate-presence).

Radix вычисляет transform-origin с учётом привязки и коллизий. По умолчанию Popover немодальный; Enter/Space открывают, Tab перемещает фокус, Esc закрывает и возвращает его на триггер. Не отменять `onOpenAutoFocus` / `onCloseAutoFocus` без конкретной причины; для перехода маршрута отдельно определить фокус заголовка новой страницы. [Radix Popover](https://www.radix-ui.com/primitives/docs/components/popover)

### 4. Плавающий док

```tsx
const reduce = useReducedMotion();
<motion.nav aria-label="Разделы работодателя" className="employer-dock">
  {items.map(item => (
    <motion.a key={item.id} href={item.href}
      aria-current={item.id === activeId ? "page" : undefined}
      whileHover={{ y: reduce ? 0 : -2 }}
      whileFocus={{ y: reduce ? 0 : -2 }}
      whileTap={{ y: 0 }} transition={spring}>
      {item.label}
    </motion.a>
  ))}
</motion.nav>
```

Док располагается выше нижней границы окна с учётом `env(safe-area-inset-bottom)`; рабочая область получает достаточный нижний отступ. Состояние выбора видно статически. Подъём обратим и мал; интерфейс работает так же на touch без hover. При reduced motion остаются заливка и focus outline.

## Условия проверки любого варианта

- Доступ к разделу через клик, касание и клавиатуру. Hover только дополняет; не открывает единственный путь к действию.
- Обычная навигация использует `nav` и ссылки. `role="menu"` и DropdownMenu требуют иного клавиатурного поведения, поэтому для этого списка не нужны. [WAI: disclosure navigation](https://www.w3.org/WAI/ARIA/apg/patterns/disclosure/examples/disclosure-navigation/)
- Постоянные рейка, метка и док не ловят Esc глобально. Esc закрывает временное раскрытие/Popover; если скрывается сфокусированный пункт, фокус сначала возвращается на триггер.
- Быстро открыть → закрыть → открыть, затем сменить раздел трижды. Должно сразу выполняться последнее намерение, без скачка к старому старту и очереди переходов.
- Tab, Shift+Tab, Enter, Space на триггере, Esc из панели, клик снаружи; после закрытия скрытые ссылки не должны попадать в Tab-порядок.
- Reduced motion, длинные русские подписи, масштаб текста, узкий экран и нижний safe area. Проверить отсутствие перекрытого содержимого и видимость focus outline.
- Одновременно использовать один ведущий эффект. Не добавлять stagger по пунктам, blur-анимацию всего экрана, следящий блик и прыгающие счётчики.

Для первого сравнения предлагаются **неподвижное меню с меткой** как спокойная база и **панель от кнопки** как максимально компактная альтернатива. Рейка показывает пользу постоянных ориентиров; док даёт другой силуэт и размещение, но требует проверки вместимости.
