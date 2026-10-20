import { useEffect, useMemo, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Extension, getSchema, type Editor, type JSONContent, type Range } from '@tiptap/core'
import { EditorContent, useEditor } from '@tiptap/react'
import { PluginKey } from '@tiptap/pm/state'
import StarterKit from '@tiptap/starter-kit'
import Placeholder from '@tiptap/extension-placeholder'
import Suggestion, { exitSuggestion, type SuggestionProps } from '@tiptap/suggestion'
import { Bold, Italic, List, ListOrdered } from 'lucide-react'
import './note-editor.css'

export type TiptapNoteEditorProps = {
  value: JSONContent
  onChange: (document: JSONContent, text: string) => void
  editable: boolean
  label?: string
  placeholder?: string
  compact?: boolean
}

type SlashItem = {
  title: string
  aliases: string
  glyph: string
  run: (editor: Editor, range: Range) => void
}

const noteKit = () => StarterKit.configure({
  heading: { levels: [2, 3] },
  link: false,
  code: false,
  codeBlock: false,
  horizontalRule: false,
  strike: false,
  underline: false,
})

/** Validate provider output before rendering or persisting it as an accepted note. */
export function validateNoteDocument(value: JSONContent) {
  if (value?.type !== 'doc' || !Array.isArray(value.content)) throw new Error('Invalid note document')
  getSchema([noteKit()]).nodeFromJSON(value).check()
}

// Uses the MIT Suggestion primitive. Tiptap's paid Notion template is not used.
const slashItems: SlashItem[] = [
  { title: 'Текст', aliases: 'text paragraph текст абзац', glyph: 'Т', run: (editor, range) => { editor.chain().focus().deleteRange(range).setParagraph().run() } },
  { title: 'Заголовок', aliases: 'heading h2 заголовок', glyph: 'H₂', run: (editor, range) => { editor.chain().focus().deleteRange(range).toggleHeading({ level: 2 }).run() } },
  { title: 'Подзаголовок', aliases: 'heading h3 подзаголовок', glyph: 'H₃', run: (editor, range) => { editor.chain().focus().deleteRange(range).toggleHeading({ level: 3 }).run() } },
  { title: 'Список', aliases: 'bullet list список маркеры', glyph: '•', run: (editor, range) => { editor.chain().focus().deleteRange(range).toggleBulletList().run() } },
  { title: 'Нумерованный список', aliases: 'ordered numbered list нумерованный список', glyph: '1.', run: (editor, range) => { editor.chain().focus().deleteRange(range).toggleOrderedList().run() } },
  { title: 'Цитата', aliases: 'quote blockquote цитата', glyph: '“', run: (editor, range) => { editor.chain().focus().deleteRange(range).toggleBlockquote().run() } },
]

function createSlashExtension() {
  const pluginKey = new PluginKey('recruiterNoteSlash')
  return Extension.create({
    name: 'recruiterNoteSlash',
    addProseMirrorPlugins() {
      const editor = this.editor
      return [
        Suggestion<SlashItem, SlashItem>({
          editor,
          pluginKey,
          char: '/',
          startOfLine: true,
          allowSpaces: false,
          allow: ({ state }) => editor.isEditable && state.selection.$from.parent.type.name === 'paragraph',
          items: ({ query }) => slashItems.filter(item => item.aliases.includes(query.toLocaleLowerCase('ru'))),
          command: ({ editor: current, range, props }) => {
            if (current.isEditable) props.run(current, range)
          },
          floatingUi: { strategy: 'fixed' },
          render: () => {
            let menu: HTMLDivElement | null = null
            let current: SuggestionProps<SlashItem, SlashItem> | null = null
            let selected = 0
            let unmount: (() => void) | undefined
            let originalControls: string | null = null
            const menuId = `rcc-note-slash-${Math.random().toString(36).slice(2)}`

            const paintSelection = () => {
              menu?.querySelectorAll<HTMLButtonElement>('[role="option"]').forEach((button, index) => {
                button.setAttribute('aria-selected', String(index === selected))
              })
              if (current?.items.length) {
                editor.view.dom.setAttribute('aria-activedescendant', `${menuId}-${selected}`)
              } else {
                editor.view.dom.removeAttribute('aria-activedescendant')
              }
            }
            const renderItems = (props: SuggestionProps<SlashItem, SlashItem>) => {
              if (!menu) return
              const oldQuery = current?.query
              current = props
              if (oldQuery !== props.query || selected >= props.items.length) selected = 0
              menu.replaceChildren()
              if (props.loading && !props.items.length) return
              if (!props.items.length) {
                const empty = document.createElement('p')
                empty.className = 'rcc-rich-slash-empty'
                empty.textContent = 'Нет команд'
                menu.append(empty)
              }
              props.items.forEach((item, index) => {
                const button = document.createElement('button')
                button.type = 'button'
                button.id = `${menuId}-${index}`
                button.className = 'rcc-rich-slash-item'
                button.setAttribute('role', 'option')
                button.tabIndex = -1
                const glyph = document.createElement('span')
                glyph.className = 'rcc-rich-slash-glyph'
                glyph.setAttribute('aria-hidden', 'true')
                glyph.textContent = item.glyph
                const text = document.createElement('span')
                text.textContent = item.title
                button.append(glyph, text)
                button.addEventListener('pointerdown', event => event.preventDefault())
                button.addEventListener('mouseenter', () => { selected = index; paintSelection() })
                button.addEventListener('click', () => current?.command(item))
                menu?.append(button)
              })
              paintSelection()
            }
            return {
              onStart: props => {
                menu = document.createElement('div')
                menu.id = menuId
                menu.className = 'rcc-rich-slash'
                menu.setAttribute('role', 'listbox')
                menu.setAttribute('aria-label', 'Формат блока')
                originalControls = editor.view.dom.getAttribute('aria-controls')
                editor.view.dom.setAttribute('aria-controls', menuId)
                renderItems(props)
                // Managed positioning handles scroll, resize, flipping and dismissal.
                const portalRoot = document.fullscreenElement
                if (portalRoot) portalRoot.append(menu)
                unmount = props.mount(menu)
              },
              onUpdate: renderItems,
              onKeyDown: ({ event }) => {
                if (event.isComposing) return false
                if (event.key === 'Escape' || event.key === 'Tab') {
                  exitSuggestion(editor.view, pluginKey)
                  return event.key === 'Escape'
                }
                if (!current?.items.length) return false
                if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
                  const step = event.key === 'ArrowDown' ? 1 : -1
                  selected = (selected + step + current.items.length) % current.items.length
                  paintSelection()
                  menu?.querySelector<HTMLElement>(`[id="${menuId}-${selected}"]`)?.scrollIntoView({ block: 'nearest' })
                  return true
                }
                if (event.key === 'Enter') {
                  current.command(current.items[selected])
                  return true
                }
                return false
              },
              onExit: () => {
                unmount?.()
                menu?.remove()
                menu = null
                current = null
                editor.view.dom.removeAttribute('aria-activedescendant')
                if (originalControls) editor.view.dom.setAttribute('aria-controls', originalControls)
                else editor.view.dom.removeAttribute('aria-controls')
              },
            }
          },
        }),
      ]
    },
  })
}

type SelectionToolbar = { visible: boolean; left: number; top: number; bold: boolean; italic: boolean; bullet: boolean; ordered: boolean }
const hiddenToolbar: SelectionToolbar = { visible: false, left: 0, top: 0, bold: false, italic: false, bullet: false, ordered: false }

export function TiptapNoteEditor({
  value,
  onChange,
  editable,
  label = 'Заметки',
  placeholder = 'Начните писать или введите /',
  compact = false,
}: TiptapNoteEditorProps) {
  const onChangeRef = useRef(onChange)
  const placeholderRef = useRef(placeholder)
  onChangeRef.current = onChange
  placeholderRef.current = placeholder
  const [toolbar, setToolbar] = useState<SelectionToolbar>(hiddenToolbar)
  const extensions = useMemo(() => [
    noteKit(),
    Placeholder.configure({ placeholder: () => placeholderRef.current }),
    createSlashExtension(),
  ], [])

  const editor = useEditor({
    extensions,
    content: value,
    editable,
    immediatelyRender: false,
    shouldRerenderOnTransaction: false,
    editorProps: {
      attributes: { role: 'textbox', 'aria-multiline': 'true', 'aria-label': label, spellcheck: 'true' },
      // Clipboard text is inserted through the schema, not through innerHTML.
      // Rich-text paste retains supported headings/lists/marks; images and links
      // have no registered node/mark and are not inserted as executable content.
    },
    onUpdate: ({ editor: current }) => onChangeRef.current(current.getJSON(), current.getText({ blockSeparator: '\n' })),
  })

  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    if (JSON.stringify(editor.getJSON()) !== JSON.stringify(value)) {
      editor.commands.setContent(value, { emitUpdate: false, errorOnInvalidContent: true })
    }
  }, [editor, value])

  useEffect(() => {
    if (!editor || editor.isDestroyed) return
    editor.setEditable(editable, false)
    editor.view.dom.setAttribute('aria-label', label)
  }, [editor, editable, label])

  useEffect(() => {
    if (!editor) return
    let disposed = false
    const update = () => {
      if (disposed || editor.isDestroyed) return
      const { from, to, empty } = editor.state.selection
      let next = hiddenToolbar
      if (editable && editor.isFocused && !empty) {
        const a = editor.view.coordsAtPos(from)
        const b = editor.view.coordsAtPos(to)
        next = {
          visible: true,
          left: Math.max(92, Math.min(window.innerWidth - 92, (a.left + b.right) / 2)),
          top: Math.max(8, Math.min(window.innerHeight - 52, a.top > 64 ? a.top - 48 : b.bottom + 8)),
          bold: editor.isActive('bold'),
          italic: editor.isActive('italic'),
          bullet: editor.isActive('bulletList'),
          ordered: editor.isActive('orderedList'),
        }
      }
      setToolbar(previous => JSON.stringify(previous) === JSON.stringify(next) ? previous : next)
    }
    const scheduleUpdate = () => { queueMicrotask(update) }
    editor.on('transaction', scheduleUpdate)
    editor.on('focus', scheduleUpdate)
    editor.on('blur', scheduleUpdate)
    window.addEventListener('resize', scheduleUpdate)
    window.addEventListener('scroll', scheduleUpdate, true)
    return () => {
      disposed = true
      editor.off('transaction', scheduleUpdate)
      editor.off('focus', scheduleUpdate)
      editor.off('blur', scheduleUpdate)
      window.removeEventListener('resize', scheduleUpdate)
      window.removeEventListener('scroll', scheduleUpdate, true)
    }
  }, [editor, editable])

  return (
    <div className={`rcc-rich-editor${compact ? ' rcc-rich-editor--compact' : ''}${editable ? '' : ' rcc-rich-editor--readonly'}`}>
      <EditorContent editor={editor} />
      {editor && toolbar.visible && createPortal(
        <div
          className="rcc-rich-format"
          role="toolbar"
          aria-label="Форматирование выделенного текста"
          style={{ left: toolbar.left, top: toolbar.top }}
          onPointerDown={event => event.preventDefault()}
        >
          <button type="button" aria-label="Жирный" aria-pressed={toolbar.bold} title="Жирный · ⌘B" onClick={() => editor.chain().focus().toggleBold().run()}><Bold size={16} /></button>
          <button type="button" aria-label="Курсив" aria-pressed={toolbar.italic} title="Курсив · ⌘I" onClick={() => editor.chain().focus().toggleItalic().run()}><Italic size={16} /></button>
          <button type="button" aria-label="Список" aria-pressed={toolbar.bullet} title="Список" onClick={() => editor.chain().focus().toggleBulletList().run()}><List size={17} /></button>
          <button type="button" aria-label="Нумерованный список" aria-pressed={toolbar.ordered} title="Нумерованный список" onClick={() => editor.chain().focus().toggleOrderedList().run()}><ListOrdered size={17} /></button>
        </div>,
        document.fullscreenElement || document.body,
      )}
    </div>
  )
}
