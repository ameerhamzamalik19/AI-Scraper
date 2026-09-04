# AI Scraper Frontend Agent Guide

This document is the working brief for an AI coding agent operating in this repository. Read it before changing code. The goal is to make a useful, verified change with the smallest reasonable scope while preserving the existing frontend/backend contract.

## Mission

Maintain and improve the AI Scraper frontend: a React chat interface that accepts links or natural-language questions, sends them to a local backend, displays conversation history, and receives assistant messages over HTTP and WebSocket transport.

The repository currently contains the frontend only. Do not assume that backend source, database models, migrations, environment files, or API implementation are available here. When backend behavior is unclear, inspect the frontend call sites first, then ask for or document the backend contract instead of inventing a breaking one.

## Repository Location

- App root: `ai-scraper/`
- On Windows, the expected working directory is the directory containing this file.
- The package manifest is `ai-scraper/package.json`.
- The terminal may open in the parent `frontend/` directory. Run commands after changing into `ai-scraper`.
- Do not edit generated `dist/` output or dependencies in `node_modules/`.

## Technology

- React `19.2.8`
- Vite `8.2.0`
- JavaScript with JSX, not TypeScript
- Axios for HTTP requests
- Native browser `WebSocket` for live messages
- ESLint 10 with the recommended JavaScript, React Hooks, and React Refresh rules
- Docker uses a two-stage Node 22 Alpine build and Nginx static hosting

There is no test runner configured in `package.json` at present. Do not claim that tests passed when only lint or build was run. If a change adds meaningful behavior and no suitable test infrastructure exists, validate it with lint/build and, when possible, a manual browser check.

## Important Files

| File | Responsibility |
| --- | --- |
| `src/App.jsx` | Main application component; chat state, API calls, WebSocket lifecycle, rendering, and user actions |
| `src/App.css` | Component layout and visual styles for the sidebar, chat, messages, loading state, and input |
| `src/index.css` | Global reset/root sizing and base font declaration |
| `src/main.jsx` | React DOM entry point |
| `package.json` | Dependencies and executable scripts |
| `vite.config.js` | Vite React plugin and development port `5173` |
| `eslint.config.js` | ESLint configuration and ignored paths |
| `Dockerfile` | Production build and Nginx image |
| `docker-compose.yml` | Frontend container exposed on host port `80` |
| `index.html` | HTML shell and document title |
| `public/` | Static public assets such as the favicon |

## Current Runtime Contract

`src/App.jsx` currently uses `API_URL = 'http://localhost:8000'`. Treat this as an explicit development assumption. A production or deployment change should introduce a deliberate configuration strategy, such as a Vite environment variable, and update all affected documentation and deployment behavior together.

### HTTP endpoints

- `GET /api/chats`
  - Expected to return an array of chat summaries.
  - The UI currently reads `chat.id` and `chat.title`.
- `GET /api/chats/{chatId}`
  - Expected to return an object containing `messages`.
  - Each message is expected to include at least `role`, `content`, and usually `timestamp`.
- `POST /api/process-link`
  - JSON body: `{ content: string, chat_id: string | null }`.
  - The UI expects a response containing `message` and, after the first request, `chat_id`.
  - Optional current fields logged by the UI include `user_id` and `detection`.
- `DELETE /api/chats/{chatId}`
  - Expected to delete the selected conversation.

### WebSocket endpoint

- `ws://localhost:8000/ws/{chatId}` when the current chat id exists.
- On open, the client sends the text message `join`.
- Incoming JSON messages are consumed when `payload.type === 'message'` and `payload.message` exists.
- The first assistant response for a newly created chat is taken from the HTTP response because there is no WebSocket room before the backend returns the new id.

Do not silently change field names, URL paths, or the first-message behavior. If the backend contract changes, update the client and this document together, and cover duplicate-message behavior for both HTTP and WebSocket paths.

## State and Interaction Rules

The current component owns these concerns:

- `inputValue`: controlled input contents
- `messages`: visible messages for the active conversation
- `chatHistory`: sidebar chat summaries
- `currentChatId`: backend conversation UUID, or `null` for a new conversation
- `isLoading`: disables submission while the request is in flight
- `isSidebarOpen`: sidebar visibility
- `error`: visible error state
- refs for scroll position, input focus, and WebSocket connection state

Preserve these behavioral invariants unless the task explicitly changes them:

1. Empty or whitespace-only submissions do nothing.
2. The user message appears immediately before the network request completes.
3. A new chat sends `chat_id: null`; the returned backend id becomes the active chat id.
4. Existing chats stream assistant messages through their WebSocket room.
5. A newly created chat does not duplicate the first assistant response when the WebSocket connects after the HTTP response.
6. Creating a new chat clears messages, id, input, and error, then focuses the input.
7. Deleting the active chat clears the active conversation.
8. Loading chat history, loading a chat, submitting input, and deleting a chat expose a useful error to the user.
9. Message rendering must tolerate empty content and preserve line breaks.
10. The message list remains scrollable and scrolls toward the newest message.

When changing async code, consider stale requests, WebSocket cleanup on chat changes, reconnect behavior, duplicate assistant messages, and state updates after a component unmount. Avoid adding a second transport or a second source of truth without first explaining the ownership boundary.

## Coding Conventions

- Follow the existing JavaScript/JSX style and keep edits focused.
- Use React state and effects consistently with the current component unless a refactor is required to fix a concrete problem.
- Keep backend communication in a clear, inspectable place. If extracting hooks or modules, preserve the public behavior and make the new ownership obvious.
- Use descriptive variable and function names; do not introduce one-letter names.
- Keep JSX accessible: use semantic elements, labels or meaningful placeholders, keyboard support, focus states, and `aria-*` attributes where needed.
- Every interactive control must expose an understandable disabled, hover, and focus state where applicable.
- Avoid putting important user-facing behavior only in `console.log`; logs are for development diagnostics, not error presentation.
- Do not introduce a dependency for a small utility that the platform or existing code handles well.
- Keep comments short and explain only non-obvious decisions. Do not add narration comments for straightforward code.
- Preserve user changes already present in the worktree. Never reset or overwrite unrelated edits.
- Avoid drive-by formatting, broad rewrites, or generated-file changes.

## UI and CSS Guidance

- Preserve the current chat/sidebar mental model unless the task requests a redesign.
- Keep the layout usable on narrow screens as well as desktop. Check overflow, long URLs, long chat titles, message wrapping, and the input area.
- Maintain stable dimensions for controls so loading states do not shift the layout.
- Ensure color contrast, visible keyboard focus, and readable error states.
- Prefer reusable class names and existing style organization over inline styling. If a one-off dynamic value genuinely needs inline style, keep it local and explain the reason in the change summary.
- Avoid changing the visual language merely to make an unrelated functional fix.
- Do not add decorative UI that competes with the chat workflow.

## Commands

Run these from `ai-scraper/`:

```powershell
npm install                 # Only when dependencies are missing or package-lock changed
npm run dev                 # Start Vite at http://localhost:5173
npm run lint                # ESLint source and config files
npm run build               # Production Vite build into dist/
npm run preview             # Serve the production build locally
docker compose up --build   # Build and serve the frontend on http://localhost
```

Recommended validation order:

1. Run the narrowest relevant check after the first edit. For JSX/logic changes, start with `npm run lint`.
2. Run `npm run build` for changes affecting imports, Vite configuration, deployment, or production bundling.
3. For API/WebSocket changes, manually exercise: initial load, new chat, first response, existing chat load, sidebar selection, deletion, backend unavailable, and a long message.
4. Report exactly which checks ran and which could not be run.

The frontend expects a backend at `http://localhost:8000`. A successful frontend build does not prove the backend contract or network behavior works.

## Agent Workflow

Before editing:

1. Read this file and inspect the relevant implementation and nearby call sites.
2. Check `git status` and preserve unrelated user work.
3. State a short hypothesis about the controlling code path and one check that could disprove it.
4. Search for existing patterns before introducing a new abstraction or dependency.

While editing:

1. Make the smallest coherent change that addresses the request.
2. Keep API and WebSocket changes explicit and synchronized.
3. Add or update documentation when an endpoint, command, environment assumption, or user workflow changes.
4. Do not commit changes unless explicitly requested.

After editing:

1. Run a focused executable validation before more exploration or unrelated cleanup.
2. Fix relevant lint/build failures in the touched slice and rerun the same check.
3. Inspect the final diff for accidental changes, debug output, broken imports, and contract drift.
4. Summarize changed files, behavior, validation commands, and any remaining backend/manual verification needs.

## Known Limitations and Risks

- The API base URL is hard-coded to localhost and is not currently environment-configurable.
- There is no automated test suite or test script configured.
- The UI currently assumes a fixed user id in informational text (`User ID: 1`); do not treat that display text as proof of authentication or authorization.
- WebSocket payload parsing assumes valid JSON and the expected shape. Defensive handling should be added deliberately if malformed or versioned payloads are a real backend possibility.
- Chat messages use array indexes as React keys. Changing this requires a stable message identity strategy and care around streamed/duplicate messages.
- The current UI uses inline SVG and emoji for icons/status indicators. Preserve the existing approach unless the task specifically concerns iconography or accessibility.

## Definition of Done

A frontend task is complete when the requested behavior is implemented, existing chat flows remain intact, the touched code passes the relevant lint/build checks, and the final report names any backend-dependent behavior that was not executable locally. Keep the diff understandable enough that another agent can continue from it without reconstructing the entire project history.