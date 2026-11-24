# Odoo Desktop

A Windows app to work on your Odoo tasks without opening the browser: see what needs attention, read and move tasks, reply, log time and plan your day.

## Open the app

Double-click **`OdooDesktop.exe`** in this folder. Nothing else to install.

Sign in with:

| Field | What to enter |
|---|---|
| Server | e.g. `your-company.odoo.com` (`https://` is added for you) |
| Database | Your Odoo database name. Optional only if the server lists exactly one database; many servers do not list them, so enter it. |
| Email | your Odoo login |
| Password or API key | Your normal Odoo password, or an API key (Odoo → avatar → My Profile / Preferences → Account Security → New API Key). Accounts with two-step verification must use an API key: Odoo refuses the password for app connections. |

Server, database, email and preferences are remembered in `%APPDATA%\OdooDesktop\settings.json`.

**Stay signed in** keeps your password or API key in Windows Credential Manager (never in a file) and signs you in automatically next time. Signing out, or unticking it in Settings, removes it.

## Pages

The app loads every task assigned to you, across all projects, and refreshes every 5 minutes.

- **Overview** — counters, a "Today in Odoo" row (activities to do, unread messages, meetings, hours logged) and the tasks that need attention now. **New task** creates a task assigned to you in a project you choose.
- **Today** — your plan for the day. Add tasks from the suggestions, from Focus or from a task page, tick them off, and what is left is carried over to the next day. Stored on this computer only.
- **Focus** — one list for all projects, grouped by urgency. Search, filter by project, pinned, untouched or done; export to CSV. Select several tasks with Ctrl or Shift and click **Move to stage** to move them all at once; with tasks of different projects, only the stages those projects have in common are offered.
- **Calendar** — deadlines and your Odoo meetings (in purple, with their time) on a month grid. Click a task to read it, a meeting to open it in Odoo, or "+n more" to list that day in Focus.
- **Activities** — everything Odoo scheduled for you on any record (calls, emails, to-dos), most urgent first. **Mark done** closes one in Odoo, with an optional note.
- **Mentions** — messages that tagged you. Tick each one off yourself when you have dealt with it (click its status, or select several and click Mark checked); the list also shows if you already replied. Stored on this computer only.
- **Discuss** — your channels and direct messages with their unread count. Pick one to read it and answer; opening it marks it as read in Odoo. A Windows notification tells you when a direct or group message arrives.
- **What changed** — what happened to your tasks since the last time you started the app. **Mark as seen** empties the list; changes are then counted from that moment.
- **Project health** — one bar per project, with a Late / At risk / On track label, based on your own tasks.
- **Team** — everyone's open tasks in one project, grouped by person, most overdue first.
- **Timesheets** — the hours you logged, per project and task, day by day, for any week.
- **Weekly summary** — finished, moved, new, rescheduled and slipped tasks for the current week. Copy or save it as text.
- **More from Odoo** — one list, seven views: tasks you follow or created, your tasks waiting on others, tasks waiting on you, milestones and project status, time off (balances, your leave, who is away today), helpdesk tickets, CRM pipeline. Buttons appear for what each view allows: request, approve or refuse time off, move a ticket to another stage, plan an activity on a ticket or an opportunity.
- **Settings** — notifications, tray, stay signed in, updates, theme, data folder.

### The task page

Double-click a task anywhere to read it in full: description, checklists, sub-tasks, attachments, messages and properties. From there you can:

- **Move to stage** — pick a stage of the project and click Move.
- **Reply** — write at the bottom and click Send; it is posted as a comment and Odoo notifies the followers.
- **Timer** — Start, then Stop to log the time as a timesheet line (hours and description can be edited first). The timer survives closing the app.
- **Today / Pin / private note** — local helpers, never sent to Odoo.
- **Attachments** — click a file to download and open it. Small PNG and GIF pictures are shown in the page. Programs and scripts are never started: they are only shown in Explorer.

Every action that writes to Odoo asks for confirmation first.

### Editing a task

On the task page, **Edit** changes the deadline, priority, assignees and tags, and **Checklist** ticks the checklist items of the description. The checklist is read again from Odoo right before it is saved; if someone changed the description meanwhile, nothing is written.

### Search and shortcuts

| Keys | Action |
|---|---|
| Ctrl+K | Search tasks, activities, conversations, mentions and pages. With three letters or more it also finds open tasks of other people in Odoo. |
| Ctrl+N | New task |
| Ctrl+1 … Ctrl+9 | Go to the first nine pages of the menu |
| F5 | Refresh |
| Esc | Leave the task page |
| Ctrl+Alt+N | From anywhere in Windows: bring the app up with the New task box (can be turned off in Settings) |

### AI assistant

Optional. In Settings, choose **Claude**, **Gemini** or **GPT**, paste your own API key from that provider and pick a model. The key is kept in Windows Credential Manager. Then **Ask AI** buttons can:

- summarise a task or a conversation, draft a reply into the reply box, or translate to English, French or Indonesian;
- propose a plan for the day (Today page) and write the weekly report (Weekly summary page);
- answer a question about your open tasks (Overview page).

Text is sent to the provider only when you click one of these actions, and the app asks first, once each time it starts. The AI never writes to Odoo: a draft only lands in the reply box, and you send it yourself. API use is billed by the provider, separately from any chat subscription.

### Offline

The tasks of the last successful refresh are kept on this computer. They are shown at once when the app starts, and when Odoo cannot be reached the app says so and keeps showing them. With Stay signed in, the app also opens on them when there is no network at start. Changes need the connection back.

### Notifications and tray

**Start with Windows** (Settings) launches the app hidden next to the clock when you sign in to Windows. With the options in Settings turned on, closing the window keeps the app next to the clock (right-click the icon to quit) and Windows shows a notification when a task is assigned to you, a deadline is today, or someone replies on one of your tasks.

### Updates

Once a day the app asks GitHub whether a newer release exists and shows "Version x.y is available" at the bottom of the menu; click it and confirm: the app downloads the new `OdooDesktop.exe` from this repository's releases, replaces itself and restarts. Your settings and local data are kept. Nothing is downloaded without that click, and the check sends nothing about you or your tasks. It can be turned off in Settings.

### Good to know

- **Done tasks** — a task counts as done when Odoo marks it closed, when its stage is folded in the kanban, or when the stage name contains a word like Done or Cancelled. Done tasks are hidden, never overdue and not counted; tick Show done in Focus to see them.
- **Untouched** — days since anything changed on the task in Odoo; 7 or more counts as untouched.
- **Local data** — pins, notes, plans, timer and history are stored per account in `%APPDATA%\OdooDesktop\data-*.json`.
- **What Odoo needs** — replies and stage moves need write access to the task; logging time needs the Timesheets app. Timesheets, Discuss, Time off, Helpdesk and CRM only show when that Odoo app is installed; a page you have no access to says so.

## For developers

Needs Python 3.10+ with Tk on Windows. The only third-party package is `anthropic`, used for the Claude connection (`pip install anthropic`); everything else runs without it.

```powershell
python main.py                                # run from source
python -m unittest discover -s tests -v       # tests
.\build.ps1                                   # tests + rebuild OdooDesktop.exe (needs pyinstaller)
```

| Path | Content |
|---|---|
| `odoo_login/odoo_client.py` | JSON-RPC calls: sign-in, reading tasks, mentions, team, attachments; writing stage, message, timesheet, new task. Sends `User-Agent: OdooDesktop/1.0`: Cloudflare blocks Python's default one. |
| `odoo_login/odoo_extra.py` | The client the app uses: the core client plus activities, timesheets, Discuss, meetings, related tasks, milestones, time off, helpdesk, CRM |
| `odoo_login/app.py` | Sign-in screen, stay signed in, tray and window lifecycle |
| `odoo_login/dashboard.py` | Dashboard shell: sidebar, Overview, Focus, What changed, Project health, task page |
| `odoo_login/pages.py` | Today, Calendar, Mentions, Team, Weekly summary, Settings, notifications |
| `odoo_login/power.py` | Overview's Odoo row, search and shortcuts, task editing, actions of More from Odoo, offline mode, in-app update |
| `odoo_login/ai.py`, `ai_other.py`, `ai_pages.py` | AI assistant: Claude through the Anthropic SDK, Gemini and GPT over HTTPS, and its screens |
| `odoo_login/odoo_pages.py` | Activities, Timesheets, Discuss, More from Odoo, meetings on the Calendar |
| `odoo_login/actions.py` | Move stage, reply, timer, new task, attachments, form dialogs |
| `odoo_login/insights.py` | Calculations: urgency groups, untouched days, changes, project health, week summary, calendar |
| `odoo_login/richtext.py` | Turns Odoo's HTML (descriptions, messages) into readable text |
| `odoo_login/store.py` | Local data: pins, notes, snapshot, plan, history, timer |
| `odoo_login/updates.py` | App version and the check for a newer GitHub release |
| `odoo_login/winshell.py` | Windows Credential Manager, tray icon and notifications (ctypes, no dependency) |
| `odoo_login/theme.py` | Dark and light palettes, fonts, styles, rounded card and button widgets |
| `odoo_login/settings.py` | Remembered server / database / email / theme / preferences |
| `assets/` | App icon and the Plus Jakarta Sans font (SIL Open Font License, see `assets/fonts/OFL.txt`) |
| `OdooDesktop.spec`, `build.ps1` | Build of the exe |
