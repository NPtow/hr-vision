# UI-паттерны анкеты после интервью

Дата проверки: 04.10.2026. Это исследовательские рекомендации и адаптированные примеры, не реализация и не валидированная шкала оценки.

## Совместимость с лабораторией

Проверены `interface-lab/package.json`, начало `interface-lab/README.md`, lockfile и установленные типы. Стек: React 19.3.0, TypeScript 7.0.2, `radix-ui` ^1.6.7. В lockfile указаны `@radix-ui/react-radio-group` 1.4.7 и `@radix-ui/react-collapsible` 1.1.20. Их установленные типы поддерживают контролируемые `value`/`onValueChange` и `open`/`onOpenChange`. Дополнительная библиотека не нужна. README описывает локальные демонстрационные макеты без подключённого HR-бэкенда.

## Что брать

| Задача | Подход | Основание |
|---|---|---|
| Один ответ по критерию | `RadioGroup.Root` + `Item` + `Indicator`, видимые подписи через `htmlFor`, заголовок группы через `aria-labelledby` | [Radix Radio Group](https://www.radix-ui.com/primitives/docs/components/radio-group), [исходник документации](https://github.com/radix-ui/website/blob/main/data/primitives/docs/components/radio-group.mdx) |
| Клавиатура | Сохранить штатную навигацию Radix; Tab входит в группу, Space и стрелки выбирают пункт | [Radix: keyboard interactions](https://www.radix-ui.com/primitives/docs/components/radio-group#keyboard-interactions) |
| Дополнительные доказательства | Краткое основание видно сразу; полный фрагмент или подробный комментарий в `Collapsible` | [Radix Collapsible](https://www.radix-ui.com/primitives/docs/components/collapsible), [исходник документации](https://github.com/radix-ui/website/blob/main/data/primitives/docs/components/collapsible.mdx) |
| Ошибка | Текст рядом с полем, `aria-invalid`, привязка текста через `aria-describedby`; после попытки отправки фокус на первой ошибке | [W3C: User Notification](https://www.w3.org/WAI/tutorials/forms/notifications/) |

Продуктовая рекомендация: начальное значение `""` означает «ответ ещё не дан», а явный пункт `"unassessed"` означает «человек выбрал “Не оценено”». Ни один положительный или отрицательный ответ не предвыбран. Эти состояния следует хранить раздельно; `unassessed` не превращать в ноль, среднюю оценку или отрицательный ответ. Это наше решение для анкеты, а не требование API Radix.

Не переносить `defaultValue="default"` из демонстрации Radix: там он выбирает плотность интерфейса, здесь создаст неподтверждённую оценку кандидата. Сохранять черновик с пропусками допустимо; при завершении можно просить выбрать ответ либо «Не оценено». Это правило завершения ещё требуется согласовать с продуктом.

## Маленький пример: критерий и явное «Не оценено»

Адаптация анатомии Radix. Названия вариантов ниже иллюстрируют UI и должны быть заменены на утверждённые поведенческие якоря. Компонент получает `error` от проверки формы после попытки завершения.

```tsx
import { useId, useState } from "react";
import { RadioGroup } from "radix-ui";

const answers = [
  ["unassessed", "Не оценено"],
  ["below", "Не соответствует критерию"],
  ["partial", "Частично соответствует"],
  ["meets", "Соответствует критерию"],
] as const;

function Criterion({ error }: { error?: string }) {
  const id = useId();
  const [answer, setAnswer] = useState("");
  return (
    <fieldset>
      <legend id={`${id}-title`}>Критерий из брифа</legend>
      <p id={`${id}-hint`}>Выберите «Не оценено», если оснований нет.</p>
      <RadioGroup.Root
        name="criterion"
        orientation="vertical"
        value={answer}
        onValueChange={setAnswer}
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-hint${error ? ` ${id}-error` : ""}`}
        aria-invalid={Boolean(error)}
      >
        {answers.map(([value, label]) => (
          <div key={value}>
            <RadioGroup.Item id={`${id}-${value}`} value={value}>
              <RadioGroup.Indicator />
            </RadioGroup.Item>
            <label htmlFor={`${id}-${value}`}>{label}</label>
          </div>
        ))}
      </RadioGroup.Root>
      {error && <p id={`${id}-error`}>{error}</p>}
    </fieldset>
  );
}
```

Для реальной анкеты состояние ответов поднимается в родитель формы. Каждый критерий получает уникальное `name`. Для ошибки пустого ответа подходит «Выберите оценку или “Не оценено”», но не «Поставьте оценку». Группы с выбранным `unassessed` проходят эту проверку. Стили должны давать видимый `:focus-visible` и отличать выбор не только цветом; snippet не содержит CSS и не подтверждает доступность готового экрана.

## Маленький пример: доказательство по запросу

```tsx
import { useState } from "react";
import { Collapsible } from "radix-ui";

function Evidence({ fragment }: { fragment: string }) {
  const [open, setOpen] = useState(false);
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen}>
      <p>Основание: пример кандидата о конкретной рабочей ситуации.</p>
      <Collapsible.Trigger asChild>
        <button type="button">
          {open ? "Скрыть фрагмент" : "Показать фрагмент интервью"}
        </button>
      </Collapsible.Trigger>
      <Collapsible.Content><blockquote>{fragment}</blockquote></Collapsible.Content>
    </Collapsible.Root>
  );
}
```

Триггер остаётся настоящей кнопкой. Не прятать в закрытую область единственное описание критерия, выбранный ответ или ошибку. Если внутри есть редактируемый комментарий, хранить его в состоянии формы, чтобы сворачивание не уничтожало черновик. Если обязательный комментарий не заполнен, раскрывать раздел перед переводом фокуса на поле. Для примера без реального транскрипта явно маркировать основание как демонстрационное.

## Ограничение Radix Form

Context7 вернул примеры `Form.Field`, `Form.Message`, `serverInvalid`, `forceMatch`. Однако актуальная [документация Form](https://www.radix-ui.com/primitives/docs/components/form#composing-with-your-own-components) прямо ограничивает композицию с другими form primitives Radix. Поэтому для этой анкеты рекомендуется обычный `<form>` и явная связка ошибок с RadioGroup. Не считать `<Form.Control asChild><RadioGroup.Root /></Form.Control>` подтверждённым решением. Нативные `input`/`textarea` могут использовать собственную доступную разметку либо отдельно подтверждённую композицию Form.

## Выполненные запросы Context7

CLI: `/Users/NIKITA/.codex/skills/context7/c7`. Родительская исследовательская задача предварительно выполнила `search "Radix UI"` и передала найденные `/websites/radix-ui_primitives` и `/radix-ui/website`; другие library IDs не использовались.

1. `docs /websites/radix-ui_primitives "Radio Group controlled value aria-labelledby item label keyboard accessibility no default selected value" 3500`
2. `docs /radix-ui/website "Collapsible Trigger Content controlled open asChild accessibility form validation aria-invalid aria-describedby radio group" 3500`
3. `docs /radix-ui/website "primitives RadioGroup.Root value onValueChange aria-label Label htmlFor radio-group.mdx" 2000`
4. `docs /radix-ui/website "primitives Form.Field serverInvalid Form.Message forceMatch aria-describedby errors accessibility" 1700`

Широкие запросы возвращали также нерелевантные Select, меню и Radix Themes. Из них использованы только перечисленные выше **Primitives**. Веб-страницы Radix Radio Group, Collapsible, Form и W3C User Notification дополнительно прочитаны напрямую 04.10.2026. Snippets адаптированы, не вставлены в приложение и не прогонялись в браузере или TypeScript-компиляторе. Проверены наличие API в установленном пакете и документированное назначение паттернов.
