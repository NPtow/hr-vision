# Локальный холст «Разбор»

React Flow (`@xyflow/react` 12.11.1, MIT) внутри WKWebView. React 19.2.0. Npm lock фиксирует зависимости. Сборка — обычный IIFE, HTML/CSS/JS из app bundle; CDN, шрифтов, запросов к сети, web storage и React Flow Pro нет. Notices зависимостей входят в bundle.

Версия приложения 0.11.0: светлый холст, плавающие инструменты, карточки 248×220, редактор по запросу. Геометрия handles задана вместе с фиксированным размером карточек, чтобы соединения были доступны сразу при загрузке WKWebView. `canvas-layout.ts` отвечает за свободное место для новой карточки и явную отменяемую раскладку. Контракт хранилища прежний.

Реализованы шесть типов карточек: источник, наблюдение, гипотеза, оффер, контакт, заметка; четыре типа направленных связей; сохранённые источники/контакты из хоста; редактор; перемещение и множественный выбор; удаление; native undo/redo; линейный список и клавиатурная форма связей. Исходный материал показывается через `referenceId` и каталог хоста, без независимой копии текста в документе. В runtime нет начальных карточек, вымышленных фактов или готовых офферов. Новая аналитическая карточка имеет пустой текст и статус «Требует проверки».

## Сборка и проверки

```sh
cd board-web
npm ci --ignore-scripts
npm test
npm run build
```

Выход: `../Sources/SignalsApp/Resources/Board/` — `index.html`, `board.js`, `board.css`, `THIRD-PARTY-NOTICES.txt`, `manifest.json` с SHA256. Сборка проверяет TypeScript и поддерживает Safari 17 / macOS 14.

13 unit tests проверяют контракт, текущую сессию, fail-closed для неизвестных типов/висячих ссылок, отсутствие подставного содержания, ссылки на архив, типы связей, семантическое удаление, совпадение ACK, сохранность конфликтного черновика и одинаковый payload при повторе. Эти проверки не заменяют WKWebView/VoiceOver/IME интеграционную проверку.

## Встраивание Swift

1. Добавить `.copy("Resources/Board")` в resources executable target `SignalsApp`.
2. Создать `WKWebView` один раз на документ. Зарегистрировать `WKScriptMessageHandler` с именем `board` **до** загрузки HTML.
3. Загружать `Board/index.html` через `loadFileURL`, `allowingReadAccessTo` ограничить каталогом Board.
4. Получить `ready`, сохранить его `sessionId` и передать state. Все host сообщения обязаны содержать тот же `sessionId` и `protocolVersion: 1`.
5. Вызывать фиксированное `window.boardHost.receive(payload)` через `callAsyncJavaScript(arguments:)`. Не интерполировать текст данных в JS-код.
6. Проверять main frame, session/document ID, команду, актуальность ревизии/полей и immutable reference IDs. Canonical storage/валидация/история принадлежат Swift; web не подтверждает долговечность самостоятельно.
7. После долговечной записи отправлять state с `ackTransactionId`. Ошибка — `error` с исходным `transactionId`, затем свежий state. Повтор **того же payload и ID** возвращает receipt без нового undo-шагa. Идемпотентность требуется и для undo/redo.
8. После `webViewWebContentProcessDidTerminate` перезагрузить assets и дождаться нового ready. Ответы старого sessionId отбрасываются. Восстанавливать канонический документ, а не web storage.

## Контракт v1

Полные TS типы и runtime-валидация: `src/protocol.ts`. Session/транспорт: `src/bridge.ts`.

Host → web:

- `state`: `{protocolVersion:1,sessionId,type:"state",document,sources,contacts,canUndo,canRedo,ackTransactionId?}`.
- `error`: `{protocolVersion:1,sessionId,type:"error",message,transactionId?}`.
- `setSelection`: `{protocolVersion:1,sessionId,type:"setSelection",documentId?,nodeIds,edgeIds?,reveal?}`. Обычный state не перехватывает viewport/выбор; `reveal:true` — явное действие.

Документ: `{id,companyId,title,revision,nodes,edges}`. Node: `{id,kind,title,body,status,referenceId?,position:{x,y}}`. Для source/contact `referenceId` обязателен. Edge: `{id,source,target,kind}`. Reference catalog item: `{id,title,body?,subtitle?,url?}`. Недоступная ссылка показывается явно. Поля с отсутствующими значениями пропускаются, не передаются как `null`.

Node kinds: `source | observation | hypothesis | offer | contact | note`.
Node statuses: `proposed | accepted | rejected | requiresVerification`.
Edge kinds: `supports | contradicts | requiresVerification | contactRationale`.

Web → `window.webkit.messageHandlers.board.postMessage(object)`:

Каждое сообщение содержит `{protocolVersion:1,sessionId,type}`.

- `ready` — запрос документа/повторный handshake.
- `transaction`: `{transactionId,documentId,baseRevision,actor:"human",operations}`.
- `undo` / `redo`: `{transactionId,documentId,baseRevision}`.
- `selectionChanged`: `{documentId,nodeIds,edgeIds}`.
- `editingChanged`: `{documentId,nodeIds,fields,editing}`. `fields` содержит `title | body | status | position`. Активный ввод продлевается каждые 10 секунд; native должен также продлевать lease для длительного drag и очищать её при завершении/смене документа.
- `openReference`: `{documentId,kind:"source"|"contact",referenceId}`. Открывает объект через native; web не переходит на URL источника.

Operations:

```ts
{ type: 'createNode', node }
{ type: 'setNodeFields', nodeId, fields: { title?, body?, status? } }
{ type: 'moveNodes', positions: [{ nodeId, x, y }] }
{ type: 'connect', edge }
{ type: 'removeEdge', edgeId }
{ type: 'deleteNode', nodeId }
```

Native `deleteNode` удаляет и incident edges в одной транзакции. Ссылки/тип/ID не меняются через fields. Пользовательский drag остаётся локальным до завершения. Клавиатурное движение отправляет `moveNodes`. У web нет отдельного document undo. В input/textarea/select/contenteditable и во время IME клавиши undo/delete остаются редактору текста. Ctrl/⌘Z вне полей вызывает native историю.

В полёте одна мутация. После 6 секунд без receipt пользователь может повторить **исходную** команду с тем же ID. Ошибка откатывает визуальную позицию к последнему подтверждённому документу. Текстовые черновики хранятся в памяти текущего renderer по document/node ID, переживают смену выбранной карточки и конфликты. Они не являются долговечно сохранёнными: до нажатия «Сохранить» app/process restart их удалит, что явно отмечено в редакторе.

Неизвестный формат/тип/версия блокирует дальнейшие изменения и сохраняет последнее подтверждённое отображение; не превращает ошибку в пустой документ. Тексты выводятся React как plain text, без HTML/Markdown-инъекции. CSP отключает сеть/frames/object, внешние страницы открывает хост.

## Источники API

Context7 использован в порядке `search "react flow"` → `docs /websites/reactflow_dev "TypeScript custom nodes NodeProps handles controlled nodes applyNodeChanges accessibility"`. Сниппеты сверены с официальными страницами и типами установленного пакета:

- [React Flow TypeScript](https://reactflow.dev/learn/advanced-use/typescript)
- [React Flow accessibility](https://reactflow.dev/learn/advanced-use/accessibility)
- [React Flow component](https://reactflow.dev/api-reference/react-flow)
- [xyflow MIT license](https://github.com/xyflow/xyflow/blob/main/LICENSE)

Готовые Pro undo/collaboration примеры не использовались.
